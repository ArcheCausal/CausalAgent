"""真实 DeepSeek Responses 推理保存、文件工具续接及 PostgreSQL 重连验收。

文档职责：验证父图输入边界和同步、异步 Responses 的真实推理回传。
适用范围：专用 reasoning-postgres 容器；不访问业务数据库或执行领域算法。
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import traceback
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def require(condition, reason):
    if not condition:
        raise AssertionError(reason)


def reasoning_texts(blocks):
    return [part['text'] for block in blocks if isinstance(block, dict) and block.get('type') == 'reasoning'
        for part in (block.get('content') or []) if part.get('type') == 'reasoning_text' and part.get('text')]


def signatures(messages):
    return [hashlib.sha256(text.encode()).hexdigest() for message in messages
        if isinstance(message.content, list) for text in reasoning_texts(message.content)]


async def verify(report):
    import httpx
    from deepagents.backends import StateBackend
    from deepagents.backends.utils import create_file_data
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.memory import InMemoryStore
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from pydantic import ValidationError

    from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
    from Agent.deep_agent.analysis_budget import CLOSEOUT_MODEL_CALL_LIMIT
    from Agent.deep_agent.graph import DeepAgentGraphConfig, build_deep_agent
    from Agent.deep_agent.model import DeepSeekResponsesChatOpenAI
    from Agent.deep_agent.profile import DeepAgentBudget
    from Agent.deep_agent.state import retry_deep_agent_from_checkpoint, to_deep_agent_input
    from Agent.deep_agent_tools.models import DataProfile, FinalAnalysisDecision
    from config.secret_file import require_secret

    model_options = dict(
        model=os.environ.get('DEEP_AGENT_MODEL', 'deepseek-v4-flash'),
        base_url=os.environ.get('DEEP_AGENT_BASE_URL', 'https://api.deepseek.com'),
        api_key=require_secret('DEEP_AGENT_API_KEY'), use_responses_api=True,
        output_version='responses/v1', reasoning={'effort': 'high'},
        streaming=True, max_tokens=8192, max_retries=0, timeout=90,
    )
    report['requests'] = []

    async def capture(request):
        require(len(report['requests']) < 16, 'request_budget_exhausted')
        payload = json.loads(request.content)
        texts = reasoning_texts(payload.get('input') or [])
        output_ids = [item['call_id'] for item in payload.get('input', [])
            if item.get('type') == 'function_call_output']
        calls = [item for item in payload.get('input', []) if item.get('type') == 'function_call']
        pending_calls = [item for item in calls if item['call_id'] not in output_ids]
        require(len(output_ids) == len(set(output_ids)), 'duplicate_tool_output_sent')
        report['requests'].append({
            'reasoning_text_chars': sum(map(len, texts)),
            'tool_outputs': sum(item.get('type') == 'function_call_output' for item in payload.get('input', [])),
            'reasoning_effort': payload.get('reasoning', {}).get('effort'),
            'tool_names': [(tool.get('function') or tool).get('name') for tool in payload.get('tools', [])],
            'last_input_role': (payload.get('input') or [{}])[-1].get('role'),
            'last_input_type': (payload.get('input') or [{}])[-1].get('type'),
            'pending_call_names': [item.get('name') for item in pending_calls],
            'input_protocol': [{key: item.get(key) for key in ('type', 'role', 'name')}
                for item in payload.get('input', [])[-12:]],
        })
        require(not pending_calls, 'pending_function_call_sent_without_output')

    # 同步流式与非流式分别请求真实供应商，不构造替代响应或推理文本。
    sync_model = DeepSeekResponsesChatOpenAI(**model_options)
    sync_reply = await asyncio.to_thread(sync_model.invoke, [HumanMessage(content='计算17乘19，只回复结果。')])
    require(bool(signatures([sync_reply])), 'sync_stream_reasoning_missing')
    report['sync_stream'] = 'PASS'
    nonstream_reply = await asyncio.to_thread(
        sync_model.invoke, [HumanMessage(content='计算17乘19，只回复结果。')], stream=False,
    )
    require(bool(signatures([nonstream_reply])), 'nonstream_reasoning_missing')
    report['nonstream'] = 'PASS'
    payload = sync_model._get_request_payload(
        [HumanMessage(content='计算17乘19。'), sync_reply], store=False,
    )
    require(reasoning_texts(payload['input']) == reasoning_texts(sync_reply.content), 'stateless_reasoning_changed')
    report['stateless_serialization'] = 'PASS'

    @asynccontextmanager
    async def checkpoint():
        async with await AsyncConnection.connect(
            host='reasoning-postgres', user='reasoning_acceptance', dbname='reasoning_acceptance',
            autocommit=True, prepare_threshold=0, row_factory=dict_row,
        ) as connection:
            saver = AsyncPostgresSaver(connection)
            await saver.setup()
            yield saver

    context = AgentRunContext(execution_guard=None, web_search_enabled=False,
        trusted_identity=TrustedJobIdentity(job_id=str(uuid4()), session_id=str(uuid4()),
            user_id=1, attempt_count=1, lease_epoch=1, worker_id='reasoning-acceptance',
            input_identity='no-analysis-dataset'))
    config = {'configurable': {'thread_id': 'reasoning-acceptance:' + str(uuid4())}}
    store = InMemoryStore()
    async with httpx.AsyncClient(timeout=90, trust_env=False, event_hooks={'request': [capture]}) as transport:
        model = DeepSeekResponsesChatOpenAI(**model_options, http_async_client=transport)

        def graph(saver, budget=None):
            return build_deep_agent(model=model, domain_tools=[], backend=StateBackend(), store=store,
                checkpointer=saver, config=DeepAgentGraphConfig(
                    model_name=model_options['model'], context_window_tokens=1000000,
                    budget=budget or DeepAgentBudget(tool_call_limit=1)))

        initial = to_deep_agent_input({
            'messages': [
                HumanMessage(content='先用 read_file 读取 /acceptance/context.txt，收到读取结果后再读取一次该文件。根据文件说明，'
                    '使用 evidence_only 的 FinalAnalysisDecision 收尾，不报告因果算法结果。'),
                AIMessage(content='决策：进入因果分析流程。', name='agent'),
                AIMessage(content='决策：数据预处理完成，进入工具处理路由', name='preprocess'),
            ],
            'data_profile': DataProfile(row_count=0, column_count=0),
        })
        require(not any(isinstance(message, AIMessage) for message in initial['messages']), 'parent_status_leaked')
        initial['files'] = {'/acceptance/context.txt': create_file_data(
            'This acceptance has no analysis dataset and no search tools. Do not report algorithm results.')}
        async with checkpoint() as saver:
            running = graph(saver)
            completed = await asyncio.wait_for(running.ainvoke(deepcopy(initial), config, context=context), timeout=240)
            saved = await running.aget_state(config)
            require(saved.values['thread_tool_call_count']['__all__'] == 1, 'tool_budget_boundary_not_reached')
        require(FinalAnalysisDecision.model_validate(completed['structured_response']).outcome == 'evidence_only',
            'decision_invalid')
        call_ids = {call['id'] for message in completed['messages'] for call in getattr(message, 'tool_calls', [])
            if call['name'] == 'read_file'}
        require(any(isinstance(message, ToolMessage) and message.tool_call_id in call_ids
            and 'no analysis dataset' in str(message.content) for message in completed['messages']), 'real_file_read_missing')
        original = signatures(completed['messages'])
        require(bool(original), 'async_reasoning_missing')
        receipts = [message.tool_call_id for message in completed['messages'] if isinstance(message, ToolMessage)]
        require(len(receipts) == len(set(receipts)), 'duplicate_tool_output_saved')
        report['parent_boundary_and_real_file_tool_roundtrip'] = 'PASS'

        async with checkpoint() as saver:
            rebuilt = graph(saver)
            restored = await rebuilt.aget_state(config)
            require(signatures(restored.values['messages']) == original, 'checkpoint_reasoning_changed')
            remaining = CLOSEOUT_MODEL_CALL_LIMIT - restored.values['closeout_model_call_count']
            requests_before_retry = len(report['requests'])
            retry_input = retry_deep_agent_from_checkpoint(restored.values, retry_instruction=
                '仍然没有分析数据。请再次读取 /acceptance/context.txt，继续用 evidence_only 的 FinalAnalysisDecision 收尾。')
            repeated = await asyncio.wait_for(rebuilt.ainvoke(retry_input, config, context=context), timeout=240)
            retried_state = await rebuilt.aget_state(config)
            require(retried_state.values['thread_tool_call_count']['__all__'] == 1,
                'gate_retry_reopened_exhausted_tool_budget')
            require(retried_state.values['analysis_budget_exhausted'] is True,
                'closeout_phase_not_restored')
            require(retried_state.values['closeout_model_call_count'] <= CLOSEOUT_MODEL_CALL_LIMIT,
                'closeout_model_allowance_exceeded')
        if remaining:
            require(FinalAnalysisDecision.model_validate(repeated['structured_response']).outcome == 'evidence_only',
                'reconnected_decision_invalid')
        else:
            require(repeated.get('structured_response') is None, 'retry_reused_unvalidated_decision')
            require(len(report['requests']) == requests_before_retry, 'retry_exceeded_closeout_allowance')
        report['closeout_model_call_counts'] = [saved.values['closeout_model_call_count'],
            retried_state.values['closeout_model_call_count']]
        require(signatures(repeated['messages'])[:len(original)] == original, 'followup_changed_old_reasoning')
        receipts = [message.tool_call_id for message in repeated['messages'] if isinstance(message, ToolMessage)]
        require(len(receipts) == len(set(receipts)), 'retry_duplicate_tool_output_saved')
        require(any(item['reasoning_text_chars'] and item['tool_outputs'] for item in report['requests']),
            'reasoning_not_replayed_with_tool_output')
        report['postgres_reconnect_and_bounded_gate_retry'] = 'PASS'
        report['tool_budget_boundary_and_gate_retry_input'] = 'PASS'
        report['tool_call_limit'] = 1
        report['thread_tool_call_counts'] = [saved.values['thread_tool_call_count']['__all__'],
            retried_state.values['thread_tool_call_count']['__all__']]

        async with checkpoint() as saver:
            model_limited = graph(saver, DeepAgentBudget(model_call_limit=1, tool_call_limit=8))
            model_budget_config = {'configurable': {'thread_id': 'model-budget:' + str(uuid4())}}
            model_budget_result = await asyncio.wait_for(
                model_limited.ainvoke(deepcopy(initial), model_budget_config, context=context), timeout=240)
            model_budget_snapshot = await model_limited.aget_state(model_budget_config)
            report['model_budget_counts'] = {key: model_budget_snapshot.values.get(key) for key in (
                'analysis_model_call_count', 'closeout_model_call_count', 'analysis_budget_reason',
                'thread_tool_call_count')}
            report['model_budget_messages'] = [
                {'type': message.type, 'status': getattr(message, 'status', None),
                 'tools': [call['name'] for call in getattr(message, 'tool_calls', [])]}
                for message in model_budget_result['messages']]
            report['model_budget_decision_errors'] = []
            for message in model_budget_result['messages']:
                for call in getattr(message, 'tool_calls', []):
                    if call['name'] == 'FinalAnalysisDecision':
                        try:
                            FinalAnalysisDecision.model_validate(call['args'])
                        except ValidationError as exc:
                            report['model_budget_decision_errors'].append([
                                dict(loc=item['loc'], type=item['type'])
                                for item in exc.errors(include_input=False, include_url=False)])
            require(model_budget_result['analysis_budget_reason'] == 'model_calls', 'model_budget_not_closed')
            require(model_budget_result['analysis_model_call_count'] == 1, 'execution_model_budget_exceeded')
            require(model_budget_snapshot.values.get('thread_tool_call_count', {}).get('__all__', 0) == 1,
                'model_closeout_executed_new_tool')
            require(model_budget_result['closeout_model_call_count'] <= CLOSEOUT_MODEL_CALL_LIMIT, 'model_closeout_budget_exceeded')
            require(bool(model_budget_result.get('structured_response')), 'model_limit_lost_decision')
        require(any(item['tool_names'] == ['FinalAnalysisDecision'] for item in report['requests']),
            'closeout_request_still_exposes_execution_tools')
        report['model_budget_closeout_and_schema_isolation'] = 'PASS'

        disabled = DeepSeekResponsesChatOpenAI(**{**model_options, 'reasoning': {'effort': 'none'}},
            http_async_client=transport)
        answer = await disabled.ainvoke([HumanMessage(content='计算17乘19，只回复结果。')])
        require(bool(answer.text), 'nonthinking_text_missing')
        report['nonthinking_stream'] = 'PASS'
    report['status'] = 'PASS'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    report = {'status': 'FAIL', 'started_at': datetime.now(timezone.utc).isoformat(),
        'limitations': ['dedicated PostgreSQL; real file tool and provider; no business MySQL, MCP, RAG, sandbox or full Job']}
    try:
        asyncio.run(verify(report))
    except Exception as exc:
        report['failure_type'] = type(exc).__name__
        report['failure_frames'] = [f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}'
            for frame in traceback.extract_tb(exc.__traceback__)[-5:]]
        if callable(getattr(exc, 'errors', None)):
            report['validation_errors'] = [dict(loc=item['loc'], type=item['type'])
                for item in exc.errors(include_input=False, include_url=False)]
        report['http_status'] = getattr(exc, 'status_code', None)
        error_text = str(exc).lower()
        report['failure_categories'] = [term for term in (
            'last message', 'reasoning', 'tool', 'function', 'call_id', 'must', 'missing',
            'corresponding', 'following', 'not found', 'invalid', 'arguments', 'response')
            if term in error_text]
        if isinstance(exc, AssertionError):
            report['failure_reason'] = str(exc)
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        with args.report.open('x', encoding='utf-8') as output:
            json.dump(report, output, indent=2)
        print(json.dumps(report), flush=True)
    raise SystemExit(0 if report['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
