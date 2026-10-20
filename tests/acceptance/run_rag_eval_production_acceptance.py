"""执行声明式 RAG 评测验收矩阵，且不通过 shell 执行命令。"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX_PATH = REPOSITORY_ROOT / "Document" / "rag_eval_production_acceptance_matrix.json"
LAYERS = {"contract", "integration", "production"}
RUNNERS = {"unittest", "compose", "alembic", "readiness"}
EXPECTED = {"pass", "single_head"}
REQUIRES = {"repository_metadata", "temporary_fixture"}
SOURCE_FORMATS = {"not_applicable", "pdf", "txt", "markdown", "csv", "xlsx", "image"}
SAFE_UNITTEST_TARGETS = {
    "tests.unit.rag_eval.test_isolated_rag_eval_routes",
    "tests.unit.agent.test_multimodal_parsing",
    "tests.unit.agent.test_multimodal_rag_eval",
    "tests.unit.rag_eval.test_rag_eval_dataset_registry",
    "tests.unit.rag_eval.test_rag_eval_index_binding",
    "tests.unit.rag_eval.test_rag_eval_queue_capacity",
    "tests.unit.rag_eval.test_rag_eval_run_lifecycle",
    "tests.unit.rag_eval.test_rag_eval_worker_queue",
}
SAFE_COMPOSE_FILES = {"docker-compose.yml", "docker-compose.prod.yml", "docker-compose.replica.yml"}
SAFE_READINESS_TARGETS = {"repository_metadata"}
READINESS_REQUIRED_FILES = (
    Path("Document/rag_eval_production_acceptance_matrix.json"),
    Path("docker-compose.yml"),
    Path("docker-compose.prod.yml"),
    Path("docker-compose.replica.yml"),
    Path("alembic.ini"),
    Path("app/rag_eval/routes.py"),
    Path("app/rag_eval/worker.py"),
    Path("app/rag_eval/job_service.py"),
    Path("Agent/knowledge_base/rag/rag_eval/rag_eval.py"),
    Path("Database/migrations/versions/s4d5e6f7a8b9_merge_develop_and_rag_eval.py"),
)
CHECK_FIELDS = {"id", "layer", "capability", "source_format", "runner", "target", "expected", "mutates_external_state", "requires"}


def load_matrix(path: Path) -> dict:
    """加载验收矩阵；矩阵无效时按 fail-closed 处理。"""
    with Path(path).open(encoding="utf-8") as handle:
        matrix = json.load(handle)
    validate_matrix(matrix)
    return matrix


def validate_matrix(matrix: dict) -> None:
    """只校验本执行器支持的声明式结构。"""
    if set(matrix) != {"schema_version", "default_layer", "checks"}:
        raise ValueError("unsupported matrix fields")
    if matrix["schema_version"] != "rag_eval_production_acceptance_v1":
        raise ValueError("unsupported schema version")
    if matrix["default_layer"] != "contract" or not isinstance(matrix["checks"], list):
        raise ValueError("invalid default layer or checks")
    ids = set()
    for check in matrix["checks"]:
        if not isinstance(check, dict) or set(check) != CHECK_FIELDS:
            raise ValueError("unsupported check fields")
        if not isinstance(check["id"], str) or not check["id"] or check["id"] in ids:
            raise ValueError("invalid or duplicate check id")
        ids.add(check["id"])
        if not isinstance(check["layer"], str) or not isinstance(check["runner"], str):
            raise ValueError("layer and runner must be strings")
        if check["layer"] not in LAYERS or check["runner"] not in RUNNERS:
            raise ValueError("unsupported layer or runner")
        if not isinstance(check["expected"], str) or not isinstance(check["source_format"], str):
            raise ValueError("expected result and source format must be strings")
        if check["expected"] not in EXPECTED or check["source_format"] not in SOURCE_FORMATS:
            raise ValueError("unsupported expected result or source format")
        if not isinstance(check["capability"], str) or not check["capability"]:
            raise ValueError("capability must be a non-empty string")
        if not isinstance(check["mutates_external_state"], bool) or not isinstance(check["requires"], list):
            raise ValueError("invalid safety fields")
        if any(not isinstance(requirement, str) or requirement not in REQUIRES for requirement in check["requires"]):
            raise ValueError("unsupported requirement")
        _validate_target(check)
        if check["layer"] == "contract" and check["mutates_external_state"]:
            raise ValueError("contract checks must be non-mutating")
        if check["layer"] == "production" and (
            check["runner"] != "readiness" or check["mutates_external_state"]
        ):
            raise ValueError("production checks must be read-only readiness checks")


def _validate_target(check: dict) -> None:
    """只允许使用代码为各执行器维护的白名单目标。"""
    target = check["target"]
    if not isinstance(target, str):
        raise ValueError("target must be a string")
    allowed = {
        "unittest": SAFE_UNITTEST_TARGETS,
        "compose": SAFE_COMPOSE_FILES,
        "alembic": {"heads"},
        "readiness": SAFE_READINESS_TARGETS,
    }[check["runner"]]
    if target not in allowed:
        raise ValueError("unsupported target")
    if check["runner"] == "alembic" and check["expected"] != "single_head":
        raise ValueError("alembic requires single_head expectation")
    if check["runner"] != "alembic" and check["expected"] != "pass":
        raise ValueError("unsupported expectation for runner")


def select_checks(matrix: dict, layer: str | None = None) -> list[dict]:
    """选择一个已校验的层级；未指定时使用矩阵的 contract 层。"""
    selected_layer = matrix["default_layer"] if layer is None else layer
    if selected_layer not in LAYERS:
        raise ValueError("unsupported layer")
    return [check for check in matrix["checks"] if check["layer"] == selected_layer]


def prepare_run(matrix: dict, layer: str | None, confirmed: bool) -> list[dict]:
    """选择生产就绪检查前要求显式确认。"""
    selected_layer = matrix["default_layer"] if layer is None else layer
    if selected_layer == "production" and not confirmed:
        raise ValueError("production requires --confirm-production-readiness")
    return select_checks(matrix, selected_layer)


def resolve_output_path(output: Path | None, matrix_path: Path) -> Path:
    """预留不在矩阵和隔离评测运行产物目录中的新报告文件。"""
    if output is None:
        directory = Path(tempfile.mkdtemp(prefix="rag-eval-acceptance-"))
        return directory / "report.json"
    candidate = Path(output).resolve()
    matrix = Path(matrix_path).resolve()
    rag_eval_runs = (REPOSITORY_ROOT / "tmp" / "rag_eval_isolated_runs").resolve()
    if candidate == matrix or candidate.exists() or _is_within(candidate, rag_eval_runs):
        raise ValueError("unsafe output path")
    return candidate


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def execute_check(check: dict) -> dict:
    """执行一个白名单内且不经 shell 的验收检查。"""
    command = None
    if check["runner"] == "unittest":
        # 目标模块通过 pytest 入口执行，才能获得 tests/conftest.py 安装的离线密钥
        # 文件和 tracing 设置；unittest 直接加载模块会绕过这些收集期配置。
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "/".join(check["target"].split(".")) + ".py",
        ]
    elif check["runner"] == "compose":
        command = ["docker", "compose", "-f", check["target"], "config", "--quiet"]
    elif check["runner"] == "alembic":
        command = [sys.executable, "-m", "alembic", "heads"]
    elif check["runner"] == "readiness":
        return _repository_metadata_readiness(check)
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    stdout = completed.stdout or ""
    detail = (stdout + (completed.stderr or "")).strip()
    if check["runner"] == "alembic" and completed.returncode == 0:
        heads = [line for line in stdout.splitlines() if line.strip()]
        passed = len(heads) == 1
    else:
        passed = completed.returncode == 0
    return {"id": check["id"], "status": "pass" if passed else "fail", "detail": detail[-2000:]}


def _repository_metadata_readiness(check: dict) -> dict:
    """只检查固定仓库文件，不启动服务或访问数据库。"""
    missing = []
    for relative_path in READINESS_REQUIRED_FILES:
        candidate = (REPOSITORY_ROOT / relative_path).resolve()
        if not _is_within(candidate, REPOSITORY_ROOT) or not candidate.is_file():
            missing.append(relative_path.as_posix())
    detail = "repository metadata readiness passed" if not missing else "missing required files: " + ", ".join(missing)
    return {"id": check["id"], "status": "pass" if not missing else "fail", "detail": detail}


def run(matrix: dict, layer: str | None = None, confirmed: bool = False, list_only: bool = False, output: Path | None = None) -> dict:
    """构建验收报告；仅在非 list 请求时真正执行检查。"""
    selected = prepare_run(matrix, layer, confirmed)
    selected_layer = matrix["default_layer"] if layer is None else layer
    report = {"schema_version": matrix["schema_version"], "layer": selected_layer, "selected_checks": selected}
    if list_only:
        return report
    started = datetime.now(timezone.utc).isoformat()
    timer = monotonic()
    results = [execute_check(check) for check in selected]
    report.update({
        "results": results,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(monotonic() - timer, 3),
        "passed": all(result["status"] == "pass" for result in results),
    })
    destination = resolve_output_path(output, DEFAULT_MATRIX_PATH)
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    with os.fdopen(os.open(destination, flags), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    report["output"] = str(destination)
    return report


def main() -> int:
    """解析安全且显式的层级选择，并打印验收报告。"""
    # Windows 控制台默认代码页无法编码中文报告；显式使用 UTF-8 避免本地验收中断。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--layer", choices=sorted(LAYERS))
    parser.add_argument("--list", action="store_true", dest="list_only")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--confirm-production-readiness", action="store_true")
    arguments = parser.parse_args()
    try:
        matrix = load_matrix(DEFAULT_MATRIX_PATH)
        report = run(matrix, arguments.layer, arguments.confirm_production_readiness, arguments.list_only, arguments.output)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if arguments.list_only:
        return 0
    return 0 if report.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
