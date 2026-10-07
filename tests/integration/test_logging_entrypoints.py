"""五类进程入口的共享日志接入静态契约。"""

from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("relative_path", "event_code"),
    [
        ("CausalAgent.py", "web.startup.ready"),
        ("app/agent/worker/bootstrap.py", "worker.startup.ready"),
        ("Database/monitor_worker.py", "monitor.startup.ready"),
        ("Database/bootstrap.py", "maintenance.startup.ready"),
        ("Database/agent_persistence_cleanup_worker.py", "maintenance.startup.ready"),
        ("Agent/CausalAgentMCP/mcp_server.py", "mcp.startup.ready"),
    ],
)
def test_process_entrypoint_uses_shared_json_runtime(relative_path: str, event_code: str):
    source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
    assert "configure_logging" in source
    assert event_code in source
    assert "logging.basicConfig" not in source


def test_application_processes_do_not_keep_local_log_handlers():
    mcp_source = (PROJECT_ROOT / "Agent/CausalAgentMCP/mcp_server.py").read_text(encoding="utf-8")
    database_source = (PROJECT_ROOT / "Database/database_init.py").read_text(encoding="utf-8")
    assert "FileHandler" not in mcp_source
    assert "FileHandler" not in database_source
