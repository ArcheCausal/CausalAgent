"""Windows Developer Preview Release workflow 的静态安全契约。"""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "release-windows.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_workflow_builds_with_the_desktop_virtual_environment():
    workflow = _workflow_text()

    assert "python -m venv .venv-desktop" in workflow
    assert ".\\.venv-desktop\\Scripts\\python.exe -m pip install" in workflow
    assert ".\\.venv-desktop\\Scripts\\python.exe -m pytest" in workflow
    assert ".\\.venv-desktop\\Scripts\\python.exe -c" in workflow


def test_manual_recovery_checks_out_and_validates_the_target_tag():
    workflow = _workflow_text()

    assert "workflow_dispatch:" in workflow
    assert "target_tag:" in workflow
    assert "upload_to_published_release:" in workflow
    assert "ref: ${{ env.TARGET_TAG }}" in workflow
    assert 'git show-ref --verify --quiet "refs/tags/$tag"' in workflow
    assert "$headCommit -ne $tagCommit" in workflow
    assert "git merge-base --is-ancestor $tagCommit origin/main" in workflow


def test_published_release_recovery_is_fail_closed_and_never_clobbers_assets():
    workflow = _workflow_text()

    assert '$env:RECOVERY_UPLOAD -eq "true"' in workflow
    assert "$release.draft -eq $true" in workflow
    assert "$release.immutable -eq $true" in workflow
    assert "$collisions.Count -gt 0" in workflow
    assert "--clobber" not in workflow
