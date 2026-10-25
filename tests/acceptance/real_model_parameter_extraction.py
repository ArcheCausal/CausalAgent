"""受预算限制的真实模型参数提取验收，人工独立入口。

职责：复用隔离 MySQL/PostgreSQL 和两个原工具入口，验证模型真实成功提取。
范围：仅使用 data_processing_recovery 专用配置；模型配置单独从容器私有 JSON 读取，
不得加载根 .env 的数据库配置。报告不包含凭据、端点、模型配置值或答复正文。
运行：python tests/acceptance/real_model_parameter_extraction.py --max-model-requests 4
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import secrets
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


async def verify(report, args):
    import httpx
    from langchain_openai import ChatOpenAI
    from tests.acceptance.data_processing_recovery import application, require
    from tests.acceptance.research_parameter_tool_sources import verify_tool_sources
    from app.agent import job_service
    from app.auth.service import find_user

    configuration = json.loads(args.model_config.read_text(encoding='utf-8-sig'))
    require(set(configuration) == {'model', 'base_url', 'api_key', 'use_responses_api', 'output_version', 'reasoning'},
        'model_only_configuration_required')
    require(all(isinstance(configuration[key], str) and configuration[key] for key in ('model', 'base_url', 'api_key')),
        'model_configuration_incomplete')
    report['model_http_requests'] = 0

    async def count_request(request):
        if report['model_http_requests'] >= args.max_model_requests:
            raise RuntimeError('model_request_budget_exhausted')
        report['model_http_requests'] += 1

    client = application().test_client()
    username, password = 'real_parameters_' + uuid4().hex[:10], secrets.token_urlsafe(24) + '9'
    require(client.post('/api/register', json={'username': username, 'password': password}).status_code == 200, 'registration_failed')
    require(client.post('/api/login', json={'username': username, 'password': password}).status_code == 200, 'login_failed')
    user_id = find_user(username)['id']
    session_id = client.post('/api/new_chat').json['new_session_id']
    source = ('x,y\n' + '\n'.join(f'{i / 7},{(i * 13) % 29}' for i in range(120)) + '\n').encode()
    upload = client.post('/api/upload_file', data={'file': (BytesIO(source), 'real-parameters.csv', 'text/csv')})
    require(upload.status_code == 200 and upload.json['success'], 'upload_failed')
    created_ids = []

    def new_job():
        created, replay = job_service.create_job(user_id, session_id, 'Isolated real parameter extraction acceptance.',
            str(uuid4()), input_user_file_id=upload.json['user_file_id'], request_id=str(uuid4()))
        created_ids.append(created['job_id'])
        require(not replay, 'unexpected_creation_replay')
        job = job_service.claim_next_job('real-parameter-worker')
        require(job and job['job_id'] == created['job_id'], 'initial_claim_failed')
        return job

    try:
        async with httpx.AsyncClient(timeout=45, trust_env=False, event_hooks={'request': [count_request]}) as http_client:
            model = ChatOpenAI(**configuration, streaming=False, max_tokens=1200, max_retries=0,
                timeout=45, http_async_client=http_client)
            await asyncio.wait_for(verify_tool_sources(report, new_job, model=model, real_model=True), timeout=150)
        report['status'] = 'PASS'
    finally:
        # Only the Jobs created by this invocation may be canceled on failure.
        for job_id in created_ids:
            job = job_service.get_job_by_id(job_id)
            if job and job['status'] in {'queued', 'running', 'waiting_input'}:
                job_service.cancel_job(user_id, job_id, str(uuid4()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-config', type=Path, default=Path('/tmp/research-parameter-model.json'))
    parser.add_argument('--max-model-requests', type=int, choices=(2, 3, 4), default=4)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    from tests.acceptance.data_processing_recovery import ARTIFACTS, configure, require
    report = {'status': 'FAIL', 'started_at': datetime.now(timezone.utc).isoformat(), 'model_http_requests': 0,
        'model_http_request_limit': args.max_model_requests, 'max_output_tokens_per_request': 1200,
        'credential_scope': 'model credentials only; application secrets and databases remain isolated',
        'limitations': ['tool calls, questions and user answers are explicit test inputs',
            'real model performs parameter extraction only; no autonomous planning',
            'real MySQL/PostgreSQL and production graph runner/event writer; no sandbox execution',
            'no model credentials, configuration values or answer bodies in public reports']}
    sources = ('tests/acceptance/real_model_parameter_extraction.py', 'tests/acceptance/research_parameter_tool_sources.py',
        'Agent/data_processing/agent.py', 'Agent/data_processing/persistence.py', 'Agent/data_processing/parameters.py')
    report['source_sha256'] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}
    try:
        configure(resume=True)
        require(os.environ.get('MYSQL_HOST') == 'recovery-mysql'
            and os.environ.get('MYSQL_WRITE_HOST') == 'recovery-mysql'
            and not os.environ.get('MYSQL_READ_HOSTS')
            and os.environ.get('CHECKPOINT_POSTGRES_HOST') == 'recovery-postgres', 'isolated_database_configuration_required')
        asyncio.run(verify(report, args))
    except Exception as exc:
        from app.agent.worker.event_adapter import _failure_chain
        report['failure_types'] = list(dict.fromkeys(type(cause).__name__ for cause in _failure_chain(exc)))
        report['failure_reason'] = str(exc) if isinstance(exc, AssertionError) else 'sanitized exception types only'
    finally:
        report['source_unchanged_during_run'] = all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
            for name, digest in report['source_sha256'].items())
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        path = ARTIFACTS / ('real-model-report-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '.json')
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(report, output, indent=2)
        print(json.dumps(report), flush=True)
    raise SystemExit(0 if report['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
