"""运行入口的受管事件目录与静态隐私政策。"""

from __future__ import annotations

import ast
from pathlib import Path

from observability.event_catalog import EVENT_SPECS, REASON_CODES


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_ROOTS = (
    PROJECT_ROOT / "app",
    PROJECT_ROOT / "Agent",
    PROJECT_ROOT / "Database",
    PROJECT_ROOT / "CausalAgent.py",
)
OFFLINE_LOG_ALLOWLIST = {
    "Database/audit_before_db_upgrade.py",
    # 已冻结且没有生产导入方的旧 MySQL checkpointer，仅保留历史离线诊断入口。
    "Database/mysql_checkpointer.py",
}
OFFLINE_PRINT_ALLOWLIST = {
    "Agent/Processing/data_visualize.py",
    # 人工自检入口：只在直接执行模块时运行，不进入 worker/web/MCP 运行路径。
    "Agent/Processing/nonlinearity.py",
    "Agent/causal/causalachieve.py",
    # 构建期与一次性卷同步 CLI：输出用于镜像构建和运维命令。
    "Agent/CausalAgentMCP/cdfm_weights.py",
    "Agent/knowledge_base/build_knowledge.py",
    "Agent/knowledge_base/query_rag.py",
    "Database/database_init.py",
    "Database/job_execution_upgrade_repair.py",
    "Database/lifecycle_repair.py",
    "Database/mysql_checkpointer.py",
    "app/auth/admin_cli.py",
}
PLAIN_LOG_METHODS = {
    "debug",
    "info",
    "warning",
    "error",
    "exception",
    "critical",
    "log",
}
SENSITIVE_DETAIL_KEYS = {
    "username",
    "filename",
    "title",
    "message",
    "prompt",
    "llm_output",
    "csv_data",
    "file_content",
    "sql",
    "sql_params",
    "connection_url",
    "exception",
    "error",
}


def _runtime_files():
    for root in RUNTIME_ROOTS:
        if root.is_file():
            yield root
        else:
            yield from root.rglob("*.py")


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _is_logger_receiver(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        name = node.id.lower()
        return name in {"logging", "logger", "log"} or name.endswith("logger")
    return False


def _event_literals(node: ast.AST) -> set[str]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant)
        and isinstance(child.value, str)
        and "." in child.value
    }


def test_runtime_paths_have_no_plain_application_logging_calls():
    violations: list[str] = []
    for path in _runtime_files():
        relative = _relative(path)
        if relative in OFFLINE_LOG_ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in PLAIN_LOG_METHODS:
                continue
            if _is_logger_receiver(node.func.value):
                violations.append(f"{relative}:{node.lineno}:{node.func.attr}")
    assert violations == []


def test_worker_wait_expiry_events_are_registered_with_bounded_count():
    """等待确认扫描的收敛与失败都使用受管事件，数量字段不携带明细。"""
    from observability.event_catalog import validate_event_details

    _, expired, violation = validate_event_details("job.input_wait.expired", {"expired_count": 2})
    assert (expired, violation) == ({"expired_count": 2}, None)
    _, failed, violation = validate_event_details(
        "job.input_wait.expire_failed", {"reason_code": "database_unavailable"}
    )
    assert (failed, violation) == ({"reason_code": "database_unavailable"}, None)


def test_terminal_prints_are_confined_to_explicit_offline_or_cli_files():
    violations: list[str] = []
    for path in _runtime_files():
        relative = _relative(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                continue
            if relative not in OFFLINE_PRINT_ALLOWLIST:
                violations.append(f"{relative}:{node.lineno}:print")
    assert violations == []


def test_log_event_literals_and_detail_keys_are_registered_and_safe():
    violations: list[str] = []
    for path in _runtime_files():
        relative = _relative(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "log_event"
                and len(node.args) >= 2
            ):
                continue
            event_codes = _event_literals(node.args[1])
            if not event_codes:
                violations.append(f"{relative}:{node.lineno}:dynamic_event_code")
                continue
            for event_code in event_codes:
                spec = EVENT_SPECS.get(event_code)
                if spec is None:
                    violations.append(f"{relative}:{node.lineno}:unknown:{event_code}")
                    continue
                details = next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "details"),
                    None,
                )
                if details is None:
                    continue
                if not isinstance(details, ast.Dict):
                    violations.append(
                        f"{relative}:{node.lineno}:dynamic_details:{event_code}"
                    )
                    continue
                keys = {
                    key.value
                    for key in details.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
                for key in sorted(keys - set(spec.details)):
                    violations.append(
                        f"{relative}:{node.lineno}:unexpected_detail:{event_code}:{key}"
                    )
                for key in sorted(keys & SENSITIVE_DETAIL_KEYS):
                    violations.append(
                        f"{relative}:{node.lineno}:sensitive_detail:{event_code}:{key}"
                    )
    assert violations == []


def test_request_failure_helpers_use_registered_literal_reason_codes():
    violations: list[str] = []
    for path in _runtime_files():
        relative = _relative(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "log_request_failure"
            ):
                continue
            reason = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "reason_code"),
                None,
            )
            if reason is None:
                continue
            if not isinstance(reason, ast.Constant) or not isinstance(reason.value, str):
                violations.append(f"{relative}:{node.lineno}:dynamic_reason_code")
            elif reason.value not in REASON_CODES:
                violations.append(
                    f"{relative}:{node.lineno}:unknown_reason_code:{reason.value}"
                )
    assert violations == []


def test_mcp_runtime_does_not_enable_verbose_algorithm_stdout():
    path = PROJECT_ROOT / "Agent" / "causal" / "causalachieve.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=_relative(path))
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "verbose"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
            ):
                violations.append(f"{_relative(path)}:{node.lineno}:verbose_stdout")
    assert violations == []


def test_web_search_planner_internal_degradation_uses_managed_event():
    """Planner 自行吞掉结构化输出异常时，必须留下受管降级事件。"""
    path = PROJECT_ROOT / "Agent" / "causal_agent" / "nodes.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=_relative(path))
    planner = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "web_search_planner_node"
    )
    managed_calls = [
        node
        for node in ast.walk(planner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_log_node_degradation"
    ]
    assert len(managed_calls) == 1
