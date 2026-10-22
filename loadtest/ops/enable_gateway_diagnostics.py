"""在现有 nginx 配置上增补诊断，保留原文件备份并先检查候选配置。"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
from datetime import datetime, timezone


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    source = (pathlib.Path(__file__).resolve().parents[2] / "deploy/staging/nginx.conf").read_text(encoding="utf-8")
    start = source.index("    # 只记录白名单只读 API")
    end = source.index("    # Grafana Live", start)
    addition = source[start:end]
    access = next(line for line in source.splitlines() if "access_log" in line and "api_diagnostic_json" in line) + "\n"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    script = r"""
import hashlib,json,pathlib,subprocess
container='causalagent-staging-gateway-1'
def run(*args,**kw): return subprocess.run(list(args),check=True,capture_output=True,**kw)
mounts=json.loads(run('sudo','-n','docker','inspect',container).stdout)[0]['Mounts']
target=next(row['Source'] for row in mounts if row['Destination']=='/etc/nginx/nginx.conf')
old=run('sudo','-n','cat',target).stdout.decode()
if 'api_diagnostic_json' in old:
 old_access=next(line for line in old.splitlines() if 'access_log' in line and 'api_diagnostic_json' in line)+chr(10)
 candidate=old.replace(old_access,ACCESS,1)
 if candidate==old:
  print(json.dumps({'status':'already_present','sha256':hashlib.sha256(old.encode()).hexdigest()}));raise SystemExit(0)
else:
 anchor='    # Grafana Live'
 assert anchor in old
 candidate=old.replace(anchor,ADDITION+anchor,1)
 log_anchor='        access_log /var/log/nginx/deprecation.jsonl deprecation_json if=$deprecation_log_enabled;\n'
 assert log_anchor in candidate
 candidate=candidate.replace(log_anchor,log_anchor+ACCESS,1)
temporary='/tmp/loadtest-gateway-'+STAMP+'.conf'
pathlib.Path(temporary).write_text(candidate)
run('sudo','-n','docker','cp',temporary,container+':'+temporary)
run('sudo','-n','docker','exec',container,'nginx','-t','-c',temporary)
backup=target+'.before-loadtest-'+STAMP
run('sudo','-n','cp','--preserve=all',target,backup)
run('sudo','-n','tee',target,input=candidate.encode())
run('sudo','-n','docker','exec',container,'nginx','-t')
run('sudo','-n','docker','exec',container,'nginx','-s','reload')
print(json.dumps({'status':'enabled','backup':backup,'before_sha256':hashlib.sha256(old.encode()).hexdigest(),
 'after_sha256':hashlib.sha256(candidate.encode()).hexdigest(),'candidate_test':True,'live_test':True,
 'change':'request_diagnostics_only','performance_parameters_changed':False}))
"""
    script = "ADDITION=" + repr(addition) + "\nACCESS=" + repr(access) + "\nSTAMP=" + repr(stamp) + "\n" + script
    result = subprocess.run(["ssh", "-i", str(pathlib.Path.home() / ".ssh/causalagent_staging"),
                             "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                             os.environ.get("LOADTEST_SSH_HOST", "ubuntu@203.195.194.205"), "python3 -"],
                            input=script, capture_output=True, text=True, encoding="utf-8", timeout=90, check=True)
    payload = json.loads(result.stdout)
    payload["collected_at_utc"] = datetime.now(timezone.utc).isoformat()
    payload["local_diagnostic_block_sha256"] = hashlib.sha256(addition.encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
