import logging
from .state import CausalAgentState
from observability.logging_runtime import log_context, log_event


ROUTE_DECISIONS = {
    "fold",
    "postprocess",
    "report",
    "normal_chat",
    "inquiry_answer",
    "context_switch",
}
FOLD_DECISIONS = {"preprocess", "agent", "normal_chat"}
LOGGER = logging.getLogger(__name__)


def _log_route_degradation(node: str, fallback: str) -> None:
    with log_context(node=node):
        log_event(
            LOGGER,
            "job.node.degraded",
            details={
                "failure_kind": "invalid_route",
                "final_attempt": 1,
                "fallback": fallback,
            },
        )

def decision_router(state: CausalAgentState) -> str:
    """
    只读取 agent 写入的显式 route_decision，不从展示消息推断控制流。
    """
    decision = state.get("route_decision")
    if decision not in ROUTE_DECISIONS:
        _log_route_degradation("decision_router", "normal_chat")
        return "normal_chat"
    return decision

def fold_router(state: CausalAgentState) -> str:
    """
    只读取 fold 写入的显式 fold_decision，不从展示消息推断控制流。
    """
    decision = state.get("fold_decision")
    if decision not in FOLD_DECISIONS:
        _log_route_degradation("fold_router", "agent")
        return "agent"
    return decision

def preprocess_router(state: CausalAgentState) -> str:
    """
    参数验证节点后的路由器。
    如果验证成功，则执行工具
    """
    return "mcp"


def execute_tool_router(state:CausalAgentState) -> str:
    """
    旧 execute_tools 节点后的兼容路由器。
    当前父图已改为 preprocess -> mcp -> rag -> agent，本函数仅保留给历史调用。
    """
    return "agent"

def mcp_router(state: CausalAgentState) -> str:
    """检测mcp是否调用成功"""
    mcp_result =  state.get("causal_analysis_result")
    if isinstance(mcp_result, dict) and mcp_result.get("success") is True:
        return "rag"
    if isinstance(mcp_result, dict) and mcp_result.get("success") is False:
        return "normal_chat"
    return "agent"


def postprocess_router(state:CausalAgentState) -> str:
    '''
    通向report_node
    '''
    return "report"


def web_search_router(state: CausalAgentState, context) -> str:
    """读 context.web_search_enabled 决定 rag 后是否走联网搜索子图。"""
    enabled = getattr(context, "web_search_enabled", False)
    return "web_search" if enabled else "agent"



