"""真实 DeepSeek Responses 推理保存、文件工具续接及 PostgreSQL 重连验收。

文档职责：验证父图输入边界和同步、异步 Responses 的真实推理回传。
适用范围：专用 reasoning-postgres 容器；不访问业务数据库或执行领域算法。
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
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

    from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
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
        require(len(output_ids) == len(set(output_ids)), 'duplicate_tool_output_sent')
        report['requests'].append({
            'reasoning_text_chars': sum(map(len, texts)),
            'tool_outputs': sum(item.get('type') == 'function_call_output' for item in payload.get('input', [])),
            'reasoning_effort': payload.get('reasoning', {}).get('effort'),
        })

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

        def graph(saver):
            return build_deep_agent(model=model, domain_tools=[], backend=StateBackend(), store=store,
                checkpointer=saver, config=DeepAgentGraphConfig(
                    model_name=model_options['model'], context_window_tokens=1000000,
                    budget=DeepAgentBudget(tool_call_limit=1)))

        initial = to_deep_agent_input({
            'messages': [
                HumanMessage(content='先用 read_file 读取 /acceptance/context.txt。根据文件说明，'
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
            completed = await asyncio.wait_for(running.ainvoke(initial, config, context=context), timeout=240)
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
            retry_input = retry_deep_agent_from_checkpoint(restored.values, retry_instruction=
                '仍然没有分析数据。请再次读取 /acceptance/context.txt，继续用 evidence_only 的 FinalAnalysisDecision 收尾。')
            repeated = await asyncio.wait_for(rebuilt.ainvoke(retry_input, config, context=context), timeout=240)
            retried_state = await rebuilt.aget_state(config)
            require(retried_state.values['thread_tool_call_count']['__all__'] == 2,
                'retry_tool_budget_boundary_not_reached')
        require(FinalAnalysisDecision.model_validate(repeated['structured_response']).outcome == 'evidence_only',
            'reconnected_decision_invalid')
        require(signatures(repeated['messages'])[:len(original)] == original, 'followup_changed_old_reasoning')
        receipts = [message.tool_call_id for message in repeated['messages'] if isinstance(message, ToolMessage)]
        require(len(receipts) == len(set(receipts)), 'retry_duplicate_tool_output_saved')
        require(any(item['reasoning_text_chars'] and item['tool_outputs'] for item in report['requests']),
            'reasoning_not_replayed_with_tool_output')
        report['postgres_reconnect_and_model_followup'] = 'PASS'
        report['tool_budget_boundary_and_gate_retry_input'] = 'PASS'
        report['tool_call_limit'] = 1
        report['thread_tool_call_counts'] = [saved.values['thread_tool_call_count']['__all__'],
            retried_state.values['thread_tool_call_count']['__all__']]

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
        report['http_status'] = getattr(exc, 'status_code', None)
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
