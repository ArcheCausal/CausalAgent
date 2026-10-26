"""算法方法定义与工具说明的单一事实源。"""

from __future__ import annotations

import hashlib
import math
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.json_schema import WithJsonSchema
from typing_extensions import Annotated

from .models import (
    CausalEffect,
    ContractModel,
    MethodDefinition,
    PREPROCESSING_OPERATION_ORDER,
    PreprocessingOperation,
    PreprocessingPlan,
    PreprocessingStep,
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

_PREPROCESSING_LABELS: dict[str, str] = {
    "select_columns": "选列",
    "drop_rows": "按列删除含缺失值的行",
    "numeric_impute": "数值插补",
    "categorical_encode": "分类编码",
}


@lru_cache(maxsize=None)
def build_model_tool_input_schema(
    base_schema: type[BaseModel],
    *,
    include_preprocessing: bool = False,
) -> type[BaseModel]:
    """给模型工具 envelope 增加公开说明，不改变底层业务参数契约。

    只有算法工具接受 preprocessing；证据检索工具不得出现该字段。
    """

    decision_schema = PublicDecision.model_json_schema()
    tolerant_decision_type = Annotated[Any, WithJsonSchema(decision_schema)]
    # 模型可见 schema 手动内联，避免动态 envelope 引用无法解析的 $defs。
    # 运行时仍由 PreprocessingPlan 做严格校验。
    plan_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "version": {"type": "string", "enum": ["data-preprocessing-v1"]},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": [
                                "select_columns",
                                "drop_rows",
                                "numeric_impute",
                                "categorical_encode",
                            ],
                        },
                        "columns": {"type": "array", "items": {"type": "string"}},
                        "method": {
                            "type": ["string", "null"],
                            "enum": ["mean", "median", "ordinal", None],
                        },
                    },
                    "required": ["operation", "columns"],
                },
            },
            "protected_columns": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["steps"],
    }
    tolerant_plan_type = Annotated[Any, WithJsonSchema(plan_schema)]
    if include_preprocessing:
        annotations = {
            "public_decision": tolerant_decision_type | None,
            "preprocessing": tolerant_plan_type | None,
        }
    else:
        annotations = {"public_decision": tolerant_decision_type | None}
    namespace: dict[str, Any] = {
        "__annotations__": annotations,
        "__module__": base_schema.__module__,
        "public_decision": Field(
            default=None,
            description=(
                "面向当前用户公开的工具选择依据；应在同一次 Tool Call 中提供。"
                "该字段不属于算法科学参数或检索参数。"
            ),
        ),
    }
    if include_preprocessing:
        namespace["preprocessing"] = Field(
            default=None,
            description=(
                "用户已经确认的处理方案。只填写 request_data_processing "
                "返回的方案内容；运行时按 capability_id 与方案摘要核对，"
                "未获批或摘要不一致的方案不会执行。"
            ),
        )
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


