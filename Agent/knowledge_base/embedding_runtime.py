"""RAG embedding 配置、显式资源和 API 故障边界。"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, Mapping
from urllib.parse import urlsplit

from observability.logging_runtime import log_event

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_LOCAL_EMBEDDING_MODEL_PATH = BASE_DIR / "models" / "bge-small-zh-v1.5"

PROVIDER_ENV = "RAG_EMBEDDING_PROVIDER"
LOCAL_MODEL_PATH_ENV = "RAG_LOCAL_EMBEDDING_MODEL_PATH"
API_KEY_ENV = "EMBEDDING_API_KEY"
API_BASE_URL_ENV = "EMBEDDING_BASE_URL"
API_MODEL_ENV = "EMBEDDING_MODEL"
DEFAULT_API_MODEL = "text-embedding-3-small"
EMBEDDING_REQUEST_CONTRACT_VERSION = "embedding-v1"
ALLOWED_API_KEY_ENV_NAMES = frozenset({
    "EMBEDDING_API_KEY",
    "RAG_EMBEDDING_API_KEY",
    "RAG_EVAL_EMBEDDING_API_KEY",
})
ALLOWED_BASE_URL_ENV_NAMES = frozenset({
    "EMBEDDING_BASE_URL",
    "RAG_EMBEDDING_BASE_URL",
    "RAG_EVAL_EMBEDDING_BASE_URL",
})
ALLOWED_MODEL_ENV_NAMES = frozenset({
    "EMBEDDING_MODEL",
    "RAG_EMBEDDING_MODEL",
    "RAG_EVAL_EMBEDDING_MODEL",
})

_API_PROVIDER_VALUES = {"api", "openai", "openai_compatible"}
_LOCAL_PROVIDER_VALUES = {"local", "huggingface"}

LOGGER = logging.getLogger(__name__)


def _endpoint_identity(value: Any) -> str:
    """把 endpoint 规整为不含凭据、查询参数或片段的身份。"""
    if not value:
        return ""
    raw = str(value).strip()
    parsed = urlsplit(raw)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("embedding endpoint identity must not contain credentials or query data")
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
    if any(marker in raw for marker in ("@", "?", "#")):
        raise ValueError("embedding endpoint identity is unsafe")
    return raw.rstrip("/")


@dataclass(frozen=True)
class EmbeddingConfiguration:
    """可传递、不可变且不含密钥值的 embedding 配置快照。"""

    mode: str
    provider: str
    model: str
    model_revision: str = ""
    endpoint_identity: str = ""
    api_key_env: str = ""
    base_url_env: str = ""
    model_env: str = ""
    dimension: int | None = None
    normalized: bool = True
    normalization: str = "l2"
    distance_metric: str = "cosine"
    query_transform: str = "identity"
    document_transform: str = "identity"
    request_contract_version: str = EMBEDDING_REQUEST_CONTRACT_VERSION
    path: str = ""
    status: str = "ready"
    missing: tuple[str, ...] = ()
    message: str = ""

    def __post_init__(self) -> None:
        """拒绝会把凭据或不确定配置带入 release 身份的字段。"""
        if self.mode not in {"local", "api"}:
            raise ValueError("embedding mode must be local or api")
        if not self.provider or not self.model:
            raise ValueError("embedding provider and model are required")
        if self.mode == "local" and any(separator in self.model for separator in ("/", "\\")):
            raise ValueError("local embedding model must be a model identity, not a path")
        if self.dimension is not None and (isinstance(self.dimension, bool) or self.dimension < 1):
            raise ValueError("embedding dimension must be positive")
        for name in (self.api_key_env, self.base_url_env, self.model_env):
            if name and not name.replace("_", "").isalnum():
                raise ValueError("embedding environment variable names are invalid")
        object.__setattr__(self, "missing", tuple(self.missing))
        object.__setattr__(self, "endpoint_identity", _endpoint_identity(self.endpoint_identity))

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | "EmbeddingConfiguration",
        *,
        derive_status: bool = False,
    ) -> "EmbeddingConfiguration":
        """从现有 resolver、manifest 或调用方配置构造安全快照。"""
        if isinstance(value, cls):
            if not derive_status:
                return value
            value = value.to_runtime_dict()
        data = dict(value)
        mode = str(data.get("mode") or ("api" if data.get("provider") in _API_PROVIDER_VALUES else "local"))
        provider = str(data.get("provider") or ("openai_compatible" if mode == "api" else "huggingface"))
        model = str(data.get("model") or data.get("model_name") or "").strip()
        dimension = data.get("dimension")
        if dimension is not None:
            dimension = int(dimension)
        normalized = bool(data.get("normalized", data.get("normalization", "l2") == "l2"))
        api_key_env = str(data.get("api_key_env") or (API_KEY_ENV if mode == "api" else ""))
        base_url_env = str(data.get("base_url_env") or (API_BASE_URL_ENV if mode == "api" else ""))
        model_env = str(data.get("model_env") or (API_MODEL_ENV if mode == "api" else ""))
        endpoint_identity = str(data.get("endpoint_identity") or data.get("base_url") or "")
        if mode == "api" and not endpoint_identity:
            endpoint_identity = os.environ.get(base_url_env, "")
        path = str(data.get("path") or "")
        if mode == "local" and not path:
            path = str(DEFAULT_LOCAL_EMBEDDING_MODEL_PATH.with_name(model)) if model else ""
        missing = tuple(str(item) for item in (data.get("missing") or ()))
        status = str(data.get("status") or "ready")
        message = str(data.get("message") or "")
        if derive_status:
            if mode == "api":
                missing_items = [
                    name
                    for name, configured in (
                        (api_key_env, bool(os.environ.get(api_key_env))),
                        (base_url_env, bool(os.environ.get(base_url_env))),
                    )
                    if name and not configured
                ]
                expected_endpoint = str(data.get("endpoint_identity") or "")
                actual_endpoint = os.environ.get(base_url_env, "")
                if expected_endpoint and actual_endpoint:
                    try:
                        if _endpoint_identity(actual_endpoint) != expected_endpoint:
                            missing_items.append(base_url_env)
                            message = "embedding endpoint identity 与 release manifest 不一致"
                        else:
                            message = "配置完整"
                    except ValueError:
                        missing_items.append(base_url_env)
                        message = "embedding endpoint identity 不安全"
                else:
                    message = "配置完整" if not missing_items else "缺少 " + ", ".join(missing_items)
                missing = tuple(dict.fromkeys(missing_items))
                status = "ready" if not missing else "missing"
            else:
                status = "ready" if path and Path(path).exists() else "missing"
                missing = () if status == "ready" else (path or "local_model_path",)
                message = "本地模型目录存在" if status == "ready" else "本地 embedding 模型目录不存在"
        return cls(
            mode=mode,
            provider=provider,
            model=model,
            model_revision=str(data.get("model_revision") or data.get("revision") or ""),
            endpoint_identity=endpoint_identity,
            api_key_env=api_key_env,
            base_url_env=base_url_env,
            model_env=model_env,
            dimension=dimension,
            normalized=normalized,
            normalization=str(data.get("normalization") or ("l2" if normalized else "none")),
            distance_metric=str(data.get("distance_metric") or "cosine"),
            query_transform=str(data.get("query_transform") or "identity"),
            document_transform=str(data.get("document_transform") or "identity"),
            request_contract_version=str(data.get("request_contract_version") or EMBEDDING_REQUEST_CONTRACT_VERSION),
            path=path,
            status=status,
            missing=missing,
            message=message,
        )

    @classmethod
    def from_manifest(cls, value: Mapping[str, Any]) -> "EmbeddingConfiguration":
        """从 manifest 恢复配置，并只在本机重新检查凭据/本地模型可用性。"""
        config = cls.from_mapping(value, derive_status=True)
        return validate_embedding_env_references(config)

    def fingerprint(self) -> dict[str, Any]:
        """返回兼容旧 pointer 的短指纹；完整身份由 ``to_manifest`` 提供。"""
        return {
            "provider": self.provider,
            "model": self.model,
            "mode": self.mode,
            "dimension": self.dimension,
            "normalized": self.normalized,
        }

    def to_manifest(self) -> dict[str, Any]:
        """返回可进入 manifest 的完整安全配置，不包含路径、密钥值或 URL。"""
        return {
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "model_revision": self.model_revision,
            "endpoint_identity": self.endpoint_identity,
            "api_key_env": self.api_key_env,
            "base_url_env": self.base_url_env,
            "model_env": self.model_env,
            "dimension": self.dimension,
            "normalized": self.normalized,
            "normalization": self.normalization,
            "distance_metric": self.distance_metric,
            "query_transform": self.query_transform,
            "document_transform": self.document_transform,
            "request_contract_version": self.request_contract_version,
        }

    def to_runtime_dict(self) -> dict[str, Any]:
        """返回创建 embedding 所需的脱敏运行时映射。"""
        return {
            **self.to_manifest(),
            "status": self.status,
            "path": self.path,
            "path_exists": bool(self.path and Path(self.path).exists()),
            "missing": list(self.missing),
            "message": self.message,
        }


def resolve_embedding_configuration(
    value: Mapping[str, Any] | EmbeddingConfiguration | None = None,
) -> EmbeddingConfiguration:
    """把显式配置或兼容环境 resolver 统一成不可变对象。"""
    if value is not None:
        return EmbeddingConfiguration.from_mapping(value)
    return EmbeddingConfiguration.from_mapping(resolve_embedding_runtime_config())


def embedding_identity(
    value: Mapping[str, Any] | EmbeddingConfiguration,
) -> dict[str, Any]:
    """返回参与 release identity 的完整 embedding 配置。"""
    return EmbeddingConfiguration.from_mapping(value).to_manifest()


def resolve_production_embedding_configuration() -> EmbeddingConfiguration:
    """惰性读取冻结正式 embedding，避免模块导入环。"""
    from Agent.knowledge_base.multimodal.defaults import resolve_production_embedding_config

    return EmbeddingConfiguration.from_mapping(resolve_production_embedding_config())


def embedding_configuration_from_manifest(manifest: Mapping[str, Any]) -> EmbeddingConfiguration:
    """从新 manifest 恢复完整配置，旧 manifest 退回其短指纹。"""
    value = manifest.get("embedding_config") or manifest.get("embedding")
    if not isinstance(value, Mapping):
        raise ValueError("manifest embedding configuration is missing")
    return EmbeddingConfiguration.from_manifest(value)


def validate_embedding_env_references(
    value: Mapping[str, Any] | EmbeddingConfiguration,
) -> EmbeddingConfiguration:
    """限制外部配置只能引用专用 embedding 环境变量。"""
    config = EmbeddingConfiguration.from_mapping(value, derive_status=True)
    if config.mode == "api":
        if config.api_key_env not in ALLOWED_API_KEY_ENV_NAMES:
            raise ValueError("embedding api_key_env is not an approved embedding credential reference")
        if config.base_url_env not in ALLOWED_BASE_URL_ENV_NAMES:
            raise ValueError("embedding base_url_env is not an approved embedding endpoint reference")
        if config.model_env and config.model_env not in ALLOWED_MODEL_ENV_NAMES:
            raise ValueError("embedding model_env is not an approved embedding reference")
    return config


def _status_code(error: BaseException) -> int | None:
    """从常见 OpenAI-compatible 异常中提取安全 HTTP 状态码。"""
    for candidate in (getattr(error, "status_code", None), getattr(getattr(error, "response", None), "status_code", None)):
        if isinstance(candidate, int) and not isinstance(candidate, bool) and 100 <= candidate <= 599:
            return candidate
    return None


def _error_text(error: BaseException) -> str:
    """只用于分类，不写入日志或持久产物。"""
    values = [str(error), type(error).__name__]
    body = getattr(error, "body", None)
    if body is not None:
        values.append(repr(body))
    return " ".join(values).lower()


def classify_embedding_api_error(error: BaseException) -> str:
    """把 embedding API 错误分类为 release 无关的稳定 reason code。"""
    status = _status_code(error)
    text = _error_text(error)
    if status == 402 or any(
        marker in text
        for marker in (
            "insufficient_quota",
            "billing",
            "quota_exceeded",
            "payment required",
            "free quota exhausted",
            "allocationquota.freetieronly",
        )
    ):
        return "quota_billing"
    if status == 401:
        return "auth_failed"
    if status == 403:
        return "auth_failed"
    if status == 429:
        return "rate_limited"
    if status is not None and 500 <= status <= 599:
        return "server_error"
    if isinstance(error, (TimeoutError,)) or "timeout" in text:
        return "timeout"
    if isinstance(error, (ConnectionError,)) or "connection" in text or "connecterror" in text:
        return "connection"
    return "unexpected_error"


_API_REASON_CODES = {
    "quota_billing",
    "auth_failed",
    "rate_limited",
    "server_error",
    "timeout",
    "connection",
    "unexpected_error",
    "unavailable",
}


class EmbeddingApiError(RuntimeError):
    """embedding API 失败；错误正文永远不作为稳定错误信息暴露。"""

    def __init__(self, category: str, *, status_code: int | None = None) -> None:
        self.category = category if category in _API_REASON_CODES else "unexpected_error"
        self.status_code = status_code
        super().__init__(f"embedding API request failed: {self.category}")


class EmbeddingCircuitOpenError(EmbeddingApiError):
    """API 熔断打开时拒绝新的计费请求。"""

    def __init__(self, category: str = "unavailable") -> None:
        super().__init__(category, status_code=None)
        self.category = category
        self.args = ("embedding API circuit is open",)


class EmbeddingApiCircuitBreaker:
    """带独立并发、速率和失败熔断状态的 embedding API 保护器。"""

    _shared_by_scope: dict[tuple[Any, ...], "EmbeddingApiCircuitBreaker"] = {}
    _shared_lock = threading.Lock()

    def __init__(
        self,
        *,
        scope: str = "production",
        max_concurrency: int = 4,
        rate_limit_per_second: float = 0.0,
        failure_threshold: int = 1,
        reset_after_seconds: float = 60.0,
    ) -> None:
        self.scope = scope
        self.max_concurrency = max(1, int(max_concurrency))
        self.rate_limit_per_second = max(0.0, float(rate_limit_per_second))
        self.failure_threshold = max(1, int(failure_threshold))
        self.reset_after_seconds = max(0.0, float(reset_after_seconds))
        self._semaphore = threading.BoundedSemaphore(self.max_concurrency)
        self._lock = threading.Lock()
        self._failure_count = 0
        self._opened_at = 0.0
        self._last_failure_category = "unavailable"
        self._last_request_at = 0.0

    @classmethod
    def from_environment(cls, scope: str = "production") -> "EmbeddingApiCircuitBreaker":
        """读取 worker 级 API 预算；evaluation 使用独立前缀。"""
        prefix = "RAG_EVAL_EMBEDDING" if scope == "evaluation" else "RAG_EMBEDDING"
        options = {
            "scope": scope,
            "max_concurrency": int(os.getenv(f"{prefix}_MAX_CONCURRENCY", "1" if scope == "evaluation" else "4")),
            "rate_limit_per_second": float(os.getenv(f"{prefix}_RATE_LIMIT_PER_SECOND", "0")),
            "failure_threshold": int(os.getenv(f"{prefix}_CIRCUIT_FAILURE_THRESHOLD", "1")),
            "reset_after_seconds": float(os.getenv(f"{prefix}_CIRCUIT_RESET_SECONDS", "60")),
        }
        key = tuple(options.items())
        with cls._shared_lock:
            existing = cls._shared_by_scope.get(key)
            if existing is not None:
                return existing
            breaker = cls(**options)
            cls._shared_by_scope[key] = breaker
            return breaker

    @property
    def is_open(self) -> bool:
        """报告当前是否拒绝新的 API 请求。"""
        with self._lock:
            return self._is_open_locked()

    def _is_open_locked(self) -> bool:
        if not self._opened_at:
            return False
        if self.reset_after_seconds and time.monotonic() - self._opened_at >= self.reset_after_seconds:
            self._opened_at = 0.0
            self._failure_count = 0
            return False
        return True

    def _wait_for_rate_limit(self) -> None:
        if self.rate_limit_per_second <= 0:
            return
        interval = 1.0 / self.rate_limit_per_second
        with self._lock:
            wait = max(0.0, self._last_request_at + interval - time.monotonic())
            self._last_request_at = time.monotonic() + wait
        if wait:
            time.sleep(wait)

    def call(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """执行一次 API 操作，分类失败并更新本 worker 的熔断状态。"""
        with self._lock:
            if self._is_open_locked():
                raise EmbeddingCircuitOpenError(self._last_failure_category)
        with self._semaphore:
            self._wait_for_rate_limit()
            try:
                result = operation(*args, **kwargs)
            except EmbeddingApiError:
                raise
            except Exception as exc:
                category = classify_embedding_api_error(exc)
                status_code = _status_code(exc)
                with self._lock:
                    self._failure_count += 1
                    should_open = category == "quota_billing" or self._failure_count >= self.failure_threshold
                    if should_open:
                        self._opened_at = time.monotonic()
                        self._last_failure_category = category
                    circuit_open = self._is_open_locked()
                _log_embedding_api_error(category, status_code, circuit_open)
                raise EmbeddingApiError(category, status_code=status_code) from exc
            else:
                with self._lock:
                    self._failure_count = 0
                return result


def _log_embedding_api_error(category: str, status_code: int | None, circuit_open: bool) -> None:
    """记录 RAG API 不可用，不记录 URL、密钥或响应正文。"""
    del status_code, circuit_open
    log_event(
        LOGGER,
        "rag.enrichment.degraded",
        details={
            "status": "unavailable",
            "reason_code": category if category in _API_REASON_CODES else "unexpected_error",
            "question_count": 0,
            "evidence_count": 0,
        },
    )


class CircuitBreakerEmbedding:
    """给 LangChain embedding 对象加 API 熔断而不改变其公开方法。"""

    def __init__(self, delegate: Any, breaker: EmbeddingApiCircuitBreaker) -> None:
        self._delegate = delegate
        self.breaker = breaker

    def embed_documents(self, texts: list[str]) -> Any:
        """受保护的文档向量请求。"""
        return self.breaker.call(self._delegate.embed_documents, texts)

    def embed_query(self, text: str) -> Any:
        """受保护的查询向量请求。"""
        return self.breaker.call(self._delegate.embed_query, text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def create_embedding_function(
    value: Mapping[str, Any] | EmbeddingConfiguration,
    *,
    scope: str = "production",
    breaker: EmbeddingApiCircuitBreaker | None = None,
) -> Any:
    """按显式配置创建 embedding；API 模式自动接入独立熔断。"""
    config = validate_embedding_env_references(value)
    if config.status != "ready":
        _log_embedding_api_error("unavailable", None, False)
        raise EmbeddingApiError("unavailable")
    if config.mode == "api":
        from langchain_openai import OpenAIEmbeddings

        if not config.api_key_env or not os.environ.get(config.api_key_env):
            raise ValueError(f"missing embedding API credential: {config.api_key_env}")
        if not config.base_url_env or not os.environ.get(config.base_url_env):
            raise ValueError(f"missing embedding API endpoint: {config.base_url_env}")
        delegate = OpenAIEmbeddings(
            api_key=os.environ[config.api_key_env],
            base_url=os.environ[config.base_url_env],
            model=config.model,
            tiktoken_enabled=False,
            check_embedding_ctx_length=False,
        )
        return CircuitBreakerEmbedding(delegate, breaker or EmbeddingApiCircuitBreaker.from_environment(scope))
    from langchain_huggingface import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(
        model_name=config.path,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": config.normalized},
    )


def resolve_embedding_runtime_config() -> Dict[str, Any]:
    """解析当前 embedding provider 配置，供构建、查询和 UI 状态共用。"""
    raw_provider = os.environ.get(PROVIDER_ENV, "auto").strip().lower() or "auto"
    provider = _resolve_provider(raw_provider)
    if provider == "api":
        return _api_embedding_config(raw_provider)
    return _local_embedding_config(raw_provider)


def _resolve_provider(raw_provider: str) -> str:
    """把环境变量 provider 解析成 api/local，auto 兼容旧行为。"""
    if raw_provider == "auto":
        if os.environ.get("KNOWLEDGE_BUILD_PROFILE") == "medical" or os.environ.get(API_KEY_ENV):
            return "api"
        return "local"
    if raw_provider in _API_PROVIDER_VALUES:
        return "api"
    if raw_provider in _LOCAL_PROVIDER_VALUES:
        return "local"
    raise ValueError(
        f"Unsupported {PROVIDER_ENV}={raw_provider!r}; expected auto, local, or openai_compatible."
    )


def _api_embedding_config(raw_provider: str) -> Dict[str, Any]:
    """返回 OpenAI-compatible embedding 配置摘要，不暴露密钥。"""
    api_key_configured = bool(os.environ.get(API_KEY_ENV))
    base_url = os.environ.get(API_BASE_URL_ENV, "")
    base_url_configured = bool(base_url)
    missing = []
    if not api_key_configured:
        missing.append(API_KEY_ENV)
    if not base_url_configured:
        missing.append(API_BASE_URL_ENV)
    return {
        "status": "ready" if not missing else "missing",
        "mode": "api",
        "provider": "openai_compatible",
        "provider_setting": raw_provider,
        "api_key_env": API_KEY_ENV,
        "base_url_env": API_BASE_URL_ENV,
        "model_env": API_MODEL_ENV,
        "api_key_configured": api_key_configured,
        "base_url_configured": base_url_configured,
        "base_url": base_url,
        "model": os.environ.get(API_MODEL_ENV, DEFAULT_API_MODEL),
        "missing": missing,
        "message": "配置完整" if not missing else "缺少 " + ", ".join(missing),
    }


def _local_embedding_config(raw_provider: str) -> Dict[str, Any]:
    """返回本地 HuggingFace embedding 配置摘要。"""
    model_path = Path(os.environ.get(LOCAL_MODEL_PATH_ENV, str(DEFAULT_LOCAL_EMBEDDING_MODEL_PATH)))
    path_exists = model_path.exists()
    return {
        "status": "ready" if path_exists else "missing",
        "mode": "local",
        "provider": "huggingface",
        "provider_setting": raw_provider,
        "model": model_path.name,
        "path": str(model_path.resolve()),
        "path_exists": path_exists,
        "missing": [] if path_exists else [LOCAL_MODEL_PATH_ENV],
        "message": "本地模型目录存在" if path_exists else "本地 embedding 模型目录不存在",
    }
