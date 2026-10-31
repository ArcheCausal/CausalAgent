"""受预算限制的真实模型参数提取验收，人工独立入口。

职责：验证初始提取和两个原工具入口，并重复统计研究角色语义场景。
范围：仅使用 data_processing_recovery 专用配置；模型配置单独从容器私有 JSON 读取，
不得加载根 .env 的数据库配置。报告不包含凭据、端点或答复正文。
运行：python tests/acceptance/real_model_parameter_extraction.py --repetitions 2 --max-model-requests 144
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


SCENARIOS = (
    {'id': 'all_input_measurements', 'question': '这些字段表示什么测量？',
     'answer': '观察单位保持不变，全部11列都是连续测量值。',
     'parameters': {'target': None, 'treatment': None}, 'expected': {},
     'expected_measurement_kind': 'continuous',
     'initial': '每行是一件工件的一次测量，全部11列都是连续测量值，用所有列探索因果结构。',
     'initial_roles': (None, None)},
    {'id': 'original_all_nodes', 'question': '本次目标或处理变量是什么？',
     'answer': '全部作为节点参与因果发现', 'parameters': {'target': None, 'treatment': None},
     'expected': {}, 'intent_updates': ['analysis_question', 'aggregation_scope'],
     'initial': '全部作为节点参与因果发现', 'initial_roles': (None, None)},
    {'id': 'scope_paraphrase', 'question': '哪些变量参与分析？',
     'answer': '用所有字段探索因果结构，运行PC算法，保留全部原始列。',
     'parameters': {'target': None, 'treatment': None}, 'expected': {},
     'intent_updates': ['analysis_question', 'aggregation_scope'],
     'initial': '用所有字段探索因果结构，运行PC算法，保留全部原始列。', 'initial_roles': (None, None)},
    {'id': 'scope_preserves_roles', 'question': '哪些变量参与分析？',
     'answer': '全部作为节点参与因果发现', 'expected': {},
     'intent_updates': ['analysis_question', 'aggregation_scope'],
     'initial': '目标是收入，干预变量是培训，全部作为节点参与因果发现。', 'initial_roles': ('收入', '培训')},
    {'id': 'explicit_named_roles', 'question': '目标和处理变量是什么？',
     'answer': '目标是收入，干预变量是培训。', 'expected': {'target': '收入', 'treatment': '培训'},
     'intent_updates': ['analysis_question'],
     'initial': '目标是收入，干预变量是培训。', 'initial_roles': ('收入', '培训')},
    {'id': 'explicit_target_cancel', 'question': '需要如何修改研究参数？',
     'answer': '不指定目标变量。', 'expected': {'target': None},
     'intent_updates': ['analysis_question'],
     'initial': '不指定目标变量。', 'initial_roles': (None, None)},
    {'id': 'cancel_both_roles', 'question': '需要如何修改研究参数？',
     'answer': '取消之前的目标和干预变量，只做无目标的结构发现。',
     'expected': {'target': None, 'treatment': None},
     'intent_updates': ['analysis_question', 'aggregation_scope'],
     'initial': '之前的目标是收入，干预变量是培训。现在取消目标和干预变量，只做无目标的结构发现。',
     'initial_roles': (None, None)},
    {'id': 'unmentioned_roles', 'question': '每行的观察单位是什么？',
     'answer': '观察单位改为device-day。', 'expected': {'observation_unit': 'device-day'},
     'initial': '请检查数据并按每台设备每天组织观测。', 'initial_roles': (None, None)},
    {'id': 'explicit_treatment_cancel', 'question': '需要如何修改研究参数？',
     'answer': '不指定干预变量。', 'expected': {'treatment': None},
     'intent_updates': ['analysis_question'],
     'initial': '目标是收入，不指定干预变量。', 'initial_roles': ('收入', None)},
    {'id': 'proposal_full_confirmation',
     'question': '当前方案提议 A：每个设备一行观测；B：每天一个时间窗口。请确认或修改。',
     'answer': 'A 默认，B 默认',
     'expected': {'observation_unit': '每个设备一行观测', 'time_window': '每天'},
     'intent_updates': ['analysis_question', 'aggregation_scope'],
     'initial': '目标是收入，干预变量是培训。', 'initial_roles': ('收入', '培训'),
     'parameters': {'observation_unit': '一行一个观测', 'time_window': '每周'},
     'plan_metadata': {'summary': 'A 默认按设备聚合为每个设备一行观测；B 默认按每天一个时间窗口。',
         'observation_unit': '每个设备一行观测', 'transformations': ['按每天聚合'],
         'variable_semantics': {'device': '设备标识', 'value': '测量值'},
         'limitations': ['每条记录需要包含设备和时间。']}},
    {'id': 'proposal_partial_confirmation',
     'question': '当前方案提议 A：每个设备一行观测；B：每天一个时间窗口。请确认或修改。',
     'answer': 'A 默认',
     'expected': {'observation_unit': '每个设备一行观测'},
     'intent_updates': ['analysis_question', 'aggregation_scope'],
     'initial': '目标是收入，干预变量是培训。', 'initial_roles': ('收入', '培训'),
     'parameters': {'observation_unit': '一行一个观测', 'time_window': '每周'},
     'plan_metadata': {'summary': 'A 默认按设备聚合为每个设备一行观测；B 默认按每天一个时间窗口。',
         'observation_unit': '每个设备一行观测', 'transformations': ['按每天聚合'],
         'variable_semantics': {'device': '设备标识', 'value': '测量值'},
         'limitations': ['每条记录需要包含设备和时间。']}},
    {'id': 'proposal_partial_modification',
     'question': '当前方案提议 A：每个设备一行观测；B：每天一个时间窗口。请确认或修改。',
     'answer': 'A 默认，但 B 改为每周',
     'expected': {'observation_unit': '每个设备一行观测', 'time_window': '每周'},
     'intent_updates': ['analysis_question', 'aggregation_scope'],
     'initial': '目标是收入，干预变量是培训。', 'initial_roles': ('收入', '培训'),
     'parameters': {'observation_unit': '一行一个观测', 'time_window': '每天'},
     'plan_metadata': {'summary': 'A 默认按设备聚合为每个设备一行观测；B 默认按每天一个时间窗口。',
         'observation_unit': '每个设备一行观测', 'transformations': ['按每天聚合'],
         'variable_semantics': {'device': '设备标识', 'value': '测量值'},
         'limitations': ['每条记录需要包含设备和时间。']}},
    {'id': 'proposal_paraphrase_confirmation',
     'question': '当前方案提议 A：每个设备一行观测；B：每天一个时间窗口。请确认或修改。',
     'answer': '接受按设备聚合，并按每天汇总；其它保持不变。',
     'expected': {'observation_unit': '每个设备一行观测', 'time_window': '每天'},
     'intent_updates': ['analysis_question', 'aggregation_scope'],
     'initial': '目标是收入，干预变量是培训。', 'initial_roles': ('收入', '培训'),
     'parameters': {'observation_unit': '一行一个观测', 'time_window': '每周'},
     'plan_metadata': {'summary': 'A 默认按设备聚合为每个设备一行观测；B 默认按每天一个时间窗口。',
         'observation_unit': '每个设备一行观测', 'transformations': ['按每天聚合'],
         'variable_semantics': {'device': '设备标识', 'value': '测量值'},
         'limitations': ['每条记录需要包含设备和时间。']}},
)


async def verify(report, args):
    import httpx
    from langchain_openai import ChatOpenAI
    from tests.acceptance.data_processing_recovery import application, require
    from tests.acceptance.research_parameter_tool_sources import verify_tool_sources
    from app.agent import job_service
    from app.auth.service import find_user
    from langchain_core.messages import HumanMessage, SystemMessage
    from Agent.data_processing.parameters import ResearchSpecInput, resolve_research_spec
    from Agent.data_processing.agent import _research_spec_tool, data_processing_system_prompt
    from Agent.deep_agent.model import DeepSeekResponsesChatOpenAI

    configuration = json.loads(args.model_config.read_text(encoding='utf-8-sig'))
    require(set(configuration) == {'model', 'base_url', 'api_key', 'use_responses_api', 'output_version', 'reasoning'},
        'model_only_configuration_required')
    require(all(isinstance(configuration[key], str) and configuration[key] for key in ('model', 'base_url', 'api_key')),
        'model_configuration_incomplete')
    initial_configuration = (json.loads(args.initial_model_config.read_text(encoding='utf-8-sig'))
        if args.initial_model_config else configuration)
    require(set(initial_configuration) == set(configuration), 'initial_model_only_configuration_required')
    require(all(isinstance(initial_configuration[key], str) and initial_configuration[key]
        for key in ('model', 'base_url', 'api_key')), 'initial_model_configuration_incomplete')
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
    input_columns = ['x', 'y', *[f'c{index}' for index in range(3, 12)]]
    source = (','.join(input_columns) + '\n' + '\n'.join(
        ','.join(str((i * (index + 1) + index) / 7) for index in range(11))
        for i in range(120)) + '\n').encode()
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
        async with httpx.AsyncClient(timeout=args.request_timeout, trust_env=False, event_hooks={'request': [count_request]}) as http_client:
            model_class = DeepSeekResponsesChatOpenAI if configuration['model'].startswith('deepseek-') else ChatOpenAI
            initial_model_class = DeepSeekResponsesChatOpenAI if initial_configuration['model'].startswith('deepseek-') else ChatOpenAI
            model = model_class(**configuration, streaming=False, max_tokens=args.max_output_tokens, max_retries=0,
                timeout=args.request_timeout, http_async_client=http_client)
            initial_model = initial_model_class(**initial_configuration, streaming=False, max_tokens=args.max_output_tokens, max_retries=0,
                timeout=args.request_timeout, http_async_client=http_client)
            report['reasoning_effort'] = configuration['reasoning']['effort']
            report['cases'] = []
            for scenario in SCENARIOS:
                if args.scenario and scenario['id'] not in args.scenario:
                    continue
                for repetition in range(1, args.repetitions + 1):
                    initial_case = {'scenario': scenario['id'], 'entry': 'initial', 'repetition': repetition, 'status': 'FAIL'}
                    report['cases'].append(initial_case)
                    try:
                        schema_tool = _research_spec_tool()
                        initial_messages = [
                            SystemMessage(content=data_processing_system_prompt()),
                            HumanMessage(content='你是数据处理 Agent。根据本次真实需求提交完整初始研究参数；没有指定的角色为null，不能把分析范围当作角色。冻结原始列为' + json.dumps(input_columns) + '。可信 source_input_id=1，expected_revision=0，reason=initial。需求：' + scenario['initial'])]
                        for attempt in range(3):
                            initial_case['model_attempts'] = attempt + 1
                            response = await initial_model.bind_tools([schema_tool], tool_choice='auto').ainvoke(initial_messages)
                            if response.tool_calls:
                                break
                            initial_messages.extend([response, HumanMessage(content=
                                '初始研究参数尚未提交。请调用 set_research_spec 提交完整草稿，不能以自然语言回复代替提交。')])
                        require(len(response.tool_calls) == 1, 'initial_tool_call_required')
                        payload = ResearchSpecInput.model_validate(response.tool_calls[0]['args'])
                        extracted = resolve_research_spec(payload.spec, input_columns=input_columns)
                        require((extracted['target'], extracted['treatment']) == scenario['initial_roles'], 'initial_roles_mismatch')
                        if scenario.get('expected_measurement_kind'):
                            require({item['column']: item['kind'] for item in extracted['measurement_semantics']}
                                == dict.fromkeys(input_columns, scenario['expected_measurement_kind']), 'initial_measurement_scope_mismatch')
                        initial_case['status'] = 'PASS'
                    except Exception as exc:
                        initial_case['failure_type'] = type(exc).__name__
                        initial_case['http_status'] = getattr(exc, 'status_code', None)
                        if isinstance(exc, AssertionError):
                            initial_case['failure_reason'] = str(exc)
                    for entry in ('ask_data_processing_question', 'request_data_processing_plan_confirmation'):
                        tool_report = {}
                        case = {'scenario': scenario['id'], 'entry': entry, 'repetition': repetition,
                            'status': 'FAIL'}
                        try:
                            await asyncio.wait_for(verify_tool_sources(tool_report, new_job,
                                model=model, real_model=True, scenario=scenario, tool_name=entry), timeout=args.request_timeout * 3 + 30)
                            require(entry in tool_report.get('real_model_completed_tools', []), 'tool_not_completed')
                            case['status'] = 'PASS'
                        except Exception as exc:
                            case['failure_type'] = type(exc).__name__
                            case['http_status'] = getattr(exc, 'status_code', None)
                            if isinstance(exc, AssertionError):
                                case['failure_reason'] = str(exc)
                        report['cases'].append(case)
                        for job_id in created_ids:
                            job = job_service.get_job_by_id(job_id)
                            if job and job['status'] in {'queued', 'running', 'waiting_input'}:
                                job_service.cancel_job(user_id, job_id, str(uuid4()))
                    print(json.dumps({'phase': 'scenario_completed', 'scenario': scenario['id'],
                        'repetition': repetition, 'passed': sum(case['status'] == 'PASS' for case in report['cases']),
                        'attempted': len(report['cases'])}), flush=True)
            report['counts'] = {status: sum(case['status'] == status for case in report['cases'])
                for status in ('PASS', 'FAIL')}
            require(report['counts']['FAIL'] == 0, 'real_model_semantic_cases_failed')
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
    parser.add_argument('--initial-model-config', type=Path)
    parser.add_argument('--max-output-tokens', type=int, choices=range(1200, 32769), default=8192,
        help='输出上限同时包含推理与可见输出；开关对照应使用相同上限。')
    parser.add_argument('--request-timeout', type=int, choices=range(30, 181), default=90)
    parser.add_argument('--max-model-requests', type=int, choices=range(1, 385), default=144)
    parser.add_argument('--repetitions', type=int, choices=range(1, 5), default=2)
    parser.add_argument('--scenario', action='append', choices=[item['id'] for item in SCENARIOS],
        help='仅运行指定场景；可重复指定。默认运行全部场景。')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    from tests.acceptance.data_processing_recovery import ARTIFACTS, configure, require
    report = {'status': 'FAIL', 'started_at': datetime.now(timezone.utc).isoformat(), 'model_http_requests': 0,
        'model_http_request_limit': args.max_model_requests, 'max_output_tokens_per_request': args.max_output_tokens,
        'request_timeout_seconds': args.request_timeout,
        'repetitions': args.repetitions, 'expected_case_count': sum(
            not args.scenario or item['id'] in args.scenario for item in SCENARIOS) * args.repetitions * 3,
        'separate_initial_model_config': bool(args.initial_model_config),
        'credential_scope': 'model credentials only; application secrets and databases remain isolated',
        'limitations': ['tool calls, questions and user answers are explicit test inputs',
            'initial cases verify real model tool arguments and deterministic resolution; source id is explicit test input',
            'answer cases execute the actual unified parameter tool against isolated databases; no autonomous planning',
            'real MySQL/PostgreSQL and production graph runner/event writer; no sandbox execution',
            'no model credentials, endpoints or answer bodies in public reports']}
    sources = ('tests/acceptance/real_model_parameter_extraction.py', 'tests/acceptance/research_parameter_tool_sources.py',
        'Agent/causal_agent/nodes.py',
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
