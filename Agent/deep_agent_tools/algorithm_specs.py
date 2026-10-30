"""算法方法定义与工具说明的单一事实源。"""

from __future__ import annotations

import hashlib
import math
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.json_schema import WithJsonSchema
from typing_extensions import Annotated

from Agent.effect_request import normalize_effect_request

from .models import (
    CausalEffect,
    ContractModel,
    MethodDefinition,
    PublicDecision,
    StandardizedGraph,
    canonical_json_bytes,
)


AlgorithmKind = Literal[
    "causal_discovery",
    "causal_estimation",
    "evidence_retrieval",
]

Availability = Literal["available", "unavailable", "unknown"]

@lru_cache(maxsize=None)
def build_model_tool_input_schema(
    base_schema: type[BaseModel],
) -> type[BaseModel]:
    """给模型工具 envelope 增加公开选择说明，不改变科学参数契约。"""

    decision_schema = PublicDecision.model_json_schema()
    tolerant_decision_type = Annotated[Any, WithJsonSchema(decision_schema)]
    namespace: dict[str, Any] = {
        "__annotations__": {"public_decision": tolerant_decision_type | None},
        "__module__": base_schema.__module__,
        "public_decision": Field(
            default=None,
            description=(
                "面向当前用户公开的工具选择依据；应在同一次 Tool Call 中提供。"
                "该字段不属于算法科学参数或检索参数。"
            ),
        ),
    }
    return type(
        f"{base_schema.__name__}ToolInput",
        (base_schema,),
        namespace,
    )


class CausalPcInput(ContractModel):
    """PC 的模型可见科学参数；Job/文件/lease 不在此 schema 中。"""

    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    test: Literal["auto", "fisherz", "chisq"] = Field(
        default="auto",
        description=(
            "条件独立性检验方式。auto 在全部变量均为数值时使用 Fisher-Z，"
            "存在分类编码列时使用卡方检验。"
        ),
    )


class CausalOlcInput(ContractModel):
    """OLC 的模型可见显著性参数。"""

    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    beta: float = Field(default=0.01, gt=0.0, lt=1.0)


class CausalDirectLiNGAMInput(ContractModel):
    """DirectLiNGAM 当前没有可由模型改变的公开参数。"""



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


EFFECT_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["effect", "intervention"],
    "properties": {
        "effect": {"type": "string", "enum": ["average_total_effect"]},
        "target_population": {"type": ["string", "null"], "minLength": 1},
        "intervention": {
            "anyOf": [
                {
                    "type": "object", "additionalProperties": False,
                    "required": ["type", "from", "to", "unit"],
                    "properties": {
                        "type": {"type": "string", "enum": ["fixed_contrast"]},
                        "from": {"type": "number"}, "to": {"type": "number"},
                        "unit": {"type": "string", "minLength": 1},
                    },
                },
                {
                    "type": "object", "additionalProperties": False,
                    "required": ["type", "delta", "unit"],
                    "properties": {
                        "type": {"type": "string", "enum": ["additive_shift"]},
                        "delta": {"type": "number"},
                        "unit": {"type": "string", "minLength": 1},
                    },
                },
            ],
        },
    },
}


class CausalBackdoorOlsInput(ContractModel):
    """后门调整 ATE 的模型可见参数；图只能通过结果引用提供。"""

    graph_result_ref: str = Field(
        description="本次运行中已发现且有效的因果图结果引用。",
    )
    treatment: str
    outcome: str
    effect_request: Annotated[dict[str, Any], WithJsonSchema(EFFECT_REQUEST_SCHEMA)] = Field(
        description="已确认的平均总效应请求；原始单位固定取值对比或增量干预。不得补猜默认对比。",
    )

    @field_validator("effect_request", mode="before")
    @classmethod
    def validate_request(cls, value: Any) -> dict[str, Any]:
        request = normalize_effect_request(value)
        if request is None:
            raise ValueError("a confirmed effect_request object is required")
        return request

    @field_validator("graph_result_ref", "treatment", "outcome")
    @classmethod
    def validate_reference_strings(cls, value: str, info: Any) -> str:
        if not value or not value.strip() or value != value.strip():
            raise ValueError(f"{info.field_name} must be a non-blank string")
        return value


