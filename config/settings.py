import os
import logging
import sys
from pathlib import Path

from config.checkpoint_settings import CheckpointPostgresConfig
from config.database_settings import DatabaseConfig, database_settings

# 计算项目根目录
# __file__ -> D:/.../CausalAgent/config/settings.py
# os.path.dirname(__file__) -> D:/.../CausalAgent/config
# os.path.dirname(os.path.dirname(__file__)) -> D:/.../CausalAgent (项目根目录)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 加载 .env 文件（如果存在）
# 这个库会自动读取项目根目录的 .env 文件，并将其中的变量加载到环境变量中
try:
    from dotenv import load_dotenv
    env_path = Path(BASE_DIR) / '.env'
    
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        logging.info(f"从 {env_path} 加载环境变量")
    else:
        logging.info("未找到 .env 文件，直接使用系统环境变量")
except ImportError:
    # 如果没有安装 python-dotenv，只使用系统环境变量（Docker环境下正常）
    logging.info("未安装 python-dotenv，使用系统环境变量")

class AppConfig:
    """
    一个用于加载、管理和验证应用配置的类。
    
    配置来源（统一使用环境变量）：
    - Docker环境：通过 docker-compose 的 environment 或 env_file 传递
    - 本地开发：通过项目根目录的 .env 文件（由 python-dotenv 自动加载）
    
    """
    def __init__(self, *, database_config: DatabaseConfig | None = None):
        """
        初始化配置类。
        所有配置都从环境变量加载（本地开发通过.env文件自动加载到环境变量）
        """
        
        # 应用必需的配置
        self.SECRET_KEY = self._get_config("SECRET_KEY")

        # AI 模型配置
        self.API_KEY = self._get_config("API_KEY")
        self.BASE_URL = self._get_config("BASE_URL")
        self.MODEL = self._get_config("MODEL")

        # 独立的数据库配置仍以相同字段暴露，兼容现有 App/Admin/Worker 调用方。
        if database_config is None:
            database_config = DatabaseConfig.from_env()
        for name in (
            "MYSQL_HOST",
            "MYSQL_WRITE_HOST",
            "MYSQL_READ_HOSTS",
            "MYSQL_PORT",
            "MYSQL_USER",
            "MYSQL_PASSWORD",
            "MYSQL_WRITE_USER",
            "MYSQL_WRITE_PASSWORD",
            "MYSQL_READ_USER",
            "MYSQL_READ_PASSWORD",
            "MYSQL_REPLICA_STATUS_USER",
            "MYSQL_REPLICA_STATUS_PASSWORD",
            "MYSQL_DATABASE",
            "MYSQL_POOL_SIZE_WRITE",
            "MYSQL_POOL_SIZE_READ",
            "MYSQL_CONNECT_TIMEOUT_SECONDS",
            "MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS",
            "MYSQL_POOL_ACQUIRE_RETRY_MS",
            "MYSQL_REPLICA_STATUS_CACHE_SECONDS",
            "MYSQL_REPLICA_MAX_LAG_SECONDS",
            "MYSQL_QUERY_WARN_MS",
        ):
            setattr(self, name, getattr(database_config, name))

        checkpoint_postgres = CheckpointPostgresConfig.from_env()
        self.CHECKPOINT_POSTGRES_HOST = checkpoint_postgres.host
        self.CHECKPOINT_POSTGRES_PORT = checkpoint_postgres.port
        self.CHECKPOINT_POSTGRES_DATABASE = checkpoint_postgres.database
        self.CHECKPOINT_POSTGRES_USER = checkpoint_postgres.user
        self.CHECKPOINT_POSTGRES_PASSWORD = checkpoint_postgres.password
        self.CHECKPOINT_POSTGRES_CONNECT_TIMEOUT_SECONDS = checkpoint_postgres.connect_timeout_seconds
        self.CHECKPOINT_POSTGRES_POOL_MIN_SIZE = checkpoint_postgres.pool_min_size
        self.CHECKPOINT_POSTGRES_POOL_MAX_SIZE = checkpoint_postgres.pool_max_size
        self.DB_INSPECTION_QUERY_TIMEOUT_MS = self._get_int_config(
            "DB_INSPECTION_QUERY_TIMEOUT_MS",
            default=3000,
        )
        self.DB_DASHBOARD_CONNECTION_WARNING_PERCENT = self._get_int_config(
            "DB_DASHBOARD_CONNECTION_WARNING_PERCENT",
            default=70,
        )
        self.DB_DASHBOARD_CONNECTION_CRITICAL_PERCENT = self._get_int_config(
            "DB_DASHBOARD_CONNECTION_CRITICAL_PERCENT",
            default=85,
        )
        self.DB_MONITOR_AUTO_REFRESH_ENABLED = self._get_bool_config(
            "DB_MONITOR_AUTO_REFRESH_ENABLED",
            default=True,
        )
        self.DB_MONITOR_REALTIME_INTERVAL_SECONDS = self._get_int_config(
            "DB_MONITOR_REALTIME_INTERVAL_SECONDS",
            default=10,
        )
        self.DB_MONITOR_SQL_INTERVAL_SECONDS = self._get_int_config(
            "DB_MONITOR_SQL_INTERVAL_SECONDS",
            default=60,
        )
        self.DB_MONITOR_TABLE_CAPACITY_INTERVAL_SECONDS = self._get_int_config(
            "DB_MONITOR_TABLE_CAPACITY_INTERVAL_SECONDS",
            default=900,
        )
        self.DB_MONITOR_SLOW_QUERY_WARNING_DELTA = self._get_int_config(
            "DB_MONITOR_SLOW_QUERY_WARNING_DELTA",
            default=1,
        )
        self.DB_MONITOR_INTEGRITY_ENABLED = self._get_bool_config(
            "DB_MONITOR_INTEGRITY_ENABLED",
            default=False,
        )
        self.DB_MONITOR_INTEGRITY_INTERVAL_SECONDS = self._get_int_config(
            "DB_MONITOR_INTEGRITY_INTERVAL_SECONDS",
            default=86400,
        )
        self.CHAT_FRONTEND_DIST_DIR = self._get_config(
            "CHAT_FRONTEND_DIST_DIR",
            required=False,
            default="",
        )
        self.CHAT_VITE_DEV_SERVER_URL = self._get_config(
            "CHAT_VITE_DEV_SERVER_URL",
            required=False,
            default="",
        ).rstrip("/")
        self.WEBSITE_FRONTEND_DIST_DIR = self._get_config(
            "WEBSITE_FRONTEND_DIST_DIR",
            required=False,
            default="",
        )
        self.WEBSITE_VITE_DEV_SERVER_URL = self._get_config(
            "WEBSITE_VITE_DEV_SERVER_URL",
            required=False,
            default="",
        ).rstrip("/")
        self.RAG_EVAL_FRONTEND_DIST_DIR = self._get_config(
            "RAG_EVAL_FRONTEND_DIST_DIR",
            required=False,
            default="",
        )
        self.ADMIN_VITE_DEV_SERVER_URL = self._get_config(
            "ADMIN_VITE_DEV_SERVER_URL",
            required=False,
            default="",
        ).rstrip("/")
        self.ADMIN_FRONTEND_DIST_DIR = self._get_config(
            "ADMIN_FRONTEND_DIST_DIR",
            required=False,
            default="",
        )
        self.ADMIN_BATCH_MAX_TARGETS = self._get_int_config(
            "ADMIN_BATCH_MAX_TARGETS",
            default=20,
        )
        self.ADMIN_DELETE_MAX_RELATED_ROWS = self._get_int_config(
            "ADMIN_DELETE_MAX_RELATED_ROWS",
            default=10000,
        )
        self.ADMIN_DB_LOCK_WAIT_TIMEOUT_SECONDS = self._get_int_config(
            "ADMIN_DB_LOCK_WAIT_TIMEOUT_SECONDS",
            default=5,
        )
        positive_database_values = {
            "CHECKPOINT_POSTGRES_CONNECT_TIMEOUT_SECONDS": self.CHECKPOINT_POSTGRES_CONNECT_TIMEOUT_SECONDS,
            "CHECKPOINT_POSTGRES_POOL_MIN_SIZE": self.CHECKPOINT_POSTGRES_POOL_MIN_SIZE,
            "CHECKPOINT_POSTGRES_POOL_MAX_SIZE": self.CHECKPOINT_POSTGRES_POOL_MAX_SIZE,
            "ADMIN_BATCH_MAX_TARGETS": self.ADMIN_BATCH_MAX_TARGETS,
            "ADMIN_DELETE_MAX_RELATED_ROWS": self.ADMIN_DELETE_MAX_RELATED_ROWS,
            "ADMIN_DB_LOCK_WAIT_TIMEOUT_SECONDS": self.ADMIN_DB_LOCK_WAIT_TIMEOUT_SECONDS,
        }
        for name, value in positive_database_values.items():
            if value <= 0:
                raise ValueError(f"配置错误: {name} 必须大于 0。")
        if self.ADMIN_BATCH_MAX_TARGETS > 50:
            raise ValueError("配置错误: ADMIN_BATCH_MAX_TARGETS 不能超过 50。")
        checkpoint_postgres.validate()
        if self.DB_INSPECTION_QUERY_TIMEOUT_MS <= 0:
            raise ValueError("配置错误: DB_INSPECTION_QUERY_TIMEOUT_MS 必须大于 0。")
        if not (
            0
            <= self.DB_DASHBOARD_CONNECTION_WARNING_PERCENT
            < self.DB_DASHBOARD_CONNECTION_CRITICAL_PERCENT
            <= 100
        ):
            raise ValueError(
                "配置错误: 数据库看板连接阈值必须满足 "
                "0 <= WARNING < CRITICAL <= 100。"
            )
        monitor_ranges = (
            ("DB_MONITOR_REALTIME_INTERVAL_SECONDS", self.DB_MONITOR_REALTIME_INTERVAL_SECONDS, 5, 10),
            ("DB_MONITOR_SQL_INTERVAL_SECONDS", self.DB_MONITOR_SQL_INTERVAL_SECONDS, 30, 60),
            (
                "DB_MONITOR_TABLE_CAPACITY_INTERVAL_SECONDS",
                self.DB_MONITOR_TABLE_CAPACITY_INTERVAL_SECONDS,
                300,
                900,
            ),
        )
        for name, value, minimum, maximum in monitor_ranges:
            if not minimum <= value <= maximum:
                raise ValueError(
                    f"配置错误: {name} 必须在 {minimum} 到 {maximum} 之间。"
                )
        if self.DB_MONITOR_SLOW_QUERY_WARNING_DELTA <= 0:
            raise ValueError("配置错误: DB_MONITOR_SLOW_QUERY_WARNING_DELTA 必须大于 0。")
        if self.DB_MONITOR_INTEGRITY_INTERVAL_SECONDS < 3600:
            raise ValueError(
                "配置错误: DB_MONITOR_INTEGRITY_INTERVAL_SECONDS 必须至少为 3600。"
            )
        self.JOB_WORKERS = self._get_int_config("JOB_WORKERS", default=2)
        self.JOB_POLL_INTERVAL_SECONDS = self._get_float_config("JOB_POLL_INTERVAL_SECONDS", default=1.0)
        self.JOB_HEARTBEAT_INTERVAL_SECONDS = self._get_int_config(
            "JOB_HEARTBEAT_INTERVAL_SECONDS",
            default=10,
        )
        self.JOB_STALE_AFTER_SECONDS = self._get_int_config("JOB_STALE_AFTER_SECONDS", default=120)
        self.JOB_DRAINING_STALE_AFTER_SECONDS = self._get_int_config(
            "JOB_DRAINING_STALE_AFTER_SECONDS",
            default=420,
        )
        self.JOB_DRAIN_TIMEOUT_SECONDS = self._get_int_config(
            "JOB_DRAIN_TIMEOUT_SECONDS",
            default=60,
        )
        if self.JOB_DRAIN_TIMEOUT_SECONDS <= 0:
            raise ValueError("配置错误: JOB_DRAIN_TIMEOUT_SECONDS 必须大于 0。")
        self.JOB_MAX_ATTEMPTS = self._get_int_config("JOB_MAX_ATTEMPTS", default=3)
        self.JOB_CHAT_HISTORY_LIMIT = self._get_int_config("JOB_CHAT_HISTORY_LIMIT", default=20)
        self.RAG_EVAL_EVALUATION_WORKERS = self._get_int_config(
            "RAG_EVAL_EVALUATION_WORKERS",
            default=5,
        )
        if not 1 <= self.RAG_EVAL_EVALUATION_WORKERS <= 16:
            raise ValueError("配置错误: RAG_EVAL_EVALUATION_WORKERS 必须在 1 到 16 之间。")
        rag_eval_kind_limits = (
            ("RAG_EVAL_INGESTION_CONCURRENCY_LIMIT", 1),
            ("RAG_EVAL_CANDIDATE_GENERATION_CONCURRENCY_LIMIT", 1),
            ("RAG_EVAL_TUNING_DATASET_GOVERNANCE_CONCURRENCY_LIMIT", 1),
            ("RAG_EVAL_DATASET_GOVERNANCE_CONCURRENCY_LIMIT", 1),
            ("RAG_EVAL_EVALUATION_CONCURRENCY_LIMIT", 3),
            ("RAG_EVAL_RAG_QUERY_CONCURRENCY_LIMIT", 2),
        )
        for name, default in rag_eval_kind_limits:
            value = self._get_int_config(name, default=default)
            if not 1 <= value <= 5:
                raise ValueError(f"配置错误: {name} 必须在 1 到 5 之间。")
            setattr(self, name, value)
        self.RAG_EVAL_ROOT = self._get_config(
            "RAG_EVAL_ROOT",
            required=False,
            default="tmp/rag_eval",
        )
        self.RAG_EVAL_DATASET_ROOT = self._get_config(
            "RAG_EVAL_DATASET_ROOT",
            required=False,
            default=str(Path(self.RAG_EVAL_ROOT) / "datasets" / "registered"),
        )
        self.RAG_EVAL_EVALUATION_POLL_INTERVAL_SECONDS = self._get_float_config(
            "RAG_EVAL_EVALUATION_POLL_INTERVAL_SECONDS",
            default=1.0,
        )
        self.RAG_EVAL_EVALUATION_HEARTBEAT_INTERVAL_SECONDS = self._get_int_config(
            "RAG_EVAL_EVALUATION_HEARTBEAT_INTERVAL_SECONDS",
            default=10,
        )
        self.RAG_EVAL_EVALUATION_JOB_STALE_AFTER_SECONDS = self._get_int_config(
            "RAG_EVAL_EVALUATION_JOB_STALE_AFTER_SECONDS",
            default=120,
        )
        self.SSE_POLL_INTERVAL_SECONDS = self._get_float_config("SSE_POLL_INTERVAL_SECONDS", default=1.0)
        self.SSE_HEARTBEAT_INTERVAL_SECONDS = self._get_int_config(
            "SSE_HEARTBEAT_INTERVAL_SECONDS",
            default=15,
        )
        self.MAX_UPLOAD_SIZE_MB = self._get_int_config("MAX_UPLOAD_SIZE_MB", default=20)
        self.MAX_UPLOAD_SIZE_BYTES = self.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        self.MYSQL_REPLICATION_USER = self._get_config(
            "MYSQL_REPLICATION_USER",
            required=False,
            default="replica"
        )
        self.MYSQL_REPLICATION_PASSWORD = self._get_config(
            "MYSQL_REPLICATION_PASSWORD",
            required=False,
            default=None
        )
        
        # LangSmith 可选配置，继续兼容项目原有的 LANGCHAIN_* 变量。
        self.LANGCHAIN_API_KEY = self._get_config("LANGCHAIN_API_KEY", required=False)
        self.LANGCHAIN_PROJECT = self._get_config(
            "LANGCHAIN_PROJECT",
            required=False,
            default="CausalAgent-Default-Project",
        )

        # 联网搜索 provider 配置
        self.WEB_SEARCH_PROVIDER = self._get_config(
            "WEB_SEARCH_PROVIDER",
            required=False,
            default="searxng",
        )
        self.SEARXNG_URL = self._get_config(
            "SEARXNG_URL",
            required=False,
            default="http://searxng:8080",
        ).rstrip("/")
        self.ARXIV_API_URL = self._get_config(
            "ARXIV_API_URL",
            required=False,
            default="http://export.arxiv.org/api/query",
        ).rstrip("/")

        # 浏览器访问 Grafana 的地址：开发默认本机映射端口，预发由网关反代到 /grafana/。
        self.GRAFANA_PUBLIC_URL = self._get_config(
            "GRAFANA_PUBLIC_URL",
            required=False,
            default="http://127.0.0.1:3000/grafana/",
        )

        # 初始化完成后，自动设置 LangSmith
        self._setup_langsmith()

    def _get_config(self, key, required=True, default=None):
        """
        从环境变量获取配置项。
        
        参数：
            key (str): 配置项名称（环境变量名）
            required (bool): 是否为必需项，默认True
            default: 默认值（仅当required=False时有效）
        
        返回：
            配置值（字符串）或默认值
        
        异常：
            ValueError: 当必需项缺失时抛出
        
        """
        # 从环境变量获取配置值
        value = os.environ.get(key)
        
        # 如果获取到值，直接返回
        if value:
            return value
        
        # 如果是必需项且未找到，抛出异常
        if required:
            error_msg = (
                f"配置错误: 缺少必需的环境变量 '{key}'。\n"
                f"请确保：\n"
                f"  - Docker环境：在项目根目录的 .env 文件中设置 {key}=...\n"
                f"  - 本地开发：在项目根目录的 .env 文件中设置 {key}=...\n"
                f"  - 或直接设置系统环境变量\n"
            )
            logging.error(error_msg)
            raise ValueError(error_msg)
        
        # 可选项未找到，返回默认值
        return default

    def _get_int_config(self, key, default):
        value = os.environ.get(key)
        if value in (None, ""):
            return default
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"配置错误: 环境变量 '{key}' 必须是整数，当前值为 '{value}'。") from exc

    def _get_float_config(self, key, default):
        value = os.environ.get(key)
        if value in (None, ""):
            return default
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"配置错误: 环境变量 '{key}' 必须是数字，当前值为 '{value}'。") from exc

    def _get_bool_config(self, key, default):
        """严格解析布尔环境变量，避免非空字符串被错误视为启用。"""
        value = os.environ.get(key)
        if value in (None, ""):
            return default
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        raise ValueError(
            f"配置错误: 环境变量 '{key}' 只能是 true 或 false，当前值为 '{value}'。"
        )

    @staticmethod
    def _parse_csv_config(value):
        if not value:
            return []
        return [item.strip() for item in value.split(",") if item.strip()]
        
    def _setup_langsmith(self):
        """
        根据配置设置 LangSmith 追踪的环境变量。
        
        LangSmith 是可选功能，用于追踪和调试 LangChain 应用。
        如果未配置 LANGCHAIN_API_KEY，应用仍可正常运行，只是不会有追踪功能。
        """
        if self.LANGCHAIN_API_KEY:
            os.environ.pop("LANGCHAIN_TRACING", None)
            os.environ.pop("LANGCHAIN_HANDLER", None)
            os.environ["LANGCHAIN_TRACING_V2"] = "true"
            os.environ["LANGCHAIN_API_KEY"] = self.LANGCHAIN_API_KEY
            os.environ["LANGSMITH_ENDPOINT"] = "https://api.smith.langchain.com"
            os.environ["LANGCHAIN_PROJECT"] = self.LANGCHAIN_PROJECT
            logging.info(f"LangSmith 追踪已启用，项目名: '{self.LANGCHAIN_PROJECT}'")
        else:
            logging.warning("未找到 'LANGCHAIN_API_KEY' 环境变量。LangSmith 追踪将不会启用。")

#  单例模式：创建全局唯一的配置实例 
# 在应用启动时，尝试加载配置。
# 如果失败，settings 将为 None，依赖此配置的服务将无法启动。
settings = None
try:
    settings = AppConfig(database_config=database_settings)
    logging.info("应用配置已从环境变量成功加载。")
except (FileNotFoundError, ValueError) as e:
    logging.critical(f"配置加载失败，应用无法启动: {e}")

    raise
