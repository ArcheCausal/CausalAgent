"""真实参数 MySQL/PostgreSQL 验收，人工入口。

职责：验证可信答复来源、连续修订、checkpoint 重连、上下文幂等及历史保护。
范围：先运行 data_processing_recovery.py；仅复用该次专用空库配置。
问题、答复和 patch 是明确构造的图输入，不调用模型，不作为模型提取证据。
运行：python tests/acceptance/research_parameter_recovery.py
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
import logging
from pathlib import Path
import secrets
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.acceptance.data_processing_recovery import (
    ARTIFACTS, application, configure, context_for, identity_for, require,
)


def fields_for(job):
    return {key: job[key] for key in (
        'user_id', 'session_id', 'job_id', 'worker_id', 'attempt_count', 'lease_epoch',
    )}


def resume_question(job, interaction, answer, *, question_id=None):
    from app.agent import job_service
    question_id = question_id or str(uuid4())
    require(job_service.complete_job_with_chat(
        job, job['worker_id'], 'interrupt', {'type': 'interrupt', 'interaction': interaction, 'question_id': question_id},
        interaction['message'], None, lease_epoch=job['lease_epoch'], question_id=question_id,
    ), 'question_not_persisted')
    resumed, replay = job_service.resume_job(
        job['user_id'], job['job_id'], question_id, answer, str(uuid4()),
    )
    require(resumed['status'] == 'queued' and not replay, 'resume_not_queued')
    claimed = job_service.claim_next_job('parameter-resumed-worker')
    require(claimed and claimed['job_id'] == job['job_id'], 'resumed_claim_failed')
    require(claimed['attempt_count'] > job['attempt_count'], 'resume_attempt_not_advanced')
    return claimed


async def continuous_revisions(job, base, report):
    from langchain_core.messages import HumanMessage
    from langgraph.graph import StateGraph, START, END
    from langgraph.runtime import Runtime
    from langgraph.types import interrupt
    from Agent.causal_agent.postgres_checkpointer import open_checkpoint_pool, build_checkpointer
    from Agent.data_processing.graph import run_data_processing_child
    from Agent.data_processing.parameters import PROCESSING_STATE_VERSION, apply_parameter_patch, sandbox_request_id
    from Agent.data_processing.persistence import read_parameter_answer
    from Agent.deep_agent.context import AgentRunContext
    from Agent.deep_agent.state import ProjectDeepAgentState
    from Agent.deep_agent_tools.identity import build_data_processing_run_id
    from Agent.deep_agent_tools.interaction import DataProcessingQuestionRequest
    from app.agent import job_service
    from app.agent.worker.graph_runner import ai_call_stream
    from app.agent.worker.event_writer import OrderedEventWriter

    job_id = job['job_id']
    run_id = build_data_processing_run_id(job_id=job_id, source_input_identity=job['input_file_hash'])
    initial = {'analysis_parameters': base, 'analysis_parameter_changes': [],
        'data_processing_state_version': PROCESSING_STATE_VERSION, 'data_processing_run_id': run_id,
        'messages': [HumanMessage(content='Explicit synthetic graph input; no model is invoked.')]}
    # Explicit inputs to the deterministic contract; these are not model output substitutes.
    revisions = (
        ('Clear the target; each observation is one device-day.', {'target': None, 'observation_unit': 'device-day'}),
        ('Use a one-day window and retain all devices.', {'time_window': 'one day', 'aggregation_scope': 'all devices'}),
    )

    def compile_graph(saver, retry=False):
        child_builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
        for index, (text, patch) in enumerate(revisions, 1):
            async def revise(state, runtime, index=index, text=text, patch=patch):
                if len(state['analysis_parameter_changes']) >= index:
                    return {}
                question_id = f'{job_id}-parameters-{index}'
                answer = interrupt(DataProcessingQuestionRequest(
                    prompt=f'Clarify revision {index}.', question_id=question_id,
                    expires_at=(datetime.now(timezone.utc) + timedelta(minutes=4)).isoformat(),
                ).public_payload())
                trusted = await asyncio.to_thread(read_parameter_answer, runtime.context,
                    question_id=question_id, answer=answer)
                require(trusted['answer'] == text, 'trusted_answer_mismatch')
                parameters, history = apply_parameter_patch(
                    state['analysis_parameters'], state['analysis_parameter_changes'], patch=patch,
                    job_id=job_id, source_input_id=trusted['source_input_id'],
                )
                return {'analysis_parameters': parameters, 'analysis_parameter_changes': history}
            revise.__annotations__['runtime'] = Runtime[AgentRunContext]
            child_builder.add_node(f'question_{index}', revise)
        child_builder.add_edge(START, 'question_1')
        child_builder.add_edge('question_1', 'question_2')
        child_builder.add_edge('question_2', END)
        child = child_builder.compile(checkpointer=saver)

        async def processing(state, config, runtime):
            return dict(await run_data_processing_child(child, config=config, job_id=job_id,
                run_id=run_id, context=runtime.context, initial_input=initial,
                retry_input={'messages': [HumanMessage(content='Retry validation.')]} if retry else None))
        processing.__annotations__['runtime'] = Runtime[AgentRunContext]
        parent = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
        parent.add_node('processing', processing)
        parent.add_edge(START, 'processing')
        parent.add_edge('processing', END)
        return parent.compile(checkpointer=saver)

    config = {'configurable': {'thread_id': job_id}}
    attempts, request_ids = [], []
    for step in range(3):
        identity = identity_for(job)
        attempts.append(identity.attempt_count)
        request_ids.append(sandbox_request_id(job_id=str(identity.job_id),
            source_hash=identity.source_input_identity(), tool_call_id='persisted-tool-call', purpose='execute'))
        async with open_checkpoint_pool() as pool:
            graph = compile_graph(build_checkpointer(pool))
            # Initial projection is explicit synthetic graph input. The actual runner handles resumes.
            if step == 0:
                await graph.aupdate_state(config, initial, as_node='__start__')
            context = context_for(identity)
            events = [event async for event in ai_call_stream(
                '', job['user_id'], 'parameter-acceptance', job['session_id'], job_id=job_id,
                job_attempt=job['attempt_count'], input_user_file_id=job['input_user_file_id'],
                input_object_id=job['input_object_id'], input_file_hash=job['input_file_hash'],
                input_filename=job['input_filename'], graph=graph,
                claim_kind='stale_recovery' if step == 0 else 'user_resume',
                input_record=job_service.get_latest_input_value(job_id),
                execution_guard=context.execution_guard, agent_runtime_context=context)]
            require(not any(event['type'] == 'error' for event in events), 'production_runner_failed')
            snapshot = await graph.aget_state(config)
            result = snapshot.values
        if step < 2:
            require(len(snapshot.interrupts) == 1, 'one_parameter_interrupt_required')
            event = next(event for event in events if event['type'] == 'interrupt')
            require(event['question_id'] == snapshot.interrupts[0].id, 'runner_question_not_interrupt_id')
            require(event['question_id'] != event['interaction']['question_id'], 'outer_and_inner_ids_not_distinct')
            writer = OrderedEventWriter(job, job['worker_id'], context.execution_guard)
            try:
                await writer.submit(event)
                await writer.close()
            finally:
                if not writer.task.done():
                    await writer.abort()
            resumed, replayed = job_service.resume_job(job['user_id'], job_id, event['question_id'], revisions[step][0], str(uuid4()))
            require(resumed['status'] == 'queued' and not replayed, 'production_runner_resume_failed')
            previous_attempt = job['attempt_count']
            job = job_service.claim_next_job('parameter-resumed-worker')
            require(job and job['job_id'] == job_id and job['attempt_count'] > previous_attempt, 'runner_resume_claim_failed')
        else:
            require(not snapshot.interrupts, 'clarification_not_completed')
    require(result['analysis_parameters']['target'] is None, 'explicit_null_lost')
    require(result['analysis_parameters']['observation_unit'] == 'device-day', 'prior_revision_lost')
    require(result['analysis_parameters']['time_window'] == 'one day', 'latest_revision_lost')
    require([row['revision'] for row in result['analysis_parameter_changes']] == [1, 2], 'invalid_revision_sequence')
    for retry in (False, True):
        async with open_checkpoint_pool() as pool:
            graph = compile_graph(build_checkpointer(pool), retry=retry)
            replay = await graph.ainvoke({'analysis_parameters': base}, config,
                context=context_for(identity_for(job)), durability='sync')
            require(replay['analysis_parameters'] == result['analysis_parameters'], 'replay_overwrote_parameters')
            require(replay['analysis_parameter_changes'] == result['analysis_parameter_changes'], 'replay_duplicated_history')
    require(len(set(request_ids)) == 1, 'request_id_changed_across_actual_attempts')
    report['continuous_clarifications'] = {'status': 'PASS', 'postgres_pool_reopens': 5,
        'actual_job_attempts': attempts, 'revisions': [1, 2], 'parent_replay_and_retry': True,
        'synthetic_graph_inputs': True, 'model_execution': False,
        'production_graph_runner_and_event_writer': True, 'distinct_outer_interrupt_and_inner_tool_ids': True}
    return job, result


def confirm_and_save(job, parameters, changes, data):
    from Agent.data_processing.parameters import parameter_identity
    from Agent.data_processing.persistence import save_candidate
    from Agent.data_processing.plans import build_processing_plan_version
    from Agent.deep_agent_tools.interaction import DataProcessingPlanConfirmationRequest
    metadata = {'summary': 'Retain observed measurements.', 'observation_unit': parameters['observation_unit'],
        'transformations': ['preserve columns'], 'variable_semantics': {'x': 'measurement', 'y': 'outcome'},
        'limitations': ['synthetic acceptance data; no model or sandbox execution']}
    semantic_identity = parameter_identity(parameters)
    version = build_processing_plan_version(job_id=job['job_id'], source_hash=job['input_file_hash'],
        semantic_identity=semantic_identity, metadata=metadata)
    interaction = DataProcessingPlanConfirmationRequest(prompt='Confirm the explicit acceptance plan.',
        question_id=str(uuid4()), plan_version=version, plan=metadata,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=4)).isoformat()).public_payload()
    job = resume_question(job, interaction, {'action': 'confirm', 'plan_version': version})
    kwargs = dict(source_hash=job['input_file_hash'], data_bytes=data, script_bytes=b'# retained acceptance artifact\n',
        metadata=metadata, plan_version=version, semantic_identity=semantic_identity,
        analysis_parameters=parameters, analysis_parameter_changes=changes)
    candidate = save_candidate(identity_for(job), **kwargs)
    require(save_candidate(identity_for(job), **kwargs) == candidate, 'candidate_replay_not_idempotent')
    return job, candidate, kwargs


async def validate_and_bind(job, candidate, parameters, changes, kwargs):
    from langgraph.runtime import Runtime
    from Agent.data_processing.graph import validate_dataset_node, bind_dataset_node
    from Agent.deep_agent_tools.adapters import build_default_adapters
    from Agent.deep_agent_tools.registry import build_default_registry
    state = {'analysis_parameters': parameters, 'analysis_parameter_changes': changes,
        'data_processing_candidate_id': candidate['dataset_id'],
        'data_processing_semantic_identity': kwargs['semantic_identity'],
        'data_processing_confirmed_plan_version': kwargs['plan_version']}
    runtime = Runtime(context=context_for(identity_for(job)))
    state.update(await validate_dataset_node(state, runtime=runtime,
        registry=build_default_registry(build_default_adapters(executor=None))))
    require(state['dataset_validation_status'] == 'accepted', 'candidate_validation_failed')
    state.update(await bind_dataset_node(state, runtime=runtime))
    return state


def context_count(user_id, session_id):
    from app.db import get_read_connection
    with get_read_connection(consistency='strong') as connection:
        cursor = connection.cursor()
        cursor.execute('SELECT COUNT(*) FROM analysis_contexts WHERE user_id=%s AND session_id=%s', (user_id, session_id))
        return cursor.fetchone()[0]


async def verify_legacy_checkpoints(report):
    from langgraph.graph import StateGraph, START, END
    from langgraph.runtime import Runtime
    from Agent.causal_agent.postgres_checkpointer import open_checkpoint_pool, build_checkpointer
    from Agent.data_processing.graph import DATA_PROCESSING_CHECKPOINT_NAMESPACE, run_data_processing_child
    from Agent.data_processing.parameters import PROCESSING_STATE_VERSION, ParameterContractError
    from Agent.deep_agent.context import AgentRunContext
    from Agent.deep_agent.state import ProjectDeepAgentState
    from Agent.deep_agent_tools.identity import build_data_processing_run_id

    for old_goal in (False, True):
        thread_id = str(uuid4())
        run_id = build_data_processing_run_id(job_id=thread_id, source_input_identity='a' * 64)
        namespace = DATA_PROCESSING_CHECKPOINT_NAMESPACE + ':' + (
            'old-goal-dependent-id' if old_goal else run_id.partition(':')[2])
        async with open_checkpoint_pool() as pool:
            saver = build_checkpointer(pool)
            child_builder = StateGraph(ProjectDeepAgentState)
            child_builder.add_node('complete', lambda state: {})
            child_builder.add_edge(START, 'complete')
            child_builder.add_edge('complete', END)
            child = child_builder.compile(checkpointer=saver)
            await child.aupdate_state({'configurable': {'thread_id': thread_id,
                'checkpoint_ns': namespace, '__pregel_checkpointer': saver}}, {'analysis_parameters': {}})

            async def processing(state, config, runtime):
                return dict(await run_data_processing_child(child, config=config, job_id=thread_id,
                    run_id=run_id, context=runtime.context, initial_input={
                        'analysis_parameters': {}, 'analysis_parameter_changes': [],
                        'data_processing_state_version': PROCESSING_STATE_VERSION, 'data_processing_run_id': run_id}))
            processing.__annotations__['runtime'] = Runtime[AgentRunContext]
            builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
            builder.add_node('processing', processing)
            builder.add_edge(START, 'processing')
            builder.add_edge('processing', END)
            graph = builder.compile(checkpointer=saver)
            config = {'configurable': {'thread_id': thread_id}}
            before = [row async for row in saver.alist(config)
                if row.config['configurable'].get('checkpoint_ns', '').startswith(DATA_PROCESSING_CHECKPOINT_NAMESPACE + ':')]
            try:
                await graph.ainvoke({}, config, context=AgentRunContext(execution_guard=None), durability='sync')
            except ParameterContractError:
                pass
            else:
                raise AssertionError('legacy_processing_checkpoint_accepted')
            after = [row async for row in saver.alist(config)
                if row.config['configurable'].get('checkpoint_ns', '').startswith(DATA_PROCESSING_CHECKPOINT_NAMESPACE + ':')]
            require(len(before) == len(after), 'legacy_checkpoint_silently_replaced')
    report['legacy_postgres_checkpoints'] = {'status': 'PASS', 'current_namespace_rejected': True,
        'old_goal_namespace_rejected': True, 'processing_checkpoints_preserved': True}


async def replay_stale_parent(job, candidate, old_context, report, *, bound):
    """显式保存旧父 State，重放真实父节点；不模拟进程崩溃或模型执行。"""
    from langgraph.graph import StateGraph, START, END
    from langgraph.runtime import Runtime
    from Agent.causal_agent.postgres_checkpointer import open_checkpoint_pool, build_checkpointer
    from Agent.data_processing.graph import DATA_PROCESSING_CHECKPOINT_NAMESPACE, data_processing_parent_node
    from Agent.data_processing.parameters import PROCESSING_STATE_VERSION, restore_parameter_binding
    from Agent.data_processing.persistence import read_candidate
    from Agent.deep_agent.context import AgentRunContext
    from Agent.deep_agent_tools.identity import build_data_processing_run_id
    from Agent.deep_agent_tools.adapters import build_default_adapters
    from Agent.deep_agent_tools.registry import build_default_registry
    from app.agent import job_service

    identity = identity_for(job)
    saved = read_candidate(identity, dataset_id=candidate['dataset_id'], source_hash=job['input_file_hash'])
    parameters = restore_parameter_binding(saved['metadata'], job_id=job['job_id'])
    run_id = build_data_processing_run_id(job_id=job['job_id'], source_input_identity=job['input_file_hash'])
    plan = saved['metadata']['processing_plan_identity']
    from tests.acceptance.data_processing_recovery import candidate_checkpoint_state
    child_state = {**parameters, **candidate_checkpoint_state(saved), 'data_processing_state_version': PROCESSING_STATE_VERSION,
        'data_processing_run_id': run_id, 'data_processing_candidate_id': candidate['dataset_id'],
        'data_processing_semantic_identity': plan['semantic_identity'],
        'data_processing_confirmed_plan_version': plan['plan_version']}
    registry = build_default_registry(build_default_adapters(executor=None))
    count_before = context_count(job['user_id'], job['session_id'])
    current_context_id = job_service.get_job_by_id(job['job_id'])['analysis_context_id']
    require(current_context_id != old_context['analysis_context_id'], 'stale_parent_context_not_distinct')
    async with open_checkpoint_pool() as pool:
        saver = build_checkpointer(pool)
        child_builder = StateGraph(dict)
        child_builder.add_node('saved_candidate', lambda state: state)
        child_builder.add_edge(START, 'saved_candidate')
        child_builder.add_edge('saved_candidate', END)
        child = child_builder.compile(checkpointer=saver)
        await child.aupdate_state({'configurable': {'thread_id': job['job_id'],
            'checkpoint_ns': f'{DATA_PROCESSING_CHECKPOINT_NAMESPACE}:{run_id.partition(":")[2]}',
            '__pregel_checkpointer': saver}}, child_state, as_node='saved_candidate')

        async def processing(state, config, runtime):
            return await data_processing_parent_node(state, config=config, runtime=runtime, agent=child, registry=registry)
        processing.__annotations__['runtime'] = Runtime[AgentRunContext]
        builder = StateGraph(dict, context_schema=AgentRunContext)
        builder.add_node('data_processing', processing)
        builder.add_edge(START, 'data_processing')
        builder.add_edge('data_processing', END)
        graph = builder.compile(checkpointer=saver)
        config = {'configurable': {'thread_id': job['job_id']}}
        await graph.aupdate_state(config, {'analysis_parameters': {'target': 'stale-parent-value'},
            'analysis_parameter_changes': [], 'analysis_context': old_context,
            'analysis_context_index': [{'analysis_context_id': current_context_id}]}, as_node='__start__')
        restored = await graph.ainvoke(None, config, context=context_for(identity), durability='sync')
        require(restored['analysis_context']['analysis_context_id'] == current_context_id, 'parent_restored_old_context')
        require(restored['context_resolution']['analysis_context_id'] == current_context_id, 'resolution_not_restored')
        require(restored['analysis_parameters'] == parameters['analysis_parameters'], 'parent_final_parameters_not_restored')
        require([row['analysis_context_id'] for row in restored['analysis_context_index']] ==
            [old_context['analysis_context_id']], 'parent_history_index_not_restored')
        require(context_count(job['user_id'], job['session_id']) == count_before, 'parent_replay_duplicated_context')
        if bound:
            require(restored['prepared_dataset_id'] == candidate['dataset_id'], 'bound_parent_dataset_not_restored')
    report['bound_parent_context_recovery' if bound else 'context_commit_before_parent_checkpoint'] = {
        'status': 'PASS', 'real_postgres_parent_checkpoint': True, 'explicit_stale_parent_state': True,
        'current_job_context_and_history_index_restored': True, 'context_count_unchanged': True}


async def verify(report):
    from Agent.data_processing.parameters import ParameterContractError, parameter_update_id
    from Agent.data_processing.persistence import DatasetPersistenceError, bind_candidate, read_bound_dataset_identity, save_candidate
    from Database.analysis_contexts import AnalysisContextFencedError, apply_final_parameters_context, load_context
    from app.agent import job_service
    from app.auth.service import find_user
    from app.db import get_read_connection

    client = application().test_client()
    username, password = 'parameters_' + uuid4().hex[:12], secrets.token_urlsafe(24) + '9'
    require(client.post('/api/register', json={'username': username, 'password': password}).status_code == 200, 'registration_failed')
    require(client.post('/api/login', json={'username': username, 'password': password}).status_code == 200, 'login_failed')
    user_id = find_user(username)['id']
    session_id = client.post('/api/new_chat').json['new_session_id']
    data = ('x,y\n' + '\n'.join(f'{i / 7},{(i * 13) % 29}' for i in range(120)) + '\n').encode()
    upload = client.post('/api/upload_file', data={'file': (BytesIO(data), 'parameters.csv', 'text/csv')})
    require(upload.status_code == 200 and upload.json['success'], 'upload_failed')

    def new_job():
        created, replay = job_service.create_job(user_id, session_id, 'Explicit synthetic parameter acceptance.',
            str(uuid4()), input_user_file_id=upload.json['user_file_id'], request_id=str(uuid4()))
        require(not replay, 'unexpected_job_replay')
        job = job_service.claim_next_job('parameter-worker')
        require(job and job['job_id'] == created['job_id'], 'initial_claim_failed')
        return job

    def complete(job):
        require(job_service.complete_job(job['job_id'], job['worker_id'], job['attempt_count'],
            {'acceptance': 'parameter persistence'}, lease_epoch=job['lease_epoch']), 'job_completion_failed')

    report['active_test'] = 'continuous_clarifications'
    original = new_job()
    job, state = await continuous_revisions(original, {'target': 'y', 'treatment': None,
        'analysis_question': 'Explicit synthetic parameter acceptance.', 'measurement_semantics': None,
        'observation_unit': None, 'time_window': None, 'aggregation_scope': None}, report)
    parameters, changes = state['analysis_parameters'], state['analysis_parameter_changes']
    with get_read_connection(consistency='strong') as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT input_id FROM analysis_job_inputs WHERE job_id=%s AND input_type='resume' ORDER BY sequence", (job['job_id'],))
        require([row['input_id'] for row in cursor.fetchall()] == [row['source_input_id'] for row in changes], 'sources_not_actual_ledger')
    report['active_test'] = 'candidate_parameters_and_fencing'
    job, candidate, kwargs = confirm_and_save(job, parameters, changes, data)
    before = context_count(user_id, session_id)
    first = apply_final_parameters_context(**fields_for(job), analysis_parameters=parameters, dataset_id=candidate['dataset_id'])
    require(context_count(user_id, session_id) == before, 'unfinished_context_duplicated')
    try:
        apply_final_parameters_context(**fields_for(original), analysis_parameters=parameters, dataset_id=candidate['dataset_id'])
    except AnalysisContextFencedError:
        pass
    else:
        raise AssertionError('old_lease_changed_context')
    await validate_and_bind(job, candidate, parameters, changes, kwargs)
    try:
        bind_candidate(identity_for(job), dataset_id=candidate['dataset_id'], source_hash=job['input_file_hash'],
            analysis_parameters={**parameters, 'time_window': 'wrong window'}, analysis_parameter_changes=[])
    except (DatasetPersistenceError, ParameterContractError):
        pass
    else:
        raise AssertionError('mismatched_parameters_bound')
    for _ in range(2):
        replay = apply_final_parameters_context(**fields_for(job), analysis_parameters=parameters, dataset_id=candidate['dataset_id'])
        require(replay['analysis_context_id'] == first['analysis_context_id'], 'context_replay_changed_identity')
        require(job_service.get_job_by_id(job['job_id'])['prepared_dataset_id'] == candidate['dataset_id'], 'replay_cleared_pointer')
    restored = read_bound_dataset_identity(identity_for(job))
    require(restored['analysis_parameters'] == parameters and restored['analysis_parameter_changes'] == changes, 'bound_parameters_not_restored')
    complete(job)
    historical = load_context(user_id, session_id, first['analysis_context_id'])
    require(historical['target'] is None and historical['treatment'] is None, 'null_role_history_not_retained')
    report['candidate_parameters_and_fencing'] = {'status': 'PASS', 'real_ledger_sources': True,
        'stale_context_write_rejected': True, 'candidate_mismatch_rejected': True, 'bound_parameters_restored': True}

    report['active_test'] = 'historical_context_protection'
    changed = {**parameters, 'time_window': 'one week'}
    second, other, other_kwargs = confirm_and_save(new_job(), changed, [], data)
    forged = deepcopy(changes)
    for record in forged:
        record['update_id'] = parameter_update_id(job_id=second['job_id'], source_input_id=record['source_input_id'])
    try:
        save_candidate(identity_for(second), **{**other_kwargs, 'analysis_parameters': parameters, 'analysis_parameter_changes': forged})
    except DatasetPersistenceError as exc:
        require('source does not belong' in str(exc), 'foreign_source_rejected_for_unexpected_reason')
    else:
        raise AssertionError('foreign_job_sources_accepted')
    switched = apply_final_parameters_context(**fields_for(second), analysis_parameters=changed, dataset_id=other['dataset_id'])
    require(switched['analysis_context_id'] != first['analysis_context_id'], 'historical_null_roles_overwritten')
    require(context_count(user_id, session_id) == before + 1, 'wrong_historical_context_count')
    require(load_context(user_id, session_id, first['analysis_context_id']) == historical, 'historical_context_changed')
    await replay_stale_parent(second, other, historical, report, bound=False)
    await validate_and_bind(second, other, changed, [], other_kwargs)
    await replay_stale_parent(second, other, historical, report, bound=True)
    replay = apply_final_parameters_context(**fields_for(second), analysis_parameters=changed, dataset_id=other['dataset_id'])
    require(replay['analysis_context_id'] == switched['analysis_context_id'], 'switched_context_replay_not_idempotent')
    require(job_service.get_job_by_id(second['job_id'])['prepared_dataset_id'] == other['dataset_id'], 'second_binding_lost')
    complete(second)
    followup = new_job()
    require(followup['prepared_dataset_id'] is None, 'followup_unexpected_prepared_dataset')
    complete(followup)
    third, same_candidate, _ = confirm_and_save(new_job(), changed, [], data)
    same = apply_final_parameters_context(**fields_for(third), analysis_parameters=changed, dataset_id=same_candidate['dataset_id'])
    require(same['analysis_context_id'] == switched['analysis_context_id'], 'identical_history_not_reused')
    require(context_count(user_id, session_id) == before + 1, 'identical_history_duplicated')
    complete(third)
    report['historical_context_protection'] = {'status': 'PASS', 'completed_null_role_history_immutable': True,
        'changed_window_creates_one_context': True, 'replay_preserves_prepared_pointer': True,
        'identical_final_parameters_reuse_history': True, 'foreign_job_source_rejected': True,
        'successful_followup_without_prepared_does_not_hide_analysis_snapshot': True}
    report['active_test'] = 'legacy_postgres_checkpoints'
    await verify_legacy_checkpoints(report)
    report['active_test'] = 'original_tool_answer_sources'
    from tests.acceptance.research_parameter_tool_sources import verify_tool_sources
    await verify_tool_sources(report, new_job)
    report['status'] = 'PASS'


def main():
    logging.disable(logging.CRITICAL)
    sources = ('tests/acceptance/research_parameter_recovery.py', 'tests/acceptance/research_parameter_tool_sources.py',
        'Agent/data_processing/parameters.py',
        'Agent/data_processing/persistence.py', 'Agent/data_processing/graph.py', 'Database/analysis_contexts.py')
    report = {'status': 'FAIL', 'started_at': datetime.now(timezone.utc).isoformat(),
        'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
        'limitations': ['synthetic graph questions, answers and parameter patches; no model extraction',
            'real MySQL ledger and PostgreSQL checkpoints; controlled resumes, no crash takeover',
            'no controller HTTP, OpenSandbox or script execution', 'Flask routes in process; no browser']}
    try:
        configure(resume=True)
        asyncio.run(verify(report))
    except Exception as exc:
        report['failure_type'] = type(exc).__name__
        report['failure_reason'] = str(exc) if isinstance(exc, AssertionError) else 'see retained container for diagnosis'
        import traceback
        (ARTIFACTS / ('parameter-failure-' + uuid4().hex[:8] + '.txt')).write_text(traceback.format_exc(), encoding='utf-8')
    finally:
        report['source_unchanged_during_run'] = all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
            for name, digest in report['source_sha256'].items())
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        (ARTIFACTS / ('parameter-report-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '.json')).write_text(
            json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
    raise SystemExit(0 if report['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
