"""主程序维护的 AlgorithmSpec 单一事实源；默认启用 PC 与 DirectLiNGAM。"""

from __future__ import annotations

import hashlib
import math
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.json_schema import WithJsonSchema
from typing_extensions import Annotated

from .models import ContractModel, PublicDecision, StandardizedGraph, canonical_json_bytes


AlgorithmKind = Literal[
    "causal_discovery",
    "causal_estimation",
    "evidence_retrieval",
]

Availability = Literal["available", "unavailable", "unknown"]


@lru_cache(maxsize=None)
def build_model_tool_input_schema(base_schema: type[BaseModel]) -> type[BaseModel]:
    """给模型工具 envelope 增加公开说明，不改变底层业务参数契约。"""

    decision_schema = PublicDecision.model_json_schema()
    tolerant_decision_type = Annotated[Any, WithJsonSchema(decision_schema)]
    return type(
        f"{base_schema.__name__}ToolInput",
        (base_schema,),
        {
            "__annotations__": {"public_decision": tolerant_decision_type | None},
            "__module__": base_schema.__module__,
            "public_decision": Field(
                default=None,
                description=(
                    "面向当前用户公开的工具选择依据；应在同一次 Tool Call 中提供。"
                    "该字段不属于算法科学参数或检索参数。"
                ),
            ),
        },
    )


class CausalPcInput(ContractModel):
    """PC 的模型可见科学参数；Job/文件/lease 不在此 schema 中。"""

    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)


class CausalOlcInput(ContractModel):
    """OLC 的模型可见显著性参数。"""

    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    beta: float = Field(default=0.01, gt=0.0, lt=1.0)


class CausalDirectLiNGAMInput(ContractModel):
    """DirectLiNGAM 当前没有可由模型改变的公开参数。"""

    pass