class CausalGFormulaInput(CausalBackdoorOlsInput):
    """g-formula 的模型可见参数；随机种子由服务端固定。"""

    graph_result_ref: str = Field(
        description="本次运行中已发现且有效的因果图结果引用。",
    )
    treatment: str
    outcome: str
    outcome_model: Literal["linear", "hgb"] = "linear"
    bootstrap: int = Field(default=200, ge=0, le=2000)

    @field_validator("graph_result_ref", "treatment", "outcome")
    @classmethod
    def validate_reference_strings(cls, value: str, info: Any) -> str:
        if not value or not value.strip() or value != value.strip():
            raise ValueError(f"{info.field_name} must be a non-blank string")
        return value

    @field_validator("bootstrap", mode="before")
    @classmethod
    def validate_bootstrap_type(cls, value: Any) -> Any:
        if type(value) is not int or value == 1:
            raise ValueError("bootstrap must be zero or an integer of at least two")
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
    requires_numeric_columns: bool = False
    requires_complete_cases: bool = False
    native_missing_handling: Literal["reject", "mask"] = "reject"
    supports_encoded_categories: bool = False
    min_rows: int = Field(default=2, ge=1)
    min_columns: int = Field(default=2, ge=1)
    enabled: bool = True
    availability: Availability = "available"
    cost_hint: str | None = None
    graph_semantics: str = "algorithm_graph"
    matrix_convention: str = "unspecified"
    edge_meanings: dict[str, str] = Field(default_factory=dict)
    limitations: tuple[str, ...] = ()

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
        if self.requires_complete_cases and self.native_missing_handling != "reject":
            raise ValueError(
                "a spec that requires complete cases cannot declare native masking"
            )
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
            "requires_numeric_columns": self.requires_numeric_columns,
            "requires_complete_cases": self.requires_complete_cases,
            "native_missing_handling": self.native_missing_handling,
            "supports_encoded_categories": self.supports_encoded_categories,
            "min_rows": self.min_rows,
            "min_columns": self.min_columns,
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
            "method": self.method_definition().model_dump(mode="json"),
        }

    @property
    def spec_digest(self) -> str:
        """当前 Spec canonical JSON 的 SHA-256。"""

        return hashlib.sha256(canonical_json_bytes(self.canonical_payload())).hexdigest()

    def build_tool_schema(self) -> dict[str, Any]:
        """从 Spec 生成模型可见的本地 function-tool schema。"""

        return {
            "name": self.tool_name,
            "description": self.model_description(),
            "parameters": build_model_tool_input_schema(
                self.model_input_schema,
            ).model_json_schema(),
        }

    def model_description(self) -> str:
        """把输入要求与允许的处理方式公开给模型，不承诺未实现能力。"""

        requirements: list[str] = []
        if self.requires_numeric_columns:
            requirements.append("全部输入变量必须是数值列")
        if self.categorical_allowed():
            requirements.append("分类列必须先按确定性顺序编码")
        if self.requires_complete_cases:
            requirements.append("不允许存在缺失值、空列或非有限值")
        elif self.native_missing_handling == "mask":
            requirements.append("缺失值按原生掩码处理；非有限值仍需显式处理")
        requirements.append(f"至少 {self.min_rows} 行、{self.min_columns} 列")
        method = self.method_definition()
        return (
            f"{self.description}\n数据要求：{'；'.join(requirements)}。"
            f"\n方法假设：{'；'.join(method.assumptions)}。"
            f"\n目标图：{method.graph_semantics}；矩阵约定：{method.matrix_convention}。"
            f"\n边语义：{'；'.join(f'{key}：{value}' for key, value in method.edge_meanings.items())}。"
            f"\n适用边界：{'；'.join(method.limitations)}。"
            "所有数据整理工作在分析开始前完成；本工具不会修改输入数据。"
        )

    def method_definition(self) -> MethodDefinition:
        return MethodDefinition(
            capability_id=self.capability_id, public_name=self.public_name,
            contract_version=self.version, description=self.description,
            input_requirements=self.data_requirements, assumptions=self.assumptions,
            graph_semantics=self.graph_semantics, matrix_convention=self.matrix_convention,
            edge_meanings=dict(self.edge_meanings), limitations=self.limitations,
        )

    def categorical_allowed(self) -> bool:
        """只有声明分类编码的 Spec 才能接收离散变量。"""

        return self.supports_encoded_categories

    def assess_applicability(
        self,
        *,
        profile: Any,
        execution_columns: tuple[str, ...] | None = None,
        semantic_kinds: Mapping[str, str] | None = None,
    ) -> tuple[str, str | None]:
        """基于实际处理后表格和测量语义判断输入是否满足算法契约。"""

        if profile is None:
            return "needs_processing", "缺少数据画像"
        if profile.row_count < self.min_rows:
            return "not_applicable", "行数少于算法要求：" + str(profile.row_count)
        if profile.column_count < self.min_columns:
            return "not_applicable", "列数少于算法要求：" + str(profile.column_count)

        selected: tuple[str, ...] = execution_columns or profile.column_names
        if len(selected) < self.min_columns:
            return "not_applicable", "输入变量数少于算法要求"
        if len(set(selected)) != len(selected) or set(selected) - set(profile.column_names):
            return "not_applicable", "执行变量与数据字段不一致"

        empty = set(profile.empty_columns) & set(selected)
        if empty:
            return "not_applicable", "存在完全缺失的列：" + "、".join(sorted(empty))
        constant = set(profile.constant_columns) & set(selected)
        if constant:
            return "not_applicable", "存在常数列：" + "、".join(sorted(constant))
        categorical = set(profile.categorical_columns) & set(selected)
        categorical.update(
            column
            for column, kind in (semantic_kinds or {}).items()
            if column in selected and kind in {"category", "ordinal", "binary"}
        )
        if categorical and not self.categorical_allowed():
            return (
                "not_applicable",
                "分类变量不适用于该算法：" + "、".join(sorted(categorical)),
            )
        if self.requires_numeric_columns:
            non_numeric = set(selected) - set(profile.numeric_columns)
            if non_numeric:
                return (
                    "not_applicable",
                    "非数值变量不适用于该算法：" + "、".join(sorted(non_numeric)),
                )

        missing = set(profile.missing_columns) & set(selected)
        if missing and (
            self.requires_complete_cases or self.native_missing_handling == "reject"
        ):
            return (
                "not_applicable",
                "输入含有算法不接受的缺失值：" + "、".join(sorted(missing)),
            )
        non_finite = set(profile.non_finite_columns) & set(selected)
        if non_finite:
            return (
                "not_applicable",
                "输入含有非有限值：" + "、".join(sorted(non_finite)),
            )
        return "ready", None


