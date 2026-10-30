"""真实思考模型的工具续接与 PostgreSQL 恢复验收。

职责：检查生产参数工具及因果 Deep Agent 对 Responses 推理历史的处理。
范围：仅使用专用验收数据库与私有模型配置，不执行沙盒、搜索或因果算法。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
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


async def verify(report, model_config):
    import httpx
    from deepagents.backends import StateBackend
    from deepagents.backends.utils import create_file_data
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from Agent.deep_agent.model import DeepSeekResponsesChatOpenAI
    from langgraph.graph import START, END, StateGraph
    from langgraph.prebuilt import ToolNode
    from langgraph.store.memory import InMemoryStore
    from langgraph.types import Command, interrupt
    from Agent.causal_agent.postgres_checkpointer import open_checkpoint_pool, build_checkpointer
    from Agent.data_processing.agent import _research_spec_tool, data_processing_system_prompt
    from Agent.data_processing.persistence import read_research_inputs
    from Agent.data_processing.validation import _inspect_csv_shape
    from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
    from Agent.deep_agent.graph import build_deep_agent, DeepAgentGraphConfig
    from Agent.deep_agent.state import ProjectDeepAgentState, initial_deep_agent_state
    from Agent.deep_agent_tools.models import DataProfile, FinalAnalysisDecision
    from app.agent import job_service
    from app.auth.service import find_user
    from tests.acceptance.data_processing_recovery import application, context_for, identity_for, require

    configuration = json.loads(model_config.read_text())
    require(configuration['reasoning']['effort'] == 'high', 'high_reasoning_required')
    report['requests'] = []

    async def capture(request):
        require(len(report['requests']) < 24, 'model_request_budget_exhausted')
        payload = json.loads(request.content)
        report['requests'].append({
            'reasoning_effort': (payload.get('reasoning') or {}).get('effort'),
            'reasoning_history_items': sum(item.get('type') == 'reasoning'
                for item in payload.get('input', []) if isinstance(item, dict)),
            'reasoning_history_text_chars': sum(len(part.get('text') or '')
                for item in payload.get('input', []) if isinstance(item, dict) and item.get('type') == 'reasoning'
                for part in (item.get('content') or []) if part.get('type') == 'reasoning_text'),
            'tool_output_items': sum(item.get('type') == 'function_call_output'
                for item in payload.get('input', []) if isinstance(item, dict)),
        })

    client = application().test_client()
    username, password = 'thinking_' + uuid4().hex[:12], secrets.token_urlsafe(24) + '9'
    require(client.post('/api/register', json={'username': username, 'password': password}).status_code == 200,
        'registration_failed')
    require(client.post('/api/login', json={'username': username, 'password': password}).status_code == 200,
        'login_failed')
    user_id = find_user(username)['id']
    session_id = client.post('/api/new_chat').json['new_session_id']
    source = b'x,y\n1,2\n2,4\n3,7\n'
    upload = client.post('/api/upload_file', data={'file': (BytesIO(source), 'thinking.csv', 'text/csv')})
    require(upload.status_code == 200 and upload.json['success'], 'upload_failed')
    created, _ = job_service.create_job(user_id, session_id,
        '目标变量 y，干预变量 x；每行是一次观测，全部输入列为连续测量。', str(uuid4()),
        input_user_file_id=upload.json['user_file_id'], request_id=str(uuid4()))
    job = job_service.claim_next_job('reasoning-recovery-worker')
    require(job and job['job_id'] == created['job_id'], 'job_claim_failed')
    identity = identity_for(job)
    try:
        async with httpx.AsyncClient(timeout=90, trust_env=False, event_hooks={'request': [capture]}) as transport:
            model = DeepSeekResponsesChatOpenAI(**configuration, streaming=True, max_tokens=8192, max_retries=0,
                timeout=90, http_async_client=transport)
            research_tool = _research_spec_tool()

            async def draft(state):
                response = await model.bind_tools([research_tool], tool_choice='set_research_spec').ainvoke(state['messages'])
                require(len(response.tool_calls) == 1, 'research_tool_call_required')
                return {'messages': [response]}

            def pause(state):
                require(state.get('data_processing_research_spec_ready'), 'research_spec_not_ready')
                require(interrupt({'kind': 'acceptance_checkpoint_boundary'}) == 'continue', 'unexpected_resume')
                return {}

            async def finish(state):
                response = await model.bind_tools([research_tool], tool_choice='none').ainvoke([
                    *state['messages'], HumanMessage(content='依据刚才的工具成功结果，简短说明参数已经保存。不要调用工具。')])
                require(not response.tool_calls and bool(response.content), 'tool_roundtrip_not_completed')
                return {'messages': [response]}

            builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
            builder.add_node('draft', draft)
            builder.add_node('tools', ToolNode([research_tool], handle_tool_errors=False))
            builder.add_node('pause', pause)
            builder.add_node('finish', finish)
            for start, end in ((START, 'draft'), ('draft', 'tools'), ('tools', 'pause'),
                               ('pause', 'finish'), ('finish', END)):
                builder.add_edge(start, end)
            inputs = read_research_inputs(identity)
            initial = inputs[0]
            columns, _ = _inspect_csv_shape(source, max_rows=100, max_columns=10, max_cells=1000)
            config = {'configurable': {'thread_id': job['job_id'] + ':thinking-parameters'}}
            state = {'analysis_parameters': {}, 'analysis_parameter_changes': [],
                'data_processing_input_columns': columns,
                'data_processing_input_sources': {str(initial['source_input_id']): initial},
                'data_processing_pending_input_id': initial['source_input_id'],
                'data_processing_research_spec_ready': False,
                'data_processing_semantic_identity': job['input_file_hash'],
                'messages': [SystemMessage(content=data_processing_system_prompt()),
                    HumanMessage(content=json.dumps({'source': initial, 'input_columns': columns,
                        'expected_revision': 0, 'reason': 'initial'}, ensure_ascii=False))]}
            async with open_checkpoint_pool() as pool:
                graph = builder.compile(checkpointer=build_checkpointer(pool))
                suspended = await graph.ainvoke(state, config, context=context_for(identity))
                require(bool(suspended.get('__interrupt__')), 'native_interrupt_missing')
            async with open_checkpoint_pool() as pool:
                graph = builder.compile(checkpointer=build_checkpointer(pool))
                restored = await graph.aget_state(config)
                require(restored.values['analysis_parameters']['target'] == 'y', 'parameters_not_restored')
                completed = await graph.ainvoke(Command(resume='continue'), config, context=context_for(identity))
                require(completed['analysis_parameters']['treatment'] == 'x', 'treatment_mismatch')
                require(len(completed['analysis_parameter_changes']) == 1, 'resume_duplicated_spec_commit')
                require(any(isinstance(item, ToolMessage) and item.name == 'set_research_spec'
                    for item in completed['messages']), 'actual_spec_tool_result_missing')
            report['parameter_tool_checkpoint_roundtrip'] = 'PASS'

            def causal_graph(saver):
                return build_deep_agent(model=model, domain_tools=[], backend=StateBackend(),
                    store=InMemoryStore(), checkpointer=saver,
                    config=DeepAgentGraphConfig(model_name=configuration['model'], context_window_tokens=1000000))

            causal_config = {'configurable': {'thread_id': job['job_id'] + ':thinking-causal'}}
            context = AgentRunContext(execution_guard=None, web_search_enabled=False,
                trusted_identity=TrustedJobIdentity(job_id=job['job_id'], session_id=session_id,
                    user_id=user_id, attempt_count=job['attempt_count'], lease_epoch=job['lease_epoch'],
                    worker_id=job['worker_id'], input_identity='no-dataset-thinking-compatibility'))
            async with open_checkpoint_pool() as pool:
                graph = causal_graph(build_checkpointer(pool))
                causal_initial = initial_deep_agent_state(
                    data_profile=DataProfile(row_count=0, column_count=0),
                    messages=[HumanMessage(content='先调用 read_file 读取 /acceptance/context.txt，依据这个文件说明的情况，'
                        '简短说明缺少数据时不能报告因果发现结果，使用 evidence_only 的 FinalAnalysisDecision 收尾。')])
                causal_initial['files'] = {'/acceptance/context.txt': create_file_data(
                    'This acceptance has no analysis dataset and no search tools. Do not report algorithm results.')}
                result = await asyncio.wait_for(graph.ainvoke(causal_initial,
                    causal_config, context=context), timeout=240)
                require(FinalAnalysisDecision.model_validate(result['structured_response']).outcome == 'evidence_only',
                    'causal_decision_mismatch')
                read_ids = {call['id'] for item in result['messages'] for call in getattr(item, 'tool_calls', [])
                    if call['name'] == 'read_file'}
                require(any(isinstance(item, ToolMessage) and item.tool_call_id in read_ids
                    and 'no analysis dataset' in str(item.content) for item in result['messages']),
                    'actual_causal_tool_result_missing')
            async with open_checkpoint_pool() as pool:
                graph = causal_graph(build_checkpointer(pool))
                restored = await graph.aget_state(causal_config)
                require(bool(restored.values.get('structured_response')), 'causal_decision_not_restored')
                repeated = await asyncio.wait_for(graph.ainvoke({'messages': [HumanMessage(content=
                    '仍然没有数据，请保留这个判断，用 evidence_only 的 FinalAnalysisDecision 简短收尾。')]},
                    causal_config, context=context), timeout=240)
                require(FinalAnalysisDecision.model_validate(repeated['structured_response']).outcome == 'evidence_only',
                    'causal_reconnected_decision_mismatch')
            report['causal_agent_tools_and_reconnected_followup'] = 'PASS'
            require(all(item['reasoning_effort'] == 'high' for item in report['requests']), 'thinking_not_enabled')
            require(any(item['reasoning_history_text_chars'] and item['tool_output_items'] for item in report['requests']),
                'reasoning_history_not_sent_with_tool_results')
            report['reasoning_history_with_tool_results'] = 'PASS'
        report['status'] = 'PASS'
    finally:
        latest = job_service.get_job_by_id(job['job_id'])
        if latest and latest['status'] in {'queued', 'running', 'waiting_input'}:
            job_service.cancel_job(user_id, job['job_id'], str(uuid4()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-config', type=Path, default=Path('/tmp/research-parameter-model-high.json'))
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    from tests.acceptance.data_processing_recovery import ARTIFACTS, configure
    report = {'status': 'FAIL', 'started_at': datetime.now(timezone.utc).isoformat(),
        'limitations': ['explicit test inputs; no autonomous sandbox execution',
            'causal agent has no dataset or domain tools; no algorithm, MCP HTTP, search or full analysis validation',
            'parameter tool uses actual Job ledger and PostgreSQL interrupt/reconnect; no model substitute']}
    try:
        configure(resume=True)
        asyncio.run(verify(report, args.model_config))
    except Exception as exc:
        report['failure_type'] = type(exc).__name__
        report['http_status'] = getattr(exc, 'status_code', None)
        if isinstance(exc, AssertionError):
            report['failure_reason'] = str(exc)
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        path = ARTIFACTS / ('reasoning-recovery-report-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '.json')
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            json.dump(report, output, indent=2)
        print(json.dumps(report), flush=True)
    raise SystemExit(0 if report['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