class CausalCdfmInput(ContractModel):
    """CDFM 唯一公开的科学参数；模型路径和预处理策略由服务端固定。"""

    threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "边概率的判决阈值；不传时由服务端使用模型自带校准器自动决定判决边界。"
        ),
    )

    @field_validator("threshold", mode="before")
    @classmethod
    def validate_threshold_type(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("threshold must be a number or null")
        if not math.isfinite(float(value)):
            raise ValueError("threshold must be finite")
        return value


class AlgorithmSpec(ContractModel):
    """算法能力的唯一事实源。

    ``adapter`` 不放进此模型：受控实现绑定由 Registry entry 静态保存，避免
    把可导入路径或动态 MCP 工具清单暴露成模型输入。
    """

    model_config = ContractModel.model_config | {"frozen": True, "arbitrary_types_allowed": True}

    capability_id: str
    version: str
    tool_name: str
    public_name: str
    description: str
    model_input_schema: type[BaseModel]
    requires: frozenset[str]
    produces: frozenset[str]
    assumptions: tuple[str, ...]
    result_contract: type[BaseModel]
    default_timeout_seconds: int = Field(gt=0)
    concurrency_key: str
    default_concurrency: int = Field(default=1, gt=0)
    kind: AlgorithmKind = "causal_discovery"
    analysis_goal: str = ""
    description_source: tuple[str, ...] = ()
    data_requirements: tuple[str, ...] = ()
    enabled: bool = True
    availability: Availability = "available"
    cost_hint: str | None = None

    @field_validator(
        "capability_id",
        "version",
        "tool_name",
        "public_name",
        "description",
        "concurrency_key",
    )
    @classmethod
    def validate_non_blank_strings(cls, value: str, info: Any) -> str:
        if not value or not value.strip() or value != value.strip():
            raise ValueError(f"{info.field_name} must be a non-blank string")
        return value

    @field_validator("analysis_goal", "cost_hint")
    @classmethod
    def validate_optional_description_strings(
        cls, value: str | None, info: Any
    ) -> str | None:
        if value is None or value == "":
            return value
        if not value.strip() or value != value.strip():
            raise ValueError(f"{info.field_name} must not contain surrounding whitespace")
        return value

    @field_validator("model_input_schema", "result_contract")
    @classmethod
    def validate_schema_types(cls, value: type[BaseModel], info: Any) -> type[BaseModel]:
        if not isinstance(value, type) or not issubclass(value, BaseModel):
            raise TypeError(f"{info.field_name} must be a Pydantic BaseModel class")
        return value

    @field_validator("requires", "produces")
    @classmethod
    def validate_artifacts(cls, value: frozenset[str], info: Any) -> frozenset[str]:
        if any(not item or not item.strip() or item != item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-blank artifact names")
        return frozenset(value)

    @model_validator(mode="after")
    def validate_spec_semantics(self) -> "AlgorithmSpec":
        if not self.assumptions:
            raise ValueError("AlgorithmSpec must declare at least one assumption")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        """返回用于 spec digest 的排序无关、无 Python class repr 的 payload。"""

        return {
            "analysis_goal": self.analysis_goal,
            "assumptions": list(self.assumptions),
            "availability": self.availability,
            "capability_id": self.capability_id,
            "concurrency_key": self.concurrency_key,
            "data_requirements": list(self.data_requirements),
            "default_concurrency": self.default_concurrency,
            "default_timeout_seconds": self.default_timeout_seconds,
            "description": self.description,
            "description_source": list(self.description_source),
            "enabled": self.enabled,
            "kind": self.kind,
            "model_input_schema": self.model_input_schema.model_json_schema(),
            "produces": sorted(self.produces),
            "public_name": self.public_name,
            "requires": sorted(self.requires),
            "result_contract": self.result_contract.model_json_schema(),
            "tool_name": self.tool_name,
            "version": self.version,
        }

    @property
    def spec_digest(self) -> str:
        """当前 Spec canonical JSON 的 SHA-256。"""

        return hashlib.sha256(canonical_json_bytes(self.canonical_payload())).hexdigest()

    def build_tool_schema(self) -> dict[str, Any]:
        """从 Spec 生成模型可见的本地 function-tool schema。"""

        return {
            "name": self.tool_name,
            "description": self.description,
            "parameters": build_model_tool_input_schema(
                self.model_input_schema
            ).model_json_schema(),
        }


PC_SPEC = AlgorithmSpec(
    capability_id="causal.pc",
    version="1.0",
    tool_name="causal_pc",
    public_name="PC 因果发现",
    description=(
        "在表格数据上使用 PC 方法发现条件独立结构并生成部分有向图。"
        "适用于一般观测因果发现；需要数值或可确定性编码的表格数据，"
        "不应在明显存在潜在混杂且需要专门处理时作为唯一方法。"
    ),
    model_input_schema=CausalPcInput,
    requires=frozenset({"tabular_dataset"}),
    produces=frozenset({"standardized_graph", "diagnostics"}),
    assumptions=(
        "条件独立性检验适用于当前数据",
        "数据中的变量名和样本身份已经由外层准入冻结",
    ),
    result_contract=StandardizedGraph,
    default_timeout_seconds=300,
    concurrency_key="causal_pc",
    default_concurrency=2,
    analysis_goal="在一般观测数据中发现条件独立结构和候选因果关系",
    description_source=("用途", "输入要求", "统计假设", "不适用边界"),
    data_requirements=("tabular_dataset", "可解析的列名", "非空样本"),
)


OLC_SPEC = AlgorithmSpec(
    capability_id="causal.olc",
    version="1.0",
    tool_name="causal_olc",
    public_name="OLC 潜在混杂因果发现",
    description=(
        "在连续表格数据上使用 OLC 处理可能存在潜在混杂的因果发现。"
        "它不适用于离散变量、非常小的样本或可以明确排除潜在混杂的场景。"
    ),
    model_input_schema=CausalOlcInput,
    requires=frozenset({"tabular_dataset"}),
    produces=frozenset({"standardized_graph", "diagnostics"}),
    assumptions=(
        "输入主要为连续变量",
        "样本量足以支持高阶统计量估计",
        "可接受存在潜在混杂及加性噪声假设",
    ),
    result_contract=StandardizedGraph,
    default_timeout_seconds=600,
    concurrency_key="causal_olc",
    default_concurrency=1,
    analysis_goal="在可能存在潜在混杂的连续观测数据中发现结构",
    description_source=("用途", "连续变量要求", "样本量", "潜在混杂假设", "不适用边界"),
    data_requirements=("tabular_dataset", "continuous_tabular_dataset", "足够样本量"),
)


DIRECT_LINGAM_SPEC = AlgorithmSpec(
    capability_id="causal.direct_lingam",
    version="1.0",
    tool_name="causal_direct_lingam",
    public_name="DirectLiNGAM 因果发现",
    description=(
        "在连续数值数据上使用 DirectLiNGAM 估计有向无环因果结构。"
        "适用于线性、非高斯、误差独立且没有潜在混杂的场景。"
    ),
    model_input_schema=CausalDirectLiNGAMInput,
    requires=frozenset({"continuous_tabular_dataset"}),
    produces=frozenset({"standardized_graph", "diagnostics"}),
    assumptions=(
        "输入为连续数值变量",
        "因果机制近似线性且噪声非高斯",
        "误差独立并且不存在潜在混杂",
    ),
    result_contract=StandardizedGraph,
    default_timeout_seconds=300,
    concurrency_key="causal_direct_lingam",
    default_concurrency=1,
    analysis_goal="在连续、线性、非高斯观测数据中估计有向无环结构",
    description_source=("用途", "连续数值要求", "线性/非高斯假设", "不适用边界"),
    data_requirements=("continuous_tabular_dataset", "无缺失数值列"),
)


CDFM_SPEC = AlgorithmSpec(
    capability_id="causal.cdfm",
    version="1.0",
    tool_name="causal_cdfm",
    public_name="CDFM 因果发现",
    description=(
        "用预训练因果发现基础模型 CDFM 在连续数值表格数据上做零样本图推断，"
        "一次调用返回整张候选有向图。同一个 checkpoint 已覆盖线性、非线性、异方差、"
        "有序或离散观测、测量误差与潜在混杂等多类机制，因此机制未知或明显非线性时，"
        "比依赖单一假设的工具更合适，也可以与 PC、DirectLiNGAM 的结果并行对照。"
        "不传 threshold 时由服务端按模型自带校准器自动决定判决边界，传入 0 到 1 的数值可覆盖它。"
        "缺失值按 NaN/±Inf 掩码传递给模型；当前只接受连续数值列，不接受分类变量。"
        "输出只有边及其方向，不包含权重和置信度：方向可能随阈值变化，"
        "不可识别场景下方向证据不足，两条相反方向的边也可能对应潜在混杂，"
        "结果不构成准确率或生产能力证明。"
    ),
    model_input_schema=CausalCdfmInput,
    requires=frozenset({"continuous_tabular_dataset"}),
    produces=frozenset({"standardized_graph", "diagnostics"}),
    assumptions=(
        "输入为连续数值变量",
        "零样本识别以预训练机制覆盖当前数据分布为前提",
        "变量名、样本顺序和冻结输入身份由外层准入固定",
        "CDFM checkpoint 可由 causal-mcp 服务端加载",
    ),
    result_contract=StandardizedGraph,
    default_timeout_seconds=600,
    concurrency_key="causal_cdfm",
    default_concurrency=1,
    analysis_goal="在连续观测数据上生成 CDFM 的有向图推断结果",
    description_source=(
        "用途",
        "机制覆盖",
        "阈值与自动校准",
        "缺失值掩码",
        "结果解读",
        "能力边界",
    ),
    data_requirements=(
        "continuous_tabular_dataset",
        "至少 2 行数据",
        "至少 2 个变量",
        "分类变量不适用",
    ),
)


DEFAULT_ALGORITHM_SPECS: tuple[AlgorithmSpec, ...] = (
    PC_SPEC,
    DIRECT_LINGAM_SPEC,
    CDFM_SPEC,
)


def build_tool_schema_snapshot() -> list[dict[str, Any]]:
    """返回按 capability_id 排序的模型 Tool schema 快照。"""

    return [
        {
            "capability_id": spec.capability_id,
            "spec_digest": spec.spec_digest,
            "tool": spec.build_tool_schema(),
        }
        for spec in sorted(DEFAULT_ALGORITHM_SPECS, key=lambda item: item.capability_id)
    ]