PC_SPEC = AlgorithmSpec(
    capability_id="causal.pc",
    version="2.0",
    graph_semantics="pdag",
    matrix_convention="causal_learn_endpoints",
    edge_meanings={"directed": "候选因果方向", "undirected": "方向尚未识别的相邻关系"},
    limitations=(
        "条件独立性检验和有限样本会影响结构；具体条件按实际 independence_test 判断",
        "Fisher-Z 基于偏相关与高斯条件独立关系，不能因 PC 是通用算法就声称本次检验不受线性或分布条件限制",
        "骨架相近不等于方向得到验证", "不直接估计因果效应",
    ),
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
        "因果马尔可夫性、忠实性和无潜在混杂的因果充分性",
        "数据中的变量名和样本身份已经由外层准入冻结",
    ),
    result_contract=StandardizedGraph,
    default_timeout_seconds=300,
    concurrency_key="causal_pc",
    default_concurrency=2,
    analysis_goal="在一般观测数据中发现条件独立结构和候选因果关系",
    description_source=("用途", "输入要求", "统计假设", "不适用边界"),
    data_requirements=(
        "tabular_dataset",
        "可解析的列名",
        "非空样本",
        "缺失值、空列、常数列和非有限值需要显式处理",
    ),
    requires_numeric_columns=False,
    requires_complete_cases=True,
    native_missing_handling="reject",
    supports_encoded_categories=True,
    min_rows=2,
    min_columns=2,
)


OLC_SPEC = AlgorithmSpec(
    capability_id="causal.olc",
    graph_semantics="latent_mixed_graph",
    matrix_convention="runner_edges",
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
    data_requirements=(
        "tabular_dataset",
        "continuous_tabular_dataset",
        "足够样本量",
        "无缺失值",
        "无非有限值",
    ),
    requires_numeric_columns=True,
    requires_complete_cases=True,
    native_missing_handling="reject",
    min_rows=2,
    min_columns=2,
)


