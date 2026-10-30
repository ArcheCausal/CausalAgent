"""RAG 验收矩阵和只读元数据检查必须引用现存部署文件。"""

from tests.acceptance.run_rag_eval_production_acceptance import (
    DEFAULT_MATRIX_PATH,
    REPOSITORY_ROOT,
    SAFE_COMPOSE_FILES,
    execute_check,
    load_matrix,
    select_checks,
)


def test_acceptance_compose_targets_exist_and_are_allowlisted():
    matrix = load_matrix(DEFAULT_MATRIX_PATH)
    targets = {check["target"] for check in matrix["checks"] if check["runner"] == "compose"}
    assert targets
    assert targets <= SAFE_COMPOSE_FILES
    for target in SAFE_COMPOSE_FILES:
        assert (REPOSITORY_ROOT / target).is_file(), target


def test_repository_metadata_readiness_uses_existing_required_files():
    matrix = load_matrix(DEFAULT_MATRIX_PATH)
    checks = select_checks(matrix, layer="production")
    assert checks
    for check in checks:
        assert check["runner"] == "readiness"
        result = execute_check(check)
        assert result["status"] == "pass", result["detail"]