class CausalAteInput(ContractModel):
    """后门调整 ATE 的模型可见参数；图只能通过结果引用提供。"""

    graph_result_ref: str = Field(
        description="本次运行中已发现且有效的因果图结果引用。",
    )
    treatment: str
    outcome: str

    @field_validator("graph_result_ref", "treatment", "outcome")
    @classmethod
    def validate_reference_strings(cls, value: str, info: Any) -> str:
        if not value or not value.strip() or value != value.strip():
            raise ValueError(f"{info.field_name} must be a non-blank string")
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
    allowed_preprocessing: tuple[PreprocessingOperation, ...] = ()
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
        if self.native_missing_handling == "mask" and "drop_rows" not in self.allowed_preprocessing:
            # 掩码算法仍然允许用户显式删行，但必须由方案声明，不能隐式处理。
            pass
        if len(set(self.allowed_preprocessing)) != len(self.allowed_preprocessing):
            raise ValueError("allowed_preprocessing must not repeat operations")
        order = [
            PREPROCESSING_OPERATION_ORDER.index(operation)
            for operation in self.allowed_preprocessing
        ]
        if order != sorted(order):
            raise ValueError("allowed_preprocessing must follow the canonical order")
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
            "allowed_preprocessing": list(self.allowed_preprocessing),
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
                include_preprocessing=True,
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
        if self.allowed_preprocessing:
            requirements.append(
                "可确认的处理操作："
                + "、".join(_PREPROCESSING_LABELS[item] for item in self.allowed_preprocessing)
            )
        requirements.append(f"至少 {self.min_rows} 行、{self.min_columns} 列")
        method = self.method_definition()
        return (
            f"{self.description}\n数据要求：{'；'.join(requirements)}。"
            f"\n方法假设：{'；'.join(method.assumptions)}。"
            f"\n目标图：{method.graph_semantics}；矩阵约定：{method.matrix_convention}。"
            f"\n边语义：{'；'.join(f'{key}：{value}' for key, value in method.edge_meanings.items())}。"
            f"\n适用边界：{'；'.join(method.limitations)}。"
            "如果原始数据不满足要求，先调用 request_data_processing 让用户确认"
            "处理方案，再携带同一方案调用本工具；不要自行假设清洗步骤。"
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

        return "categorical_encode" in self.allowed_preprocessing

    def validate_profile(
        self,
        *,
        profile: Any,
        preprocessing: PreprocessingPlan | None,
        execution_columns: tuple[str, ...] | None = None,
    ) -> str | None:
        """按 Spec 判断当前数据加工后能否直接执行，返回稳定诊断文本。

        这是本地初检；它不替代 MCP 对真实 CSV 的复检。execution_columns
        是处理步骤声明的目标列；没有处理方案时使用画像的全部列。
        """

        if profile is None:
            return "缺少数据画像"
        if profile.row_count < self.min_rows:
            return "行数少于要求：" + str(profile.row_count)
        if profile.column_count < self.min_columns:
            return "列数少于要求：" + str(profile.column_count)

        plan = preprocessing or PreprocessingPlan()
        selected: tuple[str, ...] = profile.column_names
        if "select_columns" in plan.operations():
            select_step = next(
                step for step in plan.steps if step.operation == "select_columns"
            )
            if len(select_step.columns) < self.min_columns:
                return "选列后列数少于要求"
            selected = select_step.columns
        if execution_columns is not None:
            missing = set(execution_columns) - set(selected)
            if missing:
                return "执行列不在数据画像中：" + "、".join(sorted(missing))

        categorical = set(profile.categorical_columns) & set(selected)
        if categorical and not self.categorical_allowed():
            return "分类变量需要算法支持离散检验：" + "、".join(sorted(categorical))
        if self.requires_numeric_columns:
            non_numeric = set(selected) - set(profile.numeric_columns)
            if non_numeric:
                return "非数值变量不适用于本算法：" + "、".join(sorted(non_numeric))

        empty = set(profile.empty_columns) & set(selected)
        if empty:
            return "存在完全缺失的列：" + "、".join(sorted(empty))
        constant = set(profile.constant_columns) & set(selected)
        if constant:
            return "存在常数列：" + "、".join(sorted(constant))

        imputed = {
            column
            for step in plan.steps
            if step.operation == "numeric_impute"
            for column in step.columns
        }
        dropped_missing = "drop_rows" in plan.operations()
        missing = (set(profile.missing_columns) & set(selected)) - imputed
        if missing and self.requires_complete_cases and not dropped_missing:
            return "存在缺失值，需要先确认处理：" + "、".join(sorted(missing))
        non_finite = (set(profile.non_finite_columns) & set(selected)) - (
            imputed if dropped_missing else set()
        )
        if non_finite:
            return "存在非有限值，需要先确认处理：" + "、".join(sorted(non_finite))
        return None

    def validate_preprocessing_plan(
        self,
        *,
        plan: PreprocessingPlan,
        profile: Any,
        protected_columns: tuple[str, ...] = (),
    ) -> None:
        """校验确认前与 MCP 执行前共用的处理方案规则。

        只检查方案本身是否可用于该算法，不判断真实 CSV 是否已经满足算法要求。
        列类型来自画像；MCP 侧会基于处理后的真实数据重新建立画像并再次判断。
        """

        operations = set(plan.operations())
        unsupported = operations - set(self.allowed_preprocessing)
        if unsupported:
            raise ValueError(
                "该算法不允许的处理操作：" + "、".join(sorted(unsupported))
            )
        if profile is None:
            raise ValueError("缺少数据画像，无法校验处理方案")
        known_columns = set(profile.column_names)
        for step in plan.steps:
            unknown = set(step.columns) - known_columns
            if unknown:
                raise ValueError("处理方案引用了不存在的列：" + "、".join(sorted(unknown)))
            if step.operation == "numeric_impute":
                non_numeric = set(step.columns) - set(profile.numeric_columns)
                if non_numeric:
                    raise ValueError(
                        "数值插补只能用于数值列：" + "、".join(sorted(non_numeric))
                    )
            if step.operation == "drop_rows" and set(step.columns) == known_columns:
                raise ValueError("删行必须指定具体列，不能默认对全表删除")
        for step in plan.steps:
            if step.operation == "drop_rows" and set(protected_columns) & set(step.columns):
                raise ValueError("不能按目标变量或处理变量的缺失值删除整行")

    def required_processing_reason(
        self,
        *,
        profile: Any,
        preprocessing: PreprocessingPlan | None,
    ) -> str | None:
        """说明为什么必须在执行前取得用户确认，已满足时返回 None。"""

        return self.validate_profile(profile=profile, preprocessing=preprocessing)

    def assess_applicability(
        self,
        *,
        profile: Any,
        preprocessing: PreprocessingPlan | None,
        execution_columns: tuple[str, ...] | None = None,
    ) -> tuple[str, str | None]:
        """返回 ready / needs_processing / not_applicable 三态判断。

        结构性不适用（变量类型不受支持、全空列、常数列、样本或变量数量
        不足）不能靠处理方案变成可用；缺失值、非有限值等则属于可由用户
        确认处理的条件。
        """

        if profile is None:
            return "needs_processing", "缺少数据画像"
        if profile.row_count < self.min_rows:
            return "not_applicable", "行数少于算法要求：" + str(profile.row_count)
        if profile.column_count < self.min_columns:
            return "not_applicable", "列数少于算法要求：" + str(profile.column_count)

        plan = preprocessing or PreprocessingPlan()
        selected: tuple[str, ...] = execution_columns or profile.column_names
        if "select_columns" in plan.operations():
            select_step = next(
                step for step in plan.steps if step.operation == "select_columns"
            )
            if len(select_step.columns) < self.min_columns:
                return "not_applicable", "选列后列数少于算法要求"
            selected = select_step.columns

        empty = set(profile.empty_columns) & set(selected)
        if empty:
            return "not_applicable", "存在完全缺失的列：" + "、".join(sorted(empty))
        constant = set(profile.constant_columns) & set(selected)
        if constant:
            return "not_applicable", "存在常数列：" + "、".join(sorted(constant))
        categorical = set(profile.categorical_columns) & set(selected)
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

        imputed = {
            column
            for step in plan.steps
            if step.operation == "numeric_impute"
            for column in step.columns
        }
        dropped_missing = "drop_rows" in plan.operations()
        missing = (set(profile.missing_columns) & set(selected)) - imputed
        if missing and self.requires_complete_cases and not dropped_missing:
            return (
                "needs_processing",
                "存在缺失值，需要确认处理：" + "、".join(sorted(missing)),
            )
        non_finite = (set(profile.non_finite_columns) & set(selected)) - (
            imputed if dropped_missing else set()
        )
        if non_finite:
            return (
                "needs_processing",
                "存在非有限值，需要确认处理：" + "、".join(sorted(non_finite)),
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
    allowed_preprocessing=(
        "select_columns",
        "drop_rows",
        "numeric_impute",
        "categorical_encode",
    ),
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
    allowed_preprocessing=("select_columns", "drop_rows", "numeric_impute"),
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
    allowed_preprocessing=("select_columns", "drop_rows", "numeric_impute"),
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
    allowed_preprocessing=("select_columns", "drop_rows", "numeric_impute"),
    min_rows=2,
    min_columns=2,
)


CAUSAL_ATE_SPEC = AlgorithmSpec(
    capability_id="causal.ate",
    version="1.0",
    tool_name="causal_ate",
    public_name="因果效应估计（后门调整）",
    description=(
        "在后门调整下估计处理变量对结果变量的线性总效应。"
        "必须引用本次运行中已经发现且有效的因果图；"
        "不支持未定向边、双箭头边、有向环或处理变量不是结果变量祖先的图。"
        "同一张图只能与产生它的数据版本一起使用。"
    ),
    model_input_schema=CausalAteInput,
    requires=frozenset({"standardized_graph"}),
    produces=frozenset(),
    assumptions=(
        "图包含查询的处理与结果变量，且无未定向边、双箭头边或有向环",
        "处理变量与结果变量为数值列，缺失值已在执行前处理",
        "函数形式近似线性，效应按每 1 单位变化解释，不对非线性或交互建模",
        "调整集取处理变量的全部父节点，不搜索其他有效调整集",
    ),
    result_contract=CausalEffect,
    default_timeout_seconds=120,
    concurrency_key="causal_ate",
    default_concurrency=1,
    kind="causal_estimation",
    analysis_goal="在被引用的因果图上估计处理变量对结果变量的线性总效应",
    description_source=("用途", "图引用要求", "线性假设", "不适用边界"),
    data_requirements=(
        "standardized_graph",
        "处理变量与结果变量为数值列",
        "无缺失值、空列、常数列或非有限值",
    ),
    requires_numeric_columns=True,
    requires_complete_cases=True,
    native_missing_handling="reject",
    allowed_preprocessing=("select_columns", "drop_rows", "numeric_impute"),
    min_rows=3,
    min_columns=2,
    graph_semantics="causal_estimation_input",
    matrix_convention="backdoor_ols",
    edge_meanings={"directed": "后门调整所用的因果方向"},
    limitations=(
        "只做父节点调整",
        "不处理未观测混杂、选择偏倚或测量误差",
        "不做 bootstrap，区间为线性模型内区间",
    ),
)


DEFAULT_ALGORITHM_SPECS: tuple[AlgorithmSpec, ...] = (
    PC_SPEC,
    DIRECT_LINGAM_SPEC,
    CDFM_SPEC,
)


def get_algorithm_spec(capability_id: str) -> AlgorithmSpec:
    for spec in (*DEFAULT_ALGORITHM_SPECS, OLC_SPEC, CAUSAL_ATE_SPEC):
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