DIRECT_LINGAM_SPEC = AlgorithmSpec(
    capability_id="causal.direct_lingam",
    version="2.0",
    graph_semantics="dag_target_to_source",
    matrix_convention="target_to_source",
    edge_meanings={"directed": "线性结构方程中的候选直接作用；权重是系数而非置信度"},
    limitations=("线性、非高斯、误差独立和无潜在混杂须分别检验或保持未检验", "图中系数不是已识别的干预效应"),
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
    data_requirements=("continuous_tabular_dataset", "无缺失数值列", "无非有限值"),
    requires_numeric_columns=True,
    requires_complete_cases=True,
    native_missing_handling="reject",
    min_rows=2,
    min_columns=2,
)


CDFM_SPEC = AlgorithmSpec(
    capability_id="causal.cdfm",
    version="2.0",
    graph_semantics="acyclic_directed_mixed_graph",
    matrix_convention="row_to_column_pair_binary",
    edge_meanings={"directed": "模型推断的候选因果方向", "bidirected": "潜在共同原因候选，不代表已经证实存在共同原因"},
    limitations=("机制覆盖不保证当前数据上的可识别性或准确率", "阈值可改变结构；边不携带效应量或置信度", "不继承 LiNGAM 的线性、非高斯和无潜在混杂假设"),
    tool_name="causal_cdfm",
    public_name="CDFM 因果发现",
    description=(
        "用预训练因果发现基础模型 CDFM 在数值表格数据上做零样本图推断，"
        "一次调用返回整张候选混合图。同一个 checkpoint 的训练机制包括线性、非线性、异方差、"
        "有序或离散观测、测量误差与潜在混杂等多类机制，因此机制未知或明显非线性时，"
        "可以作为候选方法，并与 PC、DirectLiNGAM 的结果分别对照。"
        "不传 threshold 时由服务端按模型自带校准器自动决定判决边界，传入 0 到 1 的数值可覆盖它。"
        "缺失值按 NaN/±Inf 掩码传递给模型；当前接收数值存储列，不接受未编码的非数值分类列。"
        "数值准入不证明测量语义连续，也不保证训练机制适用于当前分布。"
        "输出只有边及其方向，不包含权重和置信度：方向可能随阈值变化，"
        "不可识别场景下方向证据不足；成对的两个 1 解码为一条双箭头边，表示潜在共同原因候选，"
        "结果不构成准确率或生产能力证明。"
    ),
    model_input_schema=CausalCdfmInput,
    requires=frozenset({"continuous_tabular_dataset"}),
    produces=frozenset({"standardized_graph", "diagnostics"}),
    assumptions=(
        "输入采用数值存储；实际测量语义和分布适用性须依据证据评估",
        "零样本识别以预训练机制覆盖当前数据分布为前提",
        "变量名、样本顺序和冻结输入身份由外层准入固定",
        "CDFM checkpoint 可由 causal-mcp 服务端加载",
    ),
    result_contract=StandardizedGraph,
    default_timeout_seconds=600,
    concurrency_key="causal_cdfm",
    default_concurrency=1,
    analysis_goal="在数值观测数据上生成 CDFM 的候选混合图",
    description_source=(
        "用途",
        "机制覆盖",
        "阈值与自动校准",
        "缺失值掩码",
        "结果解读",
        "能力边界",
    ),
    data_requirements=(
        "数值型表格数据；存储类型不等同于连续测量语义",
        "至少 2 行数据",
        "至少 2 个变量",
        "不直接接受未编码的非数值分类列；数值编码不证明统计适用性",
    ),
    requires_numeric_columns=True,
    requires_complete_cases=False,
    native_missing_handling="mask",
    supports_encoded_categories=True,
    min_rows=2,
    min_columns=2,
)


