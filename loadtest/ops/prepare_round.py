"""预先声明定向或完整轮次，保存部署指纹并冻结客户端；不启动负载。"""
from __future__ import annotations

import argparse
import hashlib
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops.deployment_fingerprint import capture_fingerprint
from ops.freeze_client import freeze
from runner import config
from runner.flows import build_csv, build_cdfm_csv
from runner.rounds import create_manifest, write_json

REGRESSION = ["smoke", "baseline", "low", "mid_low", "mid", "stability",
              "recovery", "burst", "burst_ungated", "queue_pressure",
              "multi_session_smoke", "multi_session_pressure"]
RATE_SPECS = [(0.02, 8, 400), (0.04, 12, 300), (0.08, 16, 200),
              (0.16, 32, 200), (0.32, 64, 200)]


def prepare(target: Path, scope: str = "targeted") -> dict:
    if scope not in {"targeted", "full"}:
        raise ValueError("未知验证范围")
    if target.exists():
        raise FileExistsError("清单已存在；续跑使用原清单，不能覆盖")
    deployment = capture_fingerprint()
    frozen = freeze()
    stages = ["multi_session_smoke", "recovery"]
    if scope == "full":
        stages += [*REGRESSION, "cdfm_fixed", *(["cdfm_rate"] * len(RATE_SPECS))]
    manifest = create_manifest(target, stages, {"deployment": deployment})
    manifest["client_snapshot"] = str(frozen)
    manifest["scope"] = scope
    manifest["verification_order"] = ["historical_recalculation", "targeted"]
    if scope == "full":
        manifest["verification_order"] += ["original_12_stages", "cdfm_fixed", "cdfm_rate", "boundary_repeats"]
    rate_index = 0
    for index, row in enumerate(manifest["runs"]):
        parameters, stage = row["parameters"], row["stage"]
        row["purpose"] = ("targeted" if index < 2 else "regression" if index < 14
                          else "cdfm_baseline" if stage == "cdfm_fixed" else "cdfm_rate")
        if stage == "cdfm_rate":
            rate, tasks, duration = RATE_SPECS[rate_index]
            rate_index += 1
            parameters.update(arrival_rate=rate, tasks=tasks, duration=duration)
        users = parameters.get("users", 2 if stage == "recovery" else 1)
        budget = (7 if stage == "recovery" else parameters["tasks"] if stage.startswith("cdfm")
                  else 2 if stage == "multi_session_smoke" else users * parameters["flows"]
                  if parameters.get("flows") else math.ceil(users * parameters["duration"] / 2))
        parameters.update(task_budget=budget, observation_limit_seconds=1800,
            allow_incomplete=stage in {"recovery", "multi_session_smoke"},
            resource_limits={"cpu_percent": 98, "cpu_sustained_samples": 3,
                             "mem_available_mb": 512, "queued": 128, "connections": 200})
        if stage.startswith("cdfm"):
            inputs = [{"user_index": 1, "rows": parameters["rows"], "columns": 5,
                       "seed": parameters["seed"],
                       "sha256": hashlib.sha256(build_cdfm_csv(parameters["rows"], parameters["seed"])).hexdigest()}]
        else:
            indices = [90] if stage == "recovery" else range(1, users + 1)
            inputs = [{"user_index": 1 if stage == "recovery" else user, "rows": 40, "columns": 5, "seed": 1000 + user,
                       "sha256": hashlib.sha256(build_csv(user)).hexdigest()} for user in indices]
        parameters["input_plan"] = inputs
    write_json(target, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--scope", choices=("targeted", "full"), default="targeted",
                        help="默认只声明双会话和 Recovery；full 才包含十二档及 CDFM 阶梯")
    args = parser.parse_args()
    manifest = prepare(args.manifest.resolve(), args.scope)
    print(manifest["client_snapshot"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
