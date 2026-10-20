"""发布工作流静态契约及解析器的真实参数失败路径；不模拟 GHCR 响应。

工具需求：Git CLI，用于读取当前提交并验证参数失败路径；缺少时失败而不是跳过。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.resolve_release_images import verify_revision

ROOT = Path(__file__).resolve().parents[3]


def workflow(name):
    return yaml.load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_bundle_runs_only_after_all_four_images_have_been_published():
    publish = workflow("publish-staging-images.yml")
    bundle = publish["jobs"]["build-release-bundle"]
    assert "build-and-push" in bundle["needs"]
    assert "if" not in bundle  # 保留 needs 的默认成功条件，不能在构建失败后打包。
    assert bundle["uses"] == "./.github/workflows/build-release-bundle.yml"
    assert bundle["with"]["source_commit"] == "${{ github.sha }}"
    assert len(publish["jobs"]["build-and-push"]["strategy"]["matrix"]["include"]) == 4


def test_manual_overrides_remain_optional_and_the_version_is_explicit():
    bundle = workflow("build-release-bundle.yml")
    dispatch = bundle["on"]["workflow_dispatch"]["inputs"]
    for name in ("release_id", "source_commit", "app_sha256", "mcp_sha256",
                 "mysql_primary_sha256", "mysql_replica_sha256"):
        assert dispatch[name]["required"] == "false"
    steps = bundle["jobs"]["build-bundle"]["steps"]
    selection = next(step for step in steps if step.get("id") == "source")
    assert "status: 'success'" in selection["with"]["script"]
    assert "head_sha" in selection["with"]["script"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ steps.source.outputs.ref }}"
    resolver = next(step for step in steps if step.get("id") == "images")
    assert resolver["env"]["SOURCE_COMMIT"] == "${{ steps.version.outputs.source_commit }}"
    assert "continue-on-error" not in resolver
    assert "||" not in resolver["run"]


def test_workflow_inputs_are_passed_as_data_instead_of_shell_source():
    steps = workflow("build-release-bundle.yml")["jobs"]["build-bundle"]["steps"]
    for step in steps:
        assert "${{" not in step.get("run", "")
        assert "${{" not in step.get("with", {}).get("script", "")


@pytest.mark.parametrize("option,value", [
    ("--owner", "../invalid"),
    ("--source-commit", "develop"),
    ("--app-sha256", "not-a-digest"),
    ("--mcp-sha256", "sha256:short"),
    ("--mysql-primary-sha256", "latest"),
    ("--mysql-replica-sha256", "sha256:missing"),
])
def test_invalid_inputs_fail_before_registry_access_and_write_no_output(tmp_path, option, value):
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.strip()
    output = tmp_path / "images.json"
    github_output = tmp_path / "github-output"
    command = [sys.executable, str(ROOT / "scripts/resolve_release_images.py"),
               "--owner", "archecausal", "--source-commit", commit,
               "--output", str(output), "--github-output", str(github_output), option, value]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 2, proc.stderr
    assert "error:" in proc.stderr
    assert not output.exists()
    assert not github_output.exists()


@pytest.mark.parametrize("image", [None, {}, {"config": {}}, {"linux/amd64": {}},
                                    {"unknown/unknown": {}}])
def test_unverifiable_image_metadata_is_rejected(image):
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.strip()
    with pytest.raises(ValueError):
        verify_revision(image, commit)