CAUSAL_BACKDOOR_OLS_SPEC = AlgorithmSpec(
    capability_id="causal.backdoor_ols",
    version="3.0",
    tool_name="causal_backdoor_ols",
    public_name="因果效应估计（后门调整）",
    description=(
        "在后门调整下估计处理变量对结果变量的线性总效应。"
        "必须引用本次运行中已经发现且有效的因果图；"
        "不支持未定向边、双箭头边、有向环或处理变量不是结果变量祖先的图。"
        "同一张图只能与产生它的数据版本一起使用。"
    ),
    model_input_schema=CausalBackdoorOlsInput,
    requires=frozenset({"standardized_graph"}),
    produces=frozenset(),
    assumptions=(
        "图包含查询的处理与结果变量，且无未定向边、双箭头边或有向环",
        "处理变量、结果变量和处理变量的全部父节点为数值存储，无缺失、常数或非有限值",
        "函数形式近似线性，效应按已确认的干预幅度解释，不对非线性或交互建模",
        "调整集取处理变量的全部父节点，不搜索其他有效调整集",
    ),
    result_contract=CausalEffect,
    default_timeout_seconds=120,
    concurrency_key="causal_backdoor_ols",
    default_concurrency=1,
    kind="causal_estimation",
    analysis_goal="在被引用的因果图上估计处理变量对结果变量的线性总效应",
    description_source=("用途", "图引用要求", "线性假设", "不适用边界"),
    data_requirements=(
        "standardized_graph",
        "处理变量、结果变量和全部调整变量为数值存储，不转换字符串数值",
        "已声明为 category、ordinal、binary 的估计列不适用，不自动编码",
        "无缺失值、空列、常数列或非有限值",
    ),
    requires_numeric_columns=True,
    requires_complete_cases=True,
    native_missing_handling="reject",
    min_rows=3,
    min_columns=2,
    graph_semantics="causal_estimation_input",
    matrix_convention="backdoor_ols",
    edge_meanings={"directed": "后门调整所用的因果方向"},
    limitations=(
        "只做父节点调整",
        "未声明的测量语义仍未充分确认，不根据整数类型或唯一值数量推断连续语义",
        "不删除调整变量、不删除行、不填值、不编码或改变源图",
        "不处理未观测混杂、选择偏倚或测量误差",
        "不做 bootstrap，区间为线性模型内区间",
    ),
)


CAUSAL_G_FORMULA_SPEC = AlgorithmSpec(
    capability_id="causal.g_formula",
    version="3.0",
    tool_name="causal_g_formula",
    public_name="因果效应估计（g-formula）",
    description=(
        "在给定因果图上用调整后直接标准化估计处理变量对比的因果效应。"
        "调整集取处理变量的全部父节点，结局方程可选线性或梯度提升；"
        "不支持未定向边、双箭头边、有向环或处理变量不是结果变量祖先的图。"
    ),
    model_input_schema=CausalGFormulaInput,
    requires=frozenset({"standardized_graph"}),
    produces=frozenset(),
    assumptions=(
        "图包含查询的处理与结果变量，且无未定向边、双箭头边或有向环",
        "处理变量是结果变量的祖先",
        "相关列均为有限数值，缺失值已在执行前处理",
        "调整集取处理变量的全部父节点，不搜索其他有效调整集",
        "调整集有效且无未观测混杂时，条件均值差等于总效应",
        "结局方程由 outcome_model 决定，只拟合 E[Y|T,Z]",
    ),
    result_contract=CausalEffect,
    default_timeout_seconds=600,
    concurrency_key="causal_g_formula",
    default_concurrency=1,
    kind="causal_estimation",
    analysis_goal="在给定因果图上用调整后直接标准化估计处理变量对比的因果效应",
    description_source=("用途", "图引用要求", "模型选择", "不适用边界"),
    data_requirements=(
        "standardized_graph",
        "处理变量、结果变量及处理变量全部父节点为数值列",
        "无缺失值、空列、常数列或非有限值",
    ),
    requires_numeric_columns=True,
    requires_complete_cases=True,
    native_missing_handling="reject",
    min_rows=3,
    min_columns=2,
    graph_semantics="causal_estimation_input",
    matrix_convention="g_formula",
    edge_meanings={"directed": "确定调整集与可识别性的因果方向"},
    limitations=(
        "结局模型只估计条件均值差；存在未建模交互时线性模型仍可能有偏",
        "不处理二值/计数/有序结局的尺度转换",
        "bootstrap 为 percentile 区间，不做 BCa",
        "不处理未观测混杂、选择偏倚或测量误差",
    ),
)


DEFAULT_ALGORITHM_SPECS: tuple[AlgorithmSpec, ...] = (
    PC_SPEC,
    DIRECT_LINGAM_SPEC,
    CDFM_SPEC,
    CAUSAL_BACKDOOR_OLS_SPEC,
    CAUSAL_G_FORMULA_SPEC,
)


def get_algorithm_spec(capability_id: str) -> AlgorithmSpec:
    for spec in (*DEFAULT_ALGORITHM_SPECS, OLC_SPEC):
        if spec.capability_id == capability_id:
            return spec
    raise ValueError("unknown algorithm capability")


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

