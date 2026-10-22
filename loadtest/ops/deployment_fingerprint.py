"""从真实容器读取受控部署标识，绝不输出完整环境变量或连接串。"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
from datetime import datetime, timezone

# 远端只读执行；docker inspect 的未筛选结果只存在于远端进程内存。
REMOTE_SCRIPT = r'''
import hashlib,json,subprocess
names=['app','worker','causal-mcp','gateway']
runtime_keys={'WEB_WORKERS','WEB_THREADS','JOB_WORKERS','CAUSAL_MCP_PROCESS_WORKERS',
 'CAUSAL_MCP_MAX_PENDING','CAUSAL_MCP_MAX_INFLIGHT','CAUSAL_MCP_POOL_SIZE',
 'CAUSAL_MCP_POOL_ACQUIRE_TIMEOUT_SECONDS','CAUSAL_MCP_HTTP_READ_TIMEOUT_SECONDS',
 'CAUSAL_MCP_ENQUEUE_TIMEOUT_SECONDS','CAUSAL_MCP_QUEUE_CAPACITY','CAUSAL_MCP_ALGORITHM_DEADLINE_SECONDS',
 'CAUSAL_MCP_CLIENT_POOL_SIZE','CAUSAL_MCP_MAX_IN_FLIGHT_PER_CLIENT','CAUSAL_MCP_CONTROL_POOL_SIZE',
 'CAUSAL_MCP_CONTROL_MAX_IN_FLIGHT_PER_CLIENT','CAUSAL_MCP_CONTROL_ACQUIRE_TIMEOUT_SECONDS',
 'CAUSAL_MCP_CANCEL_TIMEOUT_SECONDS','CAUSAL_MCP_HTTP_MAX_CONNECTIONS','CAUSAL_MCP_HTTP_MAX_KEEPALIVE_CONNECTIONS',
 'DEEP_AGENT_CONTEXT_WINDOW_TOKENS','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'}
model_keys={'MODEL','DEEP_AGENT_MODEL','VISION_MODEL','EMBEDDING_MODEL','CDFM_MODEL_PATH'}
out={'images':{},'runtime':{},'model_ids':{}}
for name in names:
 container='causalagent-staging-'+name+'-1'
 data=json.loads(subprocess.check_output(['sudo','-n','docker','inspect',container]))[0]
 image=json.loads(subprocess.check_output(['sudo','-n','docker','image','inspect',data['Image']]))[0]
 labels=image.get('Config',{}).get('Labels') or {}
 out['images'][name]={'image_id':data['Image'],'repo_digests':image.get('RepoDigests'),
  'revision':labels.get('org.opencontainers.image.revision')}
 env=dict(item.split('=',1) for item in data.get('Config',{}).get('Env',[]) if '=' in item)
 out['runtime'][name]={key:env.get(key) for key in sorted(runtime_keys)}
 out['model_ids'][name]={key:env.get(key) for key in sorted(model_keys)}
 if name=='gateway':
  cfg=subprocess.check_output(['sudo','-n','docker','exec',container,'cat','/etc/nginx/nginx.conf'])
  out['gateway_config_sha256']=hashlib.sha256(cfg).hexdigest()
print(json.dumps(out))
'''


def capture_fingerprint() -> dict:
    process = subprocess.run(["ssh", "-i", str(pathlib.Path.home() / ".ssh" / "causalagent_staging"),
                              "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                              os.environ.get("LOADTEST_SSH_HOST", "ubuntu@203.195.194.205"), "python3 -"],
                             input=REMOTE_SCRIPT, capture_output=True, text=True, encoding="utf-8", timeout=90, check=True)
    return json.loads(process.stdout)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    payload = {"collected_at_utc": datetime.now(timezone.utc).isoformat(), "deployment": capture_fingerprint()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    print(f"部署标识已保存：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
