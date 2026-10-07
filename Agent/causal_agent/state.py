"""因果分析图的共享状态定义。

本文件只描述节点之间传递的数据结构，不负责状态创建、持久化或业务处理。
"""

from operator import add
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

from typing_extensions import NotRequired

from langchain_core.messages import BaseMessage, ToolMessage

from Agent.Report.document import ReportDocument
from Agent.deep_agent_tools.models import (
    AlgorithmResult,
    DataProfile,
    EvidenceResult,
    FinalAnalysisDecision,
    InvocationRecord,
    WebEvidenceResult,
)


class FileSummary(TypedDict, total=False):
    """当前 Job 的冻结文件元数据和受限数据摘要。"""

    user_file_id: Optional[int]
    object_id: Optional[int]
    file_hash: Optional[str]
    filename: Optional[str]
    rows: Optional[int]
    columns: List[str]


class CausalAgentState(TypedDict):
    """
    表示因果分析图在各节点之间传递的共享状态。

    这个 TypedDict 既是图的运行时状态，也是节点之间传递的上下文记忆。

    Attributes:
        messages: 对话消息历史。
        user_id: 当前用户 ID。
        username: 当前用户名。
        session_id: 当前聊天会话 ID。
        tool_call_request: 下游节点是否继续工具调用流程。
        analysis_parameters: 数据摘要及分析参数。
        file_summary: 文件的有限数据摘要。
        causal_analysis_result: 因果分析任务结果。
        knowledge_base_result: 结构化RAG结果，包含问题、证据链和汇总摘要。
        preprocess_summary: 预处理阶段的自然语言总结。
        postprocess_result: 后处理补充结果。
        chart_assets: 预处理阶段生成的结构化图表资源。
        report_document: 后端装配完成的结构化报告文档。
        report_revision_mode: 报告节点本次是首次生成还是按当前上下文重新生成。
        agent_decision: agent 节点的结构化意图判断结果。
        analysis_context: 当前 AnalysisContext 的只读投影。
        analysis_context_index: 同一 Session 其他历史上下文的简要索引。
        context_resolution: context_switch 节点解析出的上下文命中结果。
    """

    messages: Annotated[List[BaseMessage], add]

    username: str
    user_id: int
    session_id: str
    job_id: NotRequired[str]
    file_summary: NotRequired[Optional[FileSummary]]
    data_profile: NotRequired[Optional[DataProfile]]

    route_decision: NotRequired[
        Literal[
            "fold",
            "postprocess",
            "report",
            "normal_chat",
            "inquiry_answer",
            "context_switch",
        ]
    ]
    fold_decision: NotRequired[Literal["preprocess", "agent", "normal_chat"]]

    tool_call_request: Optional[bool]

    analysis_parameters: Optional[dict]

    # Agent 的结构化意图判断结果；只保留意图、上下文提示和澄清问题，
    # 数据库 ID、文件 ID 和最终路由都由后端解析后写入。
    agent_decision: NotRequired[dict[str, Any]]

    # 当前 AnalysisContext 的投影，以及同一 Session 其他历史上下文的索引。
    analysis_context: NotRequired[dict[str, Any]]
    analysis_context_index: NotRequired[List[Dict[str, Any]]]

    # context_switch 解析结果：命中、歧义或无匹配，以及解析出的上下文 ID。
    context_resolution: NotRequired[dict[str, Any]]

    causal_analysis_result: Optional[dict]
    knowledge_base_result: Optional[Dict[str, Any]]
    web_search_result: Optional[dict]

    preprocess_summary: Optional[str]
    postprocess_result: Optional[dict]

    chart_assets: NotRequired[Optional[dict]]
    report_document: NotRequired[Optional[ReportDocument]]
    report_revision_mode: NotRequired[
        Literal["normal_generation", "full_regeneration_from_context"]
    ]

    # 新 Deep Agent 路径的父子 State 投影；这些字段不包含 runtime-only 对象。
    deep_agent_algorithm_results: NotRequired[dict[str, AlgorithmResult]]
    deep_agent_action_ledger: NotRequired[dict[str, InvocationRecord]]
    deep_agent_rag_evidence: NotRequired[dict[str, EvidenceResult]]
    deep_agent_web_evidence: NotRequired[dict[str, WebEvidenceResult]]
    deep_agent_decision: NotRequired[Optional[FinalAnalysisDecision]]
    deep_agent_structured_response: NotRequired[Optional[FinalAnalysisDecision]]
    deep_agent_run_id: NotRequired[str]
    deep_agent_status: NotRequired[Literal["running", "completed", "revoked", "failed"]]
    deep_agent_retry_instruction: NotRequired[str]
    finalization_retry_count: NotRequired[int]
    finalization_status: NotRequired[Literal["valid", "degraded"]]
    finalization_error: NotRequired[str]


class RagSubgraphState(TypedDict, total=False):
    """RAG 子图的私有状态。

    父图只通过适配节点提供四个只读上下文字段，并最终接收
    ``rag_output`` 的投影结果；问题列表、ToolMessage 和解析中间结果
    不会回写到 ``CausalAgentState``。
    """

    messages: Annotated[List[BaseMessage], add]

    analysis_parameters: Optional[dict]
    preprocess_summary: Optional[str]
    causal_analysis_result: Optional[dict]

    rag_route: Literal["call_tool", "parse", "finish"]
    rag_questions: List[Dict[str, Any]]
    rag_tool_message: Optional[ToolMessage]
    rag_parse_result: Optional[Dict[str, Any]]
    rag_status: Literal["available", "unavailable", "protocol_error"]
    rag_output: Optional[Dict[str, Any]]


class WebSearchInput(TypedDict):
    messages: Annotated[List[BaseMessage], add]
    analysis_parameters: Optional[dict]
    causal_analysis_result: Optional[dict]
    knowledge_base_result: Optional[dict]


class WebSearchOutput(TypedDict):
    web_search_result: Optional[dict]


class WebSearchState(WebSearchInput, WebSearchOutput):
    planner: dict
    search: dict
