"""真实 MySQL、PostgreSQL 与正式确认工具的恢复验收。

文档职责：验证原生答复消费、重复确认、脚本修复和授权冲突。
适用范围：复用 data_processing_recovery 的专用空库配置；不调用模型或沙盒。
"""

import argparse
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib.util
from io import BytesIO
import json
import logging
from pathlib import Path
import secrets
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


async def verify(report, *, legacy_agent_source=None):
    from langchain_core.messages import AIMessage
    from langgraph.graph import START, END, StateGraph
    from langgraph.prebuilt import ToolNode
    from langgraph.types import Command
    from Agent.causal_agent.postgres_checkpointer import open_checkpoint_pool, build_checkpointer
    from Agent.data_processing.agent import _plan_confirmation_tool, _write_script_tool, ProcessingPlanInput
    from Agent.data_processing.graph import DataProcessingCompletionMiddleware
    from Agent.data_processing.lifecycle import confirmed_version, update_plan
    from Agent.data_processing.parameters import parameter_identity, ParameterContractError
    from Agent.data_processing.persistence import DatasetPersistenceError
    from Agent.deep_agent.context import AgentRunContext
    from Agent.deep_agent.state import ProjectDeepAgentState
    from app.agent import job_service
    from app.auth.service import find_user
    from tests.acceptance.data_processing_recovery import application, context_for, identity_for, require
    from langgraph.runtime import Runtime

    client = application().test_client()
    username, password = "confirm_" + uuid4().hex[:12], secrets.token_urlsafe(24) + "9"
    require(client.post('/api/register', json={'username': username, 'password': password}).status_code == 200, 'registration')
    require(client.post('/api/login', json={'username': username, 'password': password}).status_code == 200, 'login')
    user_id = find_user(username)['id']
    session_id = client.post('/api/new_chat').json['new_session_id']
    source = b'x,y\n1,2\n3,4\n'
    uploaded = client.post('/api/upload_file', data={'file': (BytesIO(source), 'confirmation.csv', 'text/csv')})
    require(uploaded.status_code == 200, 'upload')
    created, _ = job_service.create_job(user_id, session_id, 'Preserve all values.', str(uuid4()),
        input_user_file_id=uploaded.json['user_file_id'], request_id=str(uuid4()), web_search_enabled=False)
    job_id = created['job_id']
    report['job_id'] = job_id
    job = job_service.claim_next_job('confirmation-retry')
    require(job and job['job_id'] == job_id, 'claim')
    original_identity = identity_for(job)
    source_hash = hashlib.sha256(source).hexdigest()
    metadata = ProcessingPlanInput(summary='保留原始数值', observation_unit='一次观测',
        transformations=['保留全部观测与字段'], variable_semantics={'x': '测量值', 'y': '测量值'}).model_dump(mode='json')
    state = {'analysis_parameters': {'effect_request': None}, 'analysis_parameter_changes': [],
        'data_processing_research_spec_ready': True, 'data_processing_pending_input_id': None,
        'data_processing_plan_parameter_identity': parameter_identity({'effect_request': None}),
        'data_processing_metadata': metadata, 'data_processing_semantic_identity': 'b' * 64,
        'data_processing_confirmed_plan_version': None, 'data_processing_confirmed_plan': None,
        'data_processing_draft_script': "print('prepare')", 'data_processing_candidate_id': 0,
        'data_processing_candidate_revision': 0, 'data_processing_execution_report': None}
    config = {'configurable': {'thread_id': job_id}}

    def compile_graph(saver, confirmation_tool=None):
        builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
        builder.add_node('tools', ToolNode([confirmation_tool or _plan_confirmation_tool(), _write_script_tool()], handle_tool_errors=False))
        builder.add_edge(START, 'tools')
        builder.add_edge('tools', END)
        return builder.compile(checkpointer=saver)

    def call(name, call_id, args):
        return {'messages': [AIMessage(content='', tool_calls=[{'name': name, 'id': call_id, 'args': args}])]}

    explanation = '准备保留全部观测与原始数值，确认后执行。'
    original_context = context_for(original_identity)
    legacy_tool = None
    if legacy_agent_source is not None:
        # 读取指定 Git 基线的真实生产工具源码，创建旧空参数等待调用，再用当前工具恢复。
        spec = importlib.util.spec_from_file_location('legacy_processing_agent', legacy_agent_source)
        legacy_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy_module)
        legacy_tool = legacy_module._plan_confirmation_tool()
        from Agent.data_processing.sandbox_client import SandboxControllerProvider, SandboxControllerSettings
        original_context = replace(original_context,
            sandbox_provider=SandboxControllerProvider(SandboxControllerSettings.from_environment()))
        report['legacy_source_sha256'] = hashlib.sha256(legacy_agent_source.read_bytes()).hexdigest()
    async with open_checkpoint_pool() as pool:
        graph = compile_graph(build_checkpointer(pool), legacy_tool)
        result = await graph.ainvoke({**state, **call('request_data_processing_plan_confirmation', 'first-confirm',
            {} if legacy_tool else {'explanation': explanation})}, config, context=original_context, durability='sync')
        require(len(result['__interrupt__']) == 1, 'initial_interrupt')
        interaction = result['__interrupt__'][0].value
        version = interaction['plan_version']
        if legacy_tool:
            explanation = interaction['message']
        require(interaction['message'] == explanation and interaction['plan']['summary'] == metadata['summary'], 'explanation_and_plan')
    question_id = str(uuid4())
    require(job_service.complete_job_with_chat(job, job['worker_id'], 'interrupt',
        {'type': 'interrupt', 'interaction': interaction}, explanation, None,
        lease_epoch=job['lease_epoch'], question_id=question_id), 'persist_interrupt')
    try:
        job_service.resume_job(user_id, job_id, question_id, {'action': 'confirm', 'plan_version': '0' * 64}, str(uuid4()))
    except job_service.JobStateConflictError:
        report['stale_api_confirmation_rejected'] = True
    else:
        raise AssertionError('stale_api_confirmation_accepted')
    answer = {'action': 'confirm', 'plan_version': version}
    job_service.resume_job(user_id, job_id, question_id, answer, str(uuid4()))
    job = job_service.claim_next_job('confirmation-retry-resumed')
    require(job and job['job_id'] == job_id, 'resume_claim')
    identity = identity_for(job)
    context = context_for(identity)

    # 新连接池重新读取原问题；数据库已保存答复，但必须消费原 interrupt。
    async with open_checkpoint_pool() as pool:
        graph = compile_graph(build_checkpointer(pool))
        snapshot = await graph.aget_state(config)
        require(snapshot.interrupts[0].value == interaction, 'restored_interaction_changed')
        result = await graph.ainvoke(Command(resume=answer), config, context=context, durability='sync')
        require('__interrupt__' not in result and result['data_processing_confirmed_plan_version'] == version, 'resume_not_consumed')
        response = json.loads(result['messages'][-1].content)
        require(response['status'] == 'confirmed' and 'execute_processing_script' in response['instruction'], 'confirmed_instruction')
        report['native_resume_and_explanation_restored'] = True
        if legacy_tool:
            report['legacy_empty_argument_interrupt_consumed'] = True
        result = await graph.ainvoke(call('request_data_processing_plan_confirmation', 'repeat-confirm', {}),
            config, context=context, durability='sync')
        require('__interrupt__' not in result and json.loads(result['messages'][-1].content)['status'] == 'confirmed', 'duplicate_interrupt')
        report['duplicate_confirmation_no_interrupt'] = True
        correction = await DataProcessingCompletionMiddleware().aafter_agent(result, Runtime(context=context))
        require(correction['jump_to'] == 'model' and 'execute_processing_script' in correction['messages'][0].content,
                'missing_candidate_replanned')
        report['confirmed_missing_candidate_executes'] = True
        repaired = "print('repair')"
        repair_input = call('write_processing_script', 'repair-script',
            {'script': repaired, 'repair_summary': '输出检查未通过，正在修正写出方式；继续使用已确认方案。'})
        progress = [event async for event in graph.astream(repair_input, config, context=context,
            stream_mode='custom', durability='sync')]
        require(len(progress) == 1 and progress[0]['_event_key'].endswith(':repair-script'), 'repair_progress')
        saved = (await graph.aget_state(config)).values
        require(confirmed_version(saved, job_id=job_id, source_hash=source_hash) == version, 'repair_lost_confirmation')
        require(saved['data_processing_draft_script'] == repaired and saved['data_processing_candidate_id'] == 0
                and saved['data_processing_execution_report'] is None, 'repair_state')
        require(saved['data_processing_metadata'] == metadata and 'repair_summary' not in metadata, 'repair_changed_plan')
        report['repair_keeps_authorization'] = True
        if legacy_tool:
            # 旧工具可能对已经确认的版本再次中断；此时新工具不能用幂等返回跳过原答复。
            repeated_config = {'configurable': {'thread_id': job_id + '-repeated-legacy'}}
            legacy_graph = compile_graph(build_checkpointer(pool), legacy_tool)
            repeated = await legacy_graph.ainvoke({**saved, **call(
                'request_data_processing_plan_confirmation', 'legacy-repeat', {})}, repeated_config,
                context=replace(context, sandbox_provider=original_context.sandbox_provider), durability='sync')
            interaction = repeated['__interrupt__'][0].value
            repeated_question = str(uuid4())
            require(job_service.complete_job_with_chat(job, job['worker_id'], 'interrupt',
                {'type': 'interrupt', 'interaction': interaction}, interaction['message'], None,
                lease_epoch=job['lease_epoch'], question_id=repeated_question), 'persist_legacy_repeat')
            job_service.resume_job(user_id, job_id, repeated_question, answer, str(uuid4()))
            job = job_service.claim_next_job('confirmation-legacy-repeat')
            require(job and job['job_id'] == job_id, 'claim_legacy_repeat')
            identity = identity_for(job)
            context = context_for(identity)
            repeated = await graph.ainvoke(Command(resume=answer), repeated_config,
                context=context, durability='sync')
            response = json.loads(repeated['messages'][-1].content)
            require('__interrupt__' not in repeated and response['status'] == 'confirmed'
                    and json.loads(response['answer']) == answer, 'legacy_repeat_answer_skipped')
            report['confirmed_legacy_interrupt_consumes_original_answer'] = True
        # 在真实凭据仍有效时检查旧执行身份，不能靠 checkpoint 授权。
        try:
            await graph.ainvoke(call('request_data_processing_plan_confirmation', 'old-lease', {}),
                config, context=AgentRunContext(execution_guard=None, trusted_identity=original_identity), durability='sync')
        except DatasetPersistenceError:
            report['old_lease_rejected'] = True
        else:
            raise AssertionError('old_lease_authorized')

    # 明确改变方案后必须再次提供解释，并通过原生中断消费修改答复。
    changed = deepcopy(saved)
    changed.update(update_plan(changed, {**metadata, 'summary': '只保留已声明的样本范围'}))
    require(confirmed_version(changed, job_id=job_id, source_hash=source_hash) is None, 'changed_plan_authorized')
    legacy_config = {'configurable': {'thread_id': job_id + '-legacy'}}
    async with open_checkpoint_pool() as pool:
        graph = compile_graph(build_checkpointer(pool))
        result = await graph.ainvoke({**changed, **call('request_data_processing_plan_confirmation', 'missing-explanation', {})},
            legacy_config, context=context)
        require('__interrupt__' not in result and 'explanation_required' in result['messages'][-1].content, 'new_explanation_bypassed')
        report['changed_plan_requires_explanation'] = True
        reason = '样本范围需要改变，因此修改保留范围；请确认完整新方案。'
        result = await graph.ainvoke(call('request_data_processing_plan_confirmation', 'changed-confirm', {'explanation': reason}),
            legacy_config, context=context, durability='sync')
        revised_interaction = result['__interrupt__'][0].value
        require(revised_interaction['message'] == reason and revised_interaction['plan_version'] != version,
                'changed_explanation_or_version')
        report['changed_plan_full_card_and_reason'] = True
        stale = await graph.ainvoke(Command(resume=answer), legacy_config, context=context, durability='sync')
        require(json.loads(stale['messages'][-1].content)['status'] == 'stale_confirmation'
                and stale['data_processing_confirmed_plan_version'] is None, 'stale_tool_authorized')
        report['stale_tool_confirmation_rejected'] = True
        result = await graph.ainvoke(call('request_data_processing_plan_confirmation', 'revision-input', {'explanation': reason}),
            legacy_config, context=context, durability='sync')
        revised_interaction = result['__interrupt__'][0].value
        outer_question = str(uuid4())
        require(job_service.complete_job_with_chat(job, job['worker_id'], 'interrupt',
            {'type': 'interrupt', 'question_id': outer_question, 'interaction': revised_interaction}, reason, None,
            lease_epoch=job['lease_epoch'], question_id=outer_question), 'persist_revision_interrupt')
        revision_answer = '保留全部样本，不改变原始处理要求。'
        job_service.resume_job(user_id, job_id, outer_question, revision_answer, str(uuid4()))
        job = job_service.claim_next_job('confirmation-new-input')
        identity = identity_for(job)
        context = context_for(identity)
        result = await graph.ainvoke(Command(resume=revision_answer), legacy_config, context=context, durability='sync')
        require(result['data_processing_pending_input_id'] and result['data_processing_confirmed_plan_version'] is None,
                'new_input_not_pending')
        require(json.loads(result['messages'][-1].content)['status'] == 'revision_requested', 'revision_not_consumed')
        result = await graph.ainvoke(call('request_data_processing_plan_confirmation', 'pending-input', {}),
            legacy_config, context=context, durability='sync')
        require('__interrupt__' not in result and 'set_research_spec' in result['messages'][-1].content, 'pending_input_authorized')
        report['new_user_input_consumed_and_blocks_reuse'] = True
        conflict_config = {'configurable': {'thread_id': job_id + '-stale-receipt'}}
        try:
            await graph.ainvoke({**saved, **call('request_data_processing_plan_confirmation', 'stale-receipt', {})},
                conflict_config, context=context, durability='sync')
        except DatasetPersistenceError:
            report['latest_database_receipt_conflict_rejected'] = True
        else:
            raise AssertionError('stale_receipt_authorized')
    require(job_service.complete_job(job_id, job['worker_id'], job['attempt_count'], {'acceptance': 'completed'},
            lease_epoch=job['lease_epoch']), 'complete')
    report['status'] = 'PASS'


def main():
    from tests.acceptance.data_processing_recovery import configure, ARTIFACTS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--legacy-agent-source', type=Path, help='指定 Git 基线导出的真实 agent.py，验证旧空参数中断恢复')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    report = {'status': 'FAIL', 'limitations': ['No model, OpenSandbox or browser; real databases and tools only.']}
    try:
        configure(args.resume)
        from Database.bootstrap import run_bootstrap
        run_bootstrap()
        asyncio.run(verify(report, legacy_agent_source=args.legacy_agent_source))
    except Exception as exc:
        import traceback
        ARTIFACTS.mkdir(exist_ok=True)
        (ARTIFACTS / 'confirmation-retry-failure.txt').write_text(traceback.format_exc(), encoding='utf-8')
        report['failure_type'] = type(exc).__name__
        report['failure_code'] = str(exc) if isinstance(exc, AssertionError) else 'see_private_diagnostics'
    finally:
        (ARTIFACTS / 'confirmation-retry-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
