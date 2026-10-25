"""真实答复工具来源验收，由 research_parameter_recovery 人工入口调用。

职责：原始 ToolNode→graph_runner→event writer→Job resume→原工具来源读取。
范围：默认验证真实模型客户端的连接失败边界；real_model_parameter_extraction 入口
显式注入真实模型时验证成功修订。两种模式均不使用模型替身，不执行沙盒。
"""

from dataclasses import replace
import secrets
from uuid import uuid4


async def verify_tool_sources(report, new_job, *, model=None, real_model=False):
    from langchain_core.messages import AIMessage
    from langchain_openai import ChatOpenAI
    from langgraph.graph import StateGraph, START, END
    from langgraph.prebuilt import ToolNode
    from Agent.causal_agent.postgres_checkpointer import open_checkpoint_pool, build_checkpointer
    from Agent.data_processing.agent import _question_tool, _plan_confirmation_tool
    from Agent.data_processing.persistence import DatasetPersistenceError, read_parameter_answer
    from Agent.data_processing.sandbox_client import SandboxControllerProvider, SandboxControllerSettings
    from Agent.deep_agent.context import FrozenInputRef
    from Agent.deep_agent.state import ProjectDeepAgentState
    from Agent.llm_structured_output import StructuredOutputError
    from app.agent import job_service
    from app.agent.worker.graph_runner import ai_call_stream
    from app.agent.worker.event_adapter import _failure_chain
    from app.agent.worker.event_writer import OrderedEventWriter
    from tests.acceptance.data_processing_recovery import context_for, identity_for, require

    # A real client with an unavailable endpoint tests the failure path without supplying model output.
    if model is None:
        require(not real_model, 'real_model_instance_required')
        model = ChatOpenAI(model='unavailable-acceptance-model', api_key=secrets.token_urlsafe(32),
            base_url='http://127.0.0.1:1/v1', timeout=1, max_retries=0)
    provider = SandboxControllerProvider(SandboxControllerSettings(
        controller_url='http://127.0.0.1:1', token=secrets.token_urlsafe(32)))
    cases = []
    for tool in (_question_tool(model=model), _plan_confirmation_tool(model=model)):
        job = new_job()
        tool_id = f'{job["job_id"]}-original-tool'
        builder = StateGraph(ProjectDeepAgentState)
        builder.add_node('tools', ToolNode([tool], handle_tool_errors=False))
        builder.add_edge(START, 'tools')
        builder.add_edge('tools', END)
        config = {'configurable': {'thread_id': job['job_id']}}
        initial = {'analysis_parameters': ({'target': 'y', 'treatment': 'x',
            'analysis_question': 'Study the relation between x and y.', 'measurement_semantics': [],
            'observation_unit': 'one observation', 'time_window': 'one week', 'aggregation_scope': None}
            if real_model else {}), 'analysis_parameter_changes': [],
            'data_processing_answer_receipts': {},
            'data_processing_draft_script': '# retained artifact; not executed',
            'data_processing_semantic_identity': 'a' * 64,
            'data_processing_metadata': {'summary': 'Retain observed measurements.',
                'observation_unit': 'one observation', 'transformations': ['preserve columns'],
                'variable_semantics': {'x': 'measurement', 'y': 'outcome'}, 'limitations': []},
            'messages': [AIMessage(content='', tool_calls=[{'id': tool_id, 'name': tool.name,
                'args': {'question': 'Which observation unit?'} if tool.name == 'ask_data_processing_question' else {}}])]}

        async def run(graph, job, claim_kind):
            identity = replace(identity_for(job), frozen_input=FrozenInputRef(
                user_file_id=job['input_user_file_id'], object_id=job['input_object_id'],
                content_hash=job['input_file_hash'], filename=job['input_filename']))
            context = replace(context_for(identity), sandbox_provider=provider)
            events = [event async for event in ai_call_stream(
                '', job['user_id'], 'tool-source-acceptance', job['session_id'], job_id=job['job_id'],
                job_attempt=job['attempt_count'], input_user_file_id=job['input_user_file_id'],
                input_object_id=job['input_object_id'], input_file_hash=job['input_file_hash'],
                input_filename=job['input_filename'], graph=graph, claim_kind=claim_kind,
                input_record=job_service.get_latest_input_value(job['job_id']),
                execution_guard=context.execution_guard, agent_runtime_context=context)]
            return context, events

        async with open_checkpoint_pool() as pool:
            graph = builder.compile(checkpointer=build_checkpointer(pool))
            await graph.aupdate_state(config, initial, as_node='__start__')
            context, events = await run(graph, job, 'stale_recovery')
            require(not any(event['type'] == 'error' for event in events), 'original_tool_interrupt_failed')
            event = next(event for event in events if event['type'] == 'interrupt')
            snapshot = await graph.aget_state(config)
            require(event['question_id'] == snapshot.interrupts[0].id, 'outer_id_not_native_interrupt')
            require(event['interaction']['question_id'] == tool_id and event['question_id'] != tool_id,
                'outer_and_tool_ids_not_distinct')
            writer = OrderedEventWriter(job, job['worker_id'], context.execution_guard)
            try:
                await writer.submit(event)
                await writer.close()
            finally:
                if not writer.task.done():
                    await writer.abort()
        answer = ('请将 target 明确清空为 null；observation_unit 改为 device-day。其他研究参数保持不变。'
            if tool.name == 'ask_data_processing_question' else
            '请修改方案：time_window 改为 one day，aggregation_scope 改为 all devices。target、treatment 和 observation_unit 保持不变。'
        ) if real_model else 'Use each device-day as one observation.'
        job_service.resume_job(job['user_id'], job['job_id'], event['question_id'], answer, str(uuid4()))
        job = job_service.claim_next_job('original-tool-resumed')
        require(job and job['job_id'] == config['configurable']['thread_id'], 'original_tool_resume_claim_failed')
        trusted = read_parameter_answer(identity_for(job), question_id=tool_id, answer=answer)
        require(trusted['source_input_id'] > 0 and trusted['answer'] == answer, 'original_tool_source_missing')
        for wrong_id, wrong_answer in ((tool_id + '-other', answer), (tool_id, 'wrong answer')):
            try:
                read_parameter_answer(identity_for(job), question_id=wrong_id, answer=wrong_answer)
            except DatasetPersistenceError:
                pass
            else:
                raise AssertionError('unrelated_tool_or_answer_accepted')
        async with open_checkpoint_pool() as pool:
            graph = builder.compile(checkpointer=build_checkpointer(pool))
            context, events = await run(graph, job, 'user_resume')
            failure = next((event for event in events if event['type'] == 'error'), None)
            if real_model:
                if failure is not None:
                    raise failure['_diagnostic'].exc_info[1]
                require(any(event['type'] == 'final_result' for event in events), 'real_tool_did_not_complete')
                values = (await graph.aget_state(config)).values
                parameters = values['analysis_parameters']
                expected = ({'target': None, 'observation_unit': 'device-day'}
                    if tool.name == 'ask_data_processing_question' else
                    {'time_window': 'one day', 'aggregation_scope': 'all devices'})
                require(all(parameters.get(key) == value for key, value in expected.items()), 'real_extracted_values_mismatch')
                require(all(parameters.get(key) == value for key, value in initial['analysis_parameters'].items()
                    if key not in expected), 'unmentioned_parameter_changed')
                changes = values['analysis_parameter_changes']
                require(len(changes) == 1 and changes[0]['revision'] == 1
                    and changes[0]['source_input_id'] == trusted['source_input_id'], 'real_revision_provenance_mismatch')
                require(not values.get('data_processing_parameter_clarification_required'), 'real_answer_unresolved')
                require(values.get('data_processing_confirmed_plan_version') is None, 'revision_did_not_clear_confirmation')
                require(values['data_processing_semantic_identity'] != initial['data_processing_semantic_identity'],
                    'revision_did_not_change_semantic_identity')
                require(str(trusted['source_input_id']) in values['data_processing_answer_receipts'], 'answer_receipt_missing')
                require(job_service.complete_job(job['job_id'], job['worker_id'], job['attempt_count'],
                    {'acceptance': 'real parameter extraction'}, lease_epoch=job['lease_epoch']), 'real_test_job_finalize_failed')
                cases.append(tool.name)
                report['real_model_completed_tools'] = list(cases)
                continue
            require(failure is not None, 'unavailable_model_did_not_fail')
            error = failure['_diagnostic'].exc_info[1]
            causes = list(_failure_chain(error))
            require(any(isinstance(cause, StructuredOutputError)
                and cause.node_name == 'data_processing_parameter_extraction' for cause in causes)
                and any(type(cause).__name__ == 'APIConnectionError' for cause in causes),
                'original_tool_failed_before_model_connection_boundary')
            require(not (await graph.aget_state(config)).values['analysis_parameter_changes'],
                'model_failure_committed_parameters')
        require(job_service.fail_job(job['job_id'], job['worker_id'], job['attempt_count'],
            'Expected unavailable model in isolated acceptance.', lease_epoch=job['lease_epoch']), 'test_job_finalize_failed')
        cases.append(tool.name)
    report['original_tool_answer_sources'] = {'status': 'PASS', 'original_tools': cases,
        'production_runner_and_writer': True, 'native_outer_interrupt_id_in_resume_ledger': True,
        'source_resolved_before_actual_model_connection_failure': not real_model,
        'unrelated_tool_and_answer_rejected': True, 'model_extraction_verified': False,
        'model_output_substitute_used': False}
    if real_model:
        report['original_tool_answer_sources']['model_extraction_verified'] = True
