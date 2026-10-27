"""数据处理的真实 MySQL/PostgreSQL 恢复验收，人工独立入口。

文档职责：验证正式服务、确认账本、候选幂等、绑定恢复和上下文 fencing。
适用范围：仅限专用内部 Docker 网络内全新空库；保留数据库和 /tmp 验收产物。
运行前设置 TERMINAL_ACCEPTANCE_MYSQL_HOST=recovery-mysql、
TERMINAL_ACCEPTANCE_POSTGRES_HOST=recovery-postgres 和 DATA_RECOVERY_RUN_ID。
首次运行复用 terminal_persistence_acceptance 的空库配置；--resume 仅恢复同一容器配置。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import secrets
import sys
from typing import Any, TypedDict
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
ARTIFACTS = Path('/tmp/data-processing-recovery')


class ChildRecoveryState(TypedDict, total=False):
    analysis_parameters: dict
    analysis_parameter_changes: list
    data_processing_state_version: int
    data_processing_run_id: str
    first_answer: str
    confirmed_answer: dict
    complete: bool


def require(condition: Any, reason: str) -> None:
    if not condition:
        raise AssertionError(reason)


def candidate_checkpoint_state(saved):
    """为验收构造已实际保存候选的 checkpoint，不用于旧生产 checkpoint 回填。"""
    from Agent.data_processing.lifecycle import execution_report
    from Agent.deep_agent_tools.interaction import DataProcessingPlanSummary

    binding = saved['metadata']['processing_plan_identity']
    plan = {key: saved['metadata'][key] for key in DataProcessingPlanSummary.model_fields}
    return {
        'data_processing_metadata': plan, 'data_processing_confirmed_plan': json.loads(json.dumps(plan)),
        'data_processing_confirmed_plan_version': binding['plan_version'],
        'data_processing_semantic_identity': binding['semantic_identity'],
        'data_processing_draft_script': saved['script_bytes'].decode('utf-8'),
        'data_processing_candidate_id': saved['dataset_id'],
        'data_processing_candidate_revision': saved['revision'],
        'data_processing_execution_report': execution_report(
            candidate_id=saved['dataset_id'], revision=saved['revision'],
            plan_version=binding['plan_version'], source_hash=saved['source_hash'],
            data_bytes=saved['data_bytes'], script_bytes=saved['script_bytes']),
        'data_processing_completion_retry_count': 0,
    }


def configure(resume: bool) -> None:
    from tests.acceptance.p2_mcp.terminal_persistence_acceptance import configure_isolated_databases

    require(os.environ.get('TERMINAL_ACCEPTANCE_MYSQL_HOST') == 'recovery-mysql', 'dedicated_mysql_alias_required')
    require(os.environ.get('TERMINAL_ACCEPTANCE_POSTGRES_HOST') == 'recovery-postgres', 'dedicated_postgres_alias_required')
    run_id = os.environ.get('DATA_RECOVERY_RUN_ID', '')
    require(run_id.startswith('ca-data-recovery-'), 'dedicated_run_identity_required')
    ARTIFACTS.mkdir(exist_ok=True)
    config_path = ARTIFACTS / 'configuration.json'
    if resume:
        configuration = json.loads(config_path.read_text())
        require(configuration['run_id'] == run_id, 'resume_run_identity_mismatch')
        os.environ.update(configuration['environment'])
        return
    require(not config_path.exists(), 'use_resume_for_existing_acceptance')
    import psycopg
    with psycopg.connect(host='recovery-postgres', user='terminal_acceptance', dbname='terminal_acceptance') as connection:
        count = connection.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0]
        require(count == 0, 'refuse_nonempty_postgres')
    configure_isolated_databases()
    os.environ.update({
        'MYSQL_WRITE_HOST': 'recovery-mysql', 'MYSQL_WRITE_USER': 'root',
        'MYSQL_READ_USER': 'root', 'MYSQL_REPLICA_STATUS_USER': 'root',
        'CHECKPOINT_POSTGRES_PORT': '5432', 'PYTHONDONTWRITEBYTECODE': '1',
    })
    selected = {key: value for key, value in os.environ.items() if key.startswith(('MYSQL_', 'CHECKPOINT_', 'LANGCHAIN_', 'LANGSMITH_')) or key in {'CAUSALAGENT_SECRET_FILE', 'BASE_URL', 'MODEL'}}
    config_path.write_text(json.dumps({'run_id': run_id, 'environment': selected}), encoding='utf-8')
    config_path.chmod(0o600)


def application():
    from flask import Flask
    from config.settings import settings
    from app.auth.routes import auth_bp
    from app.chat.routes import chat_bp
    from app.files.routes import files_bp
    from app.agent.routes import agent_bp
    app = Flask(__name__)
    app.secret_key = settings.SECRET_KEY
    for blueprint in (auth_bp, chat_bp, files_bp, agent_bp):
        app.register_blueprint(blueprint)
    return app


def identity_for(job):
    from Agent.deep_agent.context import TrustedJobIdentity, AnalysisInputRef
    return TrustedJobIdentity(
        job_id=job['job_id'], session_id=job['session_id'], user_id=int(job['user_id']),
        worker_id=job['worker_id'], attempt_count=int(job['attempt_count']),
        lease_epoch=int(job['lease_epoch']), input_identity=job['input_file_hash'],
        analysis_input=AnalysisInputRef(),
    )


def context_for(identity):
    from Agent.deep_agent.context import AgentRunContext
    from app.agent.worker.execution_guard import JobExecutionGuard
    return AgentRunContext(trusted_identity=identity, execution_guard=JobExecutionGuard(
        identity.job_id, identity.worker_id, identity.attempt_count, identity.lease_epoch))


async def verify(report):
    from langgraph.runtime import Runtime
    from langgraph.graph import StateGraph, START, END
    from Agent.causal_agent.postgres_checkpointer import open_checkpoint_pool, build_checkpointer, verify_checkpoint_schema
    from Agent.deep_agent.context import AgentRunContext
    from Agent.data_processing import persistence
    from Agent.data_processing.graph import data_processing_parent_node, bind_dataset_node, validate_dataset_node
    from Agent.data_processing.plans import build_processing_plan_version
    from Agent.deep_agent_tools.interaction import DataProcessingPlanConfirmationRequest
    from Agent.deep_agent_tools.adapters import build_default_adapters
    from Agent.deep_agent_tools.registry import build_default_registry
    from Database.analysis_contexts import apply_final_parameters_context, create_context_for_new_file, switch_to_context
    from app.agent import job_service
    from app.auth.service import find_user
    from app.db import get_read_connection

    client = application().test_client()
    username = 'recovery_' + uuid4().hex[:12]
    password = secrets.token_urlsafe(24) + '9'
    require(client.post('/api/register', json={'username': username, 'password': password}).status_code == 200, 'register_failed')
    require(client.post('/api/login', json={'username': username, 'password': password}).status_code == 200, 'login_failed')
    user_id = find_user(username)['id']
    response = client.post('/api/new_chat')
    require(response.status_code == 200, 'session_creation_failed')
    session_id = response.json['new_session_id']
    source = ('flag,x,y\n' + '\n'.join(f'{i % 2},{i / 7},{(i * 13) % 29}' for i in range(120)) + '\n').encode()
    upload = client.post('/api/upload_file', data={'file': (BytesIO(source), 'recovery.csv', 'text/csv')})
    require(upload.status_code == 200 and upload.json['success'], 'file_upload_failed')
    created, replay = job_service.create_job(user_id, session_id, 'isolated recovery acceptance', str(uuid4()),
        input_user_file_id=upload.json['user_file_id'], request_id=str(uuid4()))
    require(not replay, 'unexpected_job_replay')
    job = job_service.claim_next_job('data-recovery-worker')
    require(job and job['job_id'] == created['job_id'], 'claim_failed')
    source_hash = hashlib.sha256(source).hexdigest()
    require(job['input_file_hash'] == source_hash, 'frozen_source_mismatch')
    require(job_service.get_initial_input_value(job['job_id'])['input_type'] == 'initial', 'initial_input_missing')
    original_identity = identity_for(job)
    fields = dict(user_id=user_id, session_id=session_id, job_id=job['job_id'], worker_id=job['worker_id'],
        attempt_count=job['attempt_count'], lease_epoch=job['lease_epoch'])
    require(job_service.get_job_by_id(job['job_id'])['prepared_dataset_id'] is None, 'pointer_present_before_preparation')
    report['registered_login_session_file_job'] = True
    report['job_id'] = job['job_id']

    metadata = dict(summary='Preserve numeric measurements and declared binary flag.', observation_unit='one observation',
        transformations=('preserve columns',), limitations=('synthetic acceptance data',),
        variable_semantics={'flag': 'binary event indicator', 'x': 'continuous measurement', 'y': 'continuous outcome'})
    semantic_identity = hashlib.sha256(b'recovery-acceptance-semantic-goal').hexdigest()
    version = build_processing_plan_version(job_id=job['job_id'], source_hash=source_hash, semantic_identity=semantic_identity, metadata=metadata)
    script = b'from pathlib import Path\nPath("/workspace/output/prepared.csv").write_bytes(Path("/workspace/raw/original.csv").read_bytes())\n'
    parameters = {'target': 'y', 'treatment': None, 'analysis_question': 'isolated recovery acceptance',
        'measurement_semantics': [{'column': 'flag', 'kind': 'binary', 'evidence': 'binary event indicator'}]}
    save_kwargs = dict(source_hash=source_hash, data_bytes=source, script_bytes=script, metadata=metadata,
        plan_version=version, semantic_identity=semantic_identity, analysis_parameters=parameters,
        analysis_parameter_changes=[])
    try:
        persistence.save_candidate(original_identity, **save_kwargs)
    except persistence.DatasetPersistenceError:
        pass
    else:
        raise AssertionError('unconfirmed_candidate_was_saved')
    report['unconfirmed_candidate_rejected'] = True
    report['active_test'] = 'plan_confirmation_and_input_ledger'
    question_id = str(uuid4())
    interaction = DataProcessingPlanConfirmationRequest(prompt='Confirm the isolated acceptance plan.', question_id=question_id,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=4)).isoformat(), plan_version=version, plan=metadata).public_payload()
    require(job_service.complete_job_with_chat(job, job['worker_id'], 'interrupt',
        {'type': 'interrupt', 'interaction': interaction}, 'Confirm processing plan.', None,
        lease_epoch=job['lease_epoch'], question_id=question_id), 'interrupt_not_persisted')
    try:
        job_service.resume_job(user_id, job['job_id'], question_id, {'action': 'confirm', 'plan_version': '0' * 64}, str(uuid4()))
    except job_service.JobStateConflictError:
        pass
    else:
        raise AssertionError('wrong_plan_confirmation_accepted')
    answer = {'action': 'confirm', 'plan_version': version}
    resumed, replay = job_service.resume_job(user_id, job['job_id'], question_id, answer, str(uuid4()))
    require(resumed['status'] == 'queued' and not replay, 'resume_failed')
    ledger = job_service.get_latest_input_value(job['job_id'])
    require(ledger['input_type'] == 'resume' and ledger['runtime_value'] == answer and ledger['chat_message_id'], 'confirmed_input_ledger_mismatch')
    job = job_service.claim_next_job('data-recovery-worker-resumed')
    require(job and job['job_id'] == created['job_id'], 'resumed_claim_failed')
    identity = identity_for(job)
    report['plan_confirmation_and_input_ledger'] = True
    report['active_test'] = 'candidate_replay_tuple_and_json_lists'
    candidate = persistence.save_candidate(identity, **save_kwargs)
    repeated = persistence.save_candidate(identity, **save_kwargs)
    list_kwargs = {**save_kwargs, 'metadata': json.loads(json.dumps(metadata))}
    repeated_list = persistence.save_candidate(identity, **list_kwargs)
    require(candidate == repeated == repeated_list, 'candidate_tuple_json_replay_mismatch')
    require(candidate['revision'] == 1, 'duplicate_candidate_revision')
    readback = persistence.read_candidate(identity, dataset_id=candidate['dataset_id'], source_hash=source_hash)
    require(readback['data_bytes'] == source and readback['metadata']['transformations'] == list(metadata['transformations']), 'candidate_roundtrip_mismatch')
    report['candidate_replay_tuple_and_json_lists'] = True
    report['active_test'] = 'real_validation_record_and_binding'
    registry = build_default_registry(build_default_adapters(executor=None))
    async def load_saved_candidate(child_state, runtime):
        saved = await asyncio.to_thread(persistence.read_candidate, runtime.context.trusted_identity,
            dataset_id=candidate['dataset_id'], source_hash=source_hash)
        require(saved['status'] == 'pending_validation', 'unbound_parent_candidate_not_pending')
        from langchain_core.messages import AIMessage
        return {**child_state, **candidate_checkpoint_state(saved), 'messages': [AIMessage(content='', tool_calls=[{
            'id': 'explain-saved-candidate', 'name': 'save_data_processing_execution_report',
            'args': {'explanation': 'Retained actual measurements; awaiting independent validation.'}}])]}
    load_saved_candidate.__annotations__['runtime'] = Runtime[AgentRunContext]
    async with open_checkpoint_pool() as pool:
        saver = build_checkpointer(pool)
        from langgraph.prebuilt import ToolNode
        from Agent.data_processing.agent import _execution_report_tool
        from Agent.deep_agent.state import ProjectDeepAgentState
        child_builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
        child_builder.add_node('read_actual_candidate', load_saved_candidate)
        child_builder.add_node('explain_candidate', ToolNode([_execution_report_tool()], handle_tool_errors=False))
        child_builder.add_edge(START, 'read_actual_candidate')
        child_builder.add_edge('read_actual_candidate', 'explain_candidate')
        child_builder.add_edge('explain_candidate', END)
        child = child_builder.compile(checkpointer=saver)
        async def invoke_unbound_parent(parent_state, config, runtime):
            return await data_processing_parent_node(parent_state, runtime=runtime, config=config, agent=child, registry=registry)
        invoke_unbound_parent.__annotations__['runtime'] = Runtime[AgentRunContext]
        parent_builder = StateGraph(dict, context_schema=AgentRunContext)
        parent_builder.add_node('data_processing', invoke_unbound_parent)
        parent_builder.add_edge(START, 'data_processing')
        parent_builder.add_edge('data_processing', END)
        parent = parent_builder.compile(checkpointer=saver)
        unbound = await parent.ainvoke({'analysis_parameters': parameters, 'analysis_parameter_changes': []},
            {'configurable': {'thread_id': job['job_id']}}, context=context_for(identity), durability='sync')
        require(unbound.get('data_processing_candidate_id') == candidate['dataset_id'], 'unbound_parent_registry_path_failed')
        require(job_service.get_job_by_id(job['job_id'])['prepared_dataset_id'] is None, 'unbound_parent_bound_candidate_early')
    report['unbound_parent_registry_branch'] = {'status': 'PASS',
        'real_registry_and_postgres_graphs': True, 'child_reads_actual_pending_candidate': True, 'model_execution': False}
    revised = unbound['analysis_context']
    report['final_parameters_context_bound_after_child'] = True
    state = {**unbound, 'data_processing_candidate_id': candidate['dataset_id']}
    runtime = Runtime(context=context_for(identity))
    validation = await validate_dataset_node(state, runtime=runtime, registry=registry)
    require(validation['dataset_validation_status'] == 'accepted', 'real_candidate_validation_rejected')
    require(validation['dataset_validation_report']['semantic_kinds'] == {'flag': 'binary'}, 'validated_semantics_missing')
    state.update(validation)
    require(persistence.record_validation(identity, dataset_id=candidate['dataset_id'], source_hash=source_hash,
        report=validation['dataset_validation_report'], accepted=True)['status'] == 'validated', 'validation_replay_failed')
    bound = await bind_dataset_node(state, runtime=runtime)
    state.update(bound)
    require(identity.current_input_identity() == source_hash, 'bound_identity_mismatch')
    require(state['data_processing_execution_report']['explanation'], 'execution_explanation_not_projected')
    require(state['data_processing_confirmed_plan_version'] == version, 'execution_explanation_cleared_confirmation')
    report['save_then_explain_preserves_confirmation'] = True
    report['real_validation_record_and_binding'] = True
    report['active_test'] = 'new_identity_and_postgres_pool_recovery'

    async def recover_bound(state, runtime: Runtime[AgentRunContext]):
        return await data_processing_parent_node(state, runtime=runtime, config=None, agent=None, registry=registry)
    # Runtime's concrete annotation must be available to LangGraph without a model or test double.
    recover_bound.__annotations__['runtime'] = Runtime[AgentRunContext]
    builder = StateGraph(dict, context_schema=AgentRunContext)
    builder.add_node('recover_bound', recover_bound)
    builder.add_edge(START, 'recover_bound')
    builder.add_edge('recover_bound', END)
    config = {'configurable': {'thread_id': job['job_id']}}
    async with open_checkpoint_pool() as pool:
        await verify_checkpoint_schema(pool)
        graph = builder.compile(checkpointer=build_checkpointer(pool))
        result = await graph.ainvoke(state, config, context=context_for(identity), durability='sync')
        require(result['prepared_dataset_id'] == candidate['dataset_id'], 'checkpoint_bound_id_mismatch')
    fresh_identity = identity_for(job_service.get_job_by_id(job['job_id']))
    fresh_bound = persistence.read_bound_dataset_identity(fresh_identity)
    require(fresh_bound['semantic_kinds'] == {'flag': 'binary'}, 'restored_semantic_kinds_mismatch')
    fresh_identity.analysis_input.bind(dataset_id=fresh_bound['dataset_id'], object_id=fresh_bound['data_object_id'],
        content_hash=fresh_bound['data_hash'], filename=fresh_bound['filename'], semantic_kinds=fresh_bound['semantic_kinds'])
    async with open_checkpoint_pool() as pool:
        graph = builder.compile(checkpointer=build_checkpointer(pool))
        snapshot = await graph.aget_state(config)
        require(snapshot.values['prepared_dataset_id'] == candidate['dataset_id'], 'new_pool_checkpoint_recovery_failed')
        recovered = await graph.ainvoke(None, config, context=context_for(fresh_identity), durability='sync')
        require(recovered['prepared_dataset_id'] == candidate['dataset_id'], 'checkpoint_resume_failed')
    require(persistence.read_bound_dataset(fresh_identity, source_hash=source_hash)['data_bytes'] == source, 'restored_bound_bytes_mismatch')
    report['new_identity_and_postgres_pool_recovery'] = True
    report['active_test'] = 'mcp'
    report['mcp'] = await verify_mcp(fresh_identity, source.decode())
    report['active_test'] = 'same_context_preserves_different_context_clears'
    fields.update(worker_id=job['worker_id'], attempt_count=job['attempt_count'], lease_epoch=job['lease_epoch'])
    same = apply_final_parameters_context(**fields, analysis_parameters=parameters, dataset_id=candidate['dataset_id'])
    require(same['analysis_context_id'] == revised['analysis_context_id'], 'same_context_changed')
    require(job_service.get_job_by_id(job['job_id'])['prepared_dataset_id'] == candidate['dataset_id'], 'same_context_lost_prepared_pointer')
    switch_to_context(**fields, context_id=revised['analysis_context_id'])
    require(job_service.get_job_by_id(job['job_id'])['prepared_dataset_id'] == candidate['dataset_id'], 'switch_to_same_context_lost_pointer')
    other_upload = client.post('/api/upload_file', data={'file': (BytesIO(source + b'0,21,2\n'), 'other.csv', 'text/csv')})
    require(other_upload.status_code == 200 and other_upload.json['success'], 'second_file_upload_failed')
    other = create_context_for_new_file(**fields, user_file_id=other_upload.json['user_file_id'])
    require(other['analysis_context_id'] != revised['analysis_context_id'], 'different_file_context_not_created')
    require(job_service.get_job_by_id(job['job_id'])['prepared_dataset_id'] is None, 'different_context_retained_prepared_pointer')
    switch_to_context(**fields, context_id=revised['analysis_context_id'])
    report['same_context_preserves_different_context_clears'] = True
    report['active_test'] = 'old_lease_save_validate_bind_rejected'
    for stale in (original_identity,):
        try:
            persistence.save_candidate(stale, **save_kwargs)
        except persistence.DatasetPersistenceError:
            pass
        else:
            raise AssertionError('old_lease_saved_candidate')
        try:
            persistence.record_validation(stale, dataset_id=candidate['dataset_id'], source_hash=source_hash,
                report=validation['dataset_validation_report'], accepted=True)
        except persistence.DatasetPersistenceError:
            pass
        else:
            raise AssertionError('old_lease_recorded_validation')
        try:
            persistence.bind_candidate(stale, dataset_id=candidate['dataset_id'], source_hash=source_hash,
                analysis_parameters=parameters, analysis_parameter_changes=[])
        except persistence.DatasetPersistenceError:
            pass
        else:
            raise AssertionError('old_lease_bound_candidate')
    report['old_lease_save_validate_bind_rejected'] = True
    with get_read_connection(consistency='strong') as connection:
        cursor = connection.cursor()
        cursor.execute('SELECT COUNT(*) FROM analysis_job_datasets WHERE job_id=%s', (job['job_id'],))
        require(cursor.fetchone()[0] == 1, 'candidate_count_not_one')
    require(job_service.complete_job(job['job_id'], job['worker_id'], job['attempt_count'], {'acceptance': 'completed'}, lease_epoch=job['lease_epoch']), 'finalize_failed')
    report['status'] = 'PASS'


async def verify_mcp(identity, expected_csv):
    from Agent.CausalAgentMCP.service import CausalMcpService, load_validated_input
    from Agent.CausalAgentMCP.config import McpServerConfig
    from Agent.CausalAgentMCP.executor_pool import BoundedProcessPool
    from Agent.CausalAgentMCP.runner_registry import build_default_registry
    from Agent.CausalAgentMCP.auth import sign_invocation
    from Agent.deep_agent_tools.models import AlgorithmExecutionCommand
    from Agent.deep_agent_tools.algorithm_specs import DIRECT_LINGAM_SPEC
    config = McpServerConfig.from_env()
    invocation_id = str(uuid4())
    context = identity.to_mcp_context(invocation_id=invocation_id)
    authoritative = load_validated_input(context)
    require(authoritative.csv_data == expected_csv and dict(authoritative.semantic_kinds) == {'flag': 'binary'}, 'mcp_authority_semantics_mismatch')
    spec = DIRECT_LINGAM_SPEC
    command = AlgorithmExecutionCommand(invocation_id=invocation_id, capability_id=spec.capability_id,
        capability_version=spec.version, spec_digest=spec.spec_digest, provider_call_id='lingam-binary',
        input_identity=identity.current_input_identity(), parameters={})
    pool = BoundedProcessPool(process_workers=1, queue_capacity=1)
    await pool.start()
    try:
        service = CausalMcpService(config=config, registry=build_default_registry(), executor_pool=pool)
        result = await service.execute_payload({'command': command.model_dump(mode='json'),
            'trusted_context': context.model_dump(mode='json'), 'signature': sign_invocation(context, command, config.signing_key_current)})
        require(result.get('ok') and result['result']['status'] == 'not_applicable', 'mcp_binary_lingam_not_rejected')
        return {'strong_primary_reader': True, 'binary_semantics_restored': True, 'direct_lingam_status': result['result']['status'],
            'transport': 'in_process_service_with_real_hmac_and_database'}
    finally:
        await pool.close()


async def verify_child_recovery(report):
    """使用真实嵌套 StateGraph 和每轮新建的 PostgreSQL pool 验证正式 child helper。"""
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import Command, interrupt
    from langgraph.runtime import Runtime
    from Agent.causal_agent.postgres_checkpointer import open_checkpoint_pool, build_checkpointer
    from Agent.data_processing.graph import run_data_processing_child
    from Agent.data_processing.parameters import PROCESSING_STATE_VERSION
    from Agent.deep_agent.context import AgentRunContext
    from Agent.deep_agent_tools.interaction import DataProcessingQuestionRequest, DataProcessingPlanConfirmationRequest

    thread_id = str(uuid4())
    run_id = 'data-processing:' + uuid4().hex
    initial_state = {'complete': False, 'analysis_parameters': {}, 'analysis_parameter_changes': [],
        'data_processing_state_version': PROCESSING_STATE_VERSION, 'data_processing_run_id': run_id}
    context = AgentRunContext(execution_guard=None)
    version = hashlib.sha256(b'child-reconnect-plan').hexdigest()
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    plan = dict(summary='Verify native interrupt recovery.', observation_unit='one observation',
        transformations=['preserve measurements'], variable_semantics={'x': 'measurement'}, limitations=[])

    def ask_semantics(state):
        answer = interrupt(DataProcessingQuestionRequest(prompt='Which observation unit?',
            question_id='recovery-semantic-question', expires_at=expiry).public_payload())
        require(answer == 'one observation', 'child_semantic_resume_value_mismatch')
        return {'first_answer': answer}

    def ask_confirmation(state):
        answer = interrupt(DataProcessingPlanConfirmationRequest(prompt='Confirm this processing plan.',
            question_id='recovery-plan-question', expires_at=expiry, plan_version=version, plan=plan).public_payload())
        require(answer == {'action': 'confirm', 'plan_version': version}, 'child_plan_resume_value_mismatch')
        return {'confirmed_answer': answer, 'complete': True}

    def compile_graph(saver):
        child_builder = StateGraph(ChildRecoveryState)
        child_builder.add_node('semantic_question', ask_semantics)
        child_builder.add_node('plan_confirmation', ask_confirmation)
        child_builder.add_edge(START, 'semantic_question')
        child_builder.add_edge('semantic_question', 'plan_confirmation')
        child_builder.add_edge('plan_confirmation', END)
        child = child_builder.compile(checkpointer=saver)

        async def call_child(state, config, runtime):
            return await run_data_processing_child(child, config=config, job_id=thread_id,
                run_id=run_id, context=runtime.context, initial_input=initial_state)
        call_child.__annotations__['runtime'] = Runtime[AgentRunContext]
        parent = StateGraph(ChildRecoveryState, context_schema=AgentRunContext)
        parent.add_node('processing_child', call_child)
        parent.add_edge(START, 'processing_child')
        parent.add_edge('processing_child', END)
        return parent.compile(checkpointer=saver)

    config = {'configurable': {'thread_id': thread_id}}
    interrupt_ids = []
    inputs = [initial_state, Command(resume='one observation'),
        Command(resume={'action': 'confirm', 'plan_version': version})]
    for index, value in enumerate(inputs):
        async with open_checkpoint_pool() as pool:
            graph = compile_graph(build_checkpointer(pool))
            if index:
                prior = await graph.aget_state(config)
                require(prior.interrupts, 'reconnected_parent_interrupt_missing')
            result = await graph.ainvoke(value, config, context=context, durability='sync')
            snapshot = await graph.aget_state(config)
            if index < 2:
                require(len(snapshot.interrupts) == 1, 'child_interrupt_count_mismatch')
                item = snapshot.interrupts[0]
                interrupt_ids.append(item.id)
                expected_type = 'data_processing_question' if index == 0 else 'data_processing_plan_confirmation'
                require(item.value['interaction_type'] == expected_type, 'child_public_interrupt_type_mismatch')
            else:
                require(not snapshot.interrupts and result['complete'], 'child_did_not_complete_after_two_resumes')
                require(result['first_answer'] == 'one observation', 'child_lost_first_answer')
                require(result['confirmed_answer']['plan_version'] == version, 'child_lost_confirmation')
    require(len(set(interrupt_ids)) == 2, 'child_interrupt_ids_not_independent')
    report['native_child_postgres_recovery'] = {'status': 'PASS', 'real_compiled_parent_and_child': True,
        'fresh_postgres_pools': 3, 'native_interrupts': 2, 'resumes_after_reconnect': 2,
        'limitations': 'deterministic question nodes; no LLM, OpenSandbox or Job confirmation service'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--child-only', action='store_true')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    report = {'status': 'FAIL', 'started_at': datetime.now(timezone.utc).isoformat(),
        'limitations': ['Flask routes in process; no HTTP/browser', 'no model/OpenSandbox/script execution',
            'same Job checkpoint recovery; no worker crash or expired-lease takeover', 'MCP service in process; no HTTP transport']}
    sources = ('app/agent/job_service.py', 'Agent/data_processing/graph.py',
        'Agent/data_processing/persistence.py', 'Agent/measurement_semantics.py',
        'tests/acceptance/data_processing_recovery.py')
    report['source_sha256'] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}
    try:
        configure(args.resume)
        report['first_run_empty_databases'] = not args.resume or any(
            json.loads(path.read_text()).get('first_run_empty_databases') is True
            for path in ARTIFACTS.glob('report-*.json'))
        from Database.bootstrap import run_bootstrap
        run_bootstrap()
        report['formal_alembic_head_and_postgres_setup'] = True
        asyncio.run(verify_child_recovery(report))
        if args.child_only:
            report['status'] = 'PASS'
        else:
            if args.resume:
                from app.agent import job_service
                for path in ARTIFACTS.glob('report-*.json'):
                    previous = json.loads(path.read_text())
                    previous_id = previous.get('job_id')
                    if previous_id:
                        previous_job = job_service.get_job_by_id(previous_id)
                        if previous_job and previous_job['status'] in {'queued', 'running', 'waiting_input'}:
                            job_service.cancel_job(previous_job['user_id'], previous_id, str(uuid4()))
            asyncio.run(verify(report))
    except Exception as exc:
        report['failure_type'] = type(exc).__name__
        # Assertion labels are authored here; database exception text may contain connection details.
        report['failure_reason'] = str(exc) if isinstance(exc, AssertionError) else 'see retained container for diagnosis'
        import traceback
        (ARTIFACTS / ('failure-' + uuid4().hex[:8] + '.txt')).write_text(traceback.format_exc(), encoding='utf-8')
        raise SystemExit(1) from None
    finally:
        report['source_unchanged_during_run'] = all(
            hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
            for name, digest in report['source_sha256'].items())
        names = ('first_run_empty_databases', 'formal_alembic_head_and_postgres_setup',
            'registered_login_session_file_job', 'final_parameters_context_bound_after_child',
            'unconfirmed_candidate_rejected', 'plan_confirmation_and_input_ledger',
            'candidate_replay_tuple_and_json_lists', 'save_then_explain_preserves_confirmation', 'real_validation_record_and_binding',
            'new_identity_and_postgres_pool_recovery', 'mcp',
            'same_context_preserves_different_context_clears', 'old_lease_save_validate_bind_rejected',
            'native_child_postgres_recovery')
        cases = {}
        for name in names:
            value = report.get(name)
            cases[name] = 'PASS' if value else 'NOT_RUN'
        if report['status'] != 'PASS' and report.get('active_test'):
            cases[report['active_test']] = 'FAIL'
        report['test_cases'] = cases
        report['counts'] = {status: sum(value == status for value in cases.values())
            for status in ('PASS', 'FAIL', 'NOT_RUN')}
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        ARTIFACTS.mkdir(exist_ok=True)
        (ARTIFACTS / ('report-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
