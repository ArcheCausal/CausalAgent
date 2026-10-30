"""真实答复工具来源验收，由 research_parameter_recovery 人工入口调用。

职责：原始 ToolNode→graph_runner→event writer→Job resume→原工具来源读取。
范围：默认验证答复仅接收和停止旧授权；真实模型入口通过同一参数工具提交修订。
不使用模型替身，不执行沙盒。
"""

from dataclasses import replace
import json
import secrets
from uuid import uuid4


async def verify_spec_commits(report, new_job):
    """显式工具调用验收；使用真实账本和冻结 CSV，不作为模型理解证据。"""
    from langchain_core.messages import AIMessage
    from langgraph.graph import StateGraph, START, END
    from langgraph.prebuilt import ToolNode
    from Agent.causal_agent.postgres_checkpointer import open_checkpoint_pool, build_checkpointer
    from Agent.data_processing.agent import _research_spec_tool
    from Agent.data_processing.persistence import read_research_inputs
    from Agent.data_processing.validation import _inspect_csv_shape
    from Agent.deep_agent.context import AgentRunContext
    from Agent.deep_agent.state import ProjectDeepAgentState
    from Database.agent_connect import require_frozen_file_for_job
    from app.agent import job_service
    from tests.acceptance.data_processing_recovery import context_for, identity_for, require

    job = new_job('y 是结果变量，x 是处理变量；每次观测为一行，全部输入列均为连续测量。')
    identity = identity_for(job)
    sources = read_research_inputs(identity)
    source = next(item for item in sources if item['input_type'] == 'initial')
    frozen = require_frozen_file_for_job(job['user_id'], job['job_id'], job['input_user_file_id'], job['input_object_id'])
    columns, _ = _inspect_csv_shape(bytes(frozen['file_content']), max_rows=100_000,
        max_columns=1_000, max_cells=1_000_000)
    builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
    builder.add_node('tools', ToolNode([_research_spec_tool()], handle_tool_errors=False))
    builder.add_edge(START, 'tools')
    builder.add_edge('tools', END)
    config = {'configurable': {'thread_id': job['job_id']}}
    initial = {'analysis_parameters': {}, 'analysis_parameter_changes': [],
        'data_processing_input_columns': columns,
        'data_processing_input_sources': {str(source['source_input_id']): source},
        'data_processing_pending_input_id': source['source_input_id'],
        'data_processing_research_spec_ready': False,
        'data_processing_semantic_identity': job['input_file_hash']}
    draft = {'target': 'x', 'treatment': 'x', 'analysis_question': '研究 x 对 y 的影响',
        'observation_unit': None, 'time_window': None, 'aggregation_scope': None,
        'measurement_semantics': [{'scope': 'all_input_columns', 'columns': [],
            'kind': 'continuous', 'evidence': source['text']}]}

    def message(spec, revision, reason):
        return AIMessage(content='', tool_calls=[{'id': uuid4().hex, 'name': 'set_research_spec',
            'args': {'spec': spec, 'expected_revision': revision,
                'source_input_id': source['source_input_id'], 'reason': reason}}])

    async with open_checkpoint_pool() as pool:
        graph = builder.compile(checkpointer=build_checkpointer(pool))
        first = await graph.ainvoke({**initial, 'messages': [message(draft, 0, 'initial')]},
            config, context=context_for(identity))
        require(len(first['analysis_parameter_changes']) == 1, 'initial_spec_not_committed')
        require(first['data_processing_pending_input_id'] is None, 'initial_spec_still_pending')
        require({item['column'] for item in first['analysis_parameters']['measurement_semantics']} == set(columns),
            'full_input_columns_not_resolved')
        corrected = {**draft, 'target': 'y', 'observation_unit': '每次观测'}
        second = await graph.ainvoke({'messages': [message(corrected, 1, 'correction')]},
            config, context=context_for(identity))
        require(second['analysis_parameters']['target'] == 'y', 'same_source_correction_not_applied')
        require(len(second['analysis_parameter_changes']) == 2, 'correction_not_a_new_revision')
        require(all(item['source_input_id'] == source['source_input_id'] for item in second['analysis_parameter_changes']),
            'correction_source_not_actual_initial_ledger')
        replay = await graph.ainvoke({'messages': [message(corrected, 1, 'correction')]},
            config, context=context_for(identity))
        require(replay['analysis_parameter_changes'] == second['analysis_parameter_changes'], 'replay_added_revision')
        stale = await graph.ainvoke({'messages': [message(draft, 0, 'initial')]},
            config, context=context_for(identity))
        require(json.loads(stale['messages'][-1].content)['status'] == 'rejected', 'stale_spec_accepted')
        require(stale['analysis_parameters'] == second['analysis_parameters'], 'stale_spec_changed_parameters')
    async with open_checkpoint_pool() as pool:
        restored = await builder.compile(checkpointer=build_checkpointer(pool)).aget_state(config)
        require(restored.values['analysis_parameter_changes'] == second['analysis_parameter_changes'],
            'spec_revisions_not_restored_after_reconnect')
    require(job_service.complete_job(job['job_id'], job['worker_id'], job['attempt_count'],
        {'acceptance': 'research spec tool contract'}, lease_epoch=job['lease_epoch']), 'spec_job_completion_failed')
    report['research_spec_tool_commits'] = {'status': 'PASS', 'actual_initial_ledger_source': True,
        'actual_frozen_column_scope': True, 'same_source_correction': True,
        'identical_replay_no_revision': True, 'stale_spec_rejected': True,
        'postgres_reconnect_preserves_revisions': True, 'model_invoked': False}


async def verify_tool_sources(report, new_job, *, model=None, real_model=False, scenario=None, tool_name=None):
    from langchain_core.messages import AIMessage
    from langgraph.graph import StateGraph, START, END
    from langgraph.prebuilt import ToolNode
    from Agent.causal_agent.postgres_checkpointer import open_checkpoint_pool, build_checkpointer
    from Agent.data_processing.agent import _question_tool, _plan_confirmation_tool, _research_spec_tool, data_processing_system_prompt
    from Agent.data_processing.persistence import DatasetPersistenceError, read_parameter_answer, read_research_inputs
    from Agent.data_processing.sandbox_client import SandboxControllerProvider, SandboxControllerSettings
    from Agent.deep_agent.context import FrozenInputRef
    from Agent.deep_agent.state import ProjectDeepAgentState
    from Agent.data_processing.parameters import parameter_identity
    from Database.agent_connect import require_frozen_file_for_job
    from Agent.data_processing.validation import _inspect_csv_shape
    from app.agent import job_service
    from app.agent.worker.graph_runner import ai_call_stream
    from app.agent.worker.event_adapter import _failure_chain
    from app.agent.worker.event_writer import OrderedEventWriter
    from tests.acceptance.data_processing_recovery import context_for, identity_for, require

    require(not real_model or model is not None, 'real_model_instance_required')
    provider = SandboxControllerProvider(SandboxControllerSettings(
        controller_url='http://127.0.0.1:1', token=secrets.token_urlsafe(32)))
    cases = []
    for tool in (_question_tool(), _plan_confirmation_tool()):
        if tool_name is not None and tool.name != tool_name:
            continue
        job = new_job()
        tool_id = f'{job["job_id"]}-original-tool'
        builder = StateGraph(ProjectDeepAgentState)
        builder.add_node('tools', ToolNode([tool], handle_tool_errors=False))
        builder.add_edge(START, 'tools')
        if real_model:
            research_tool = _research_spec_tool()
            async def propose(state, runtime):
                require(int(state.get('model_call_count') or 0) < 3, 'research_spec_retry_budget_exhausted')
                prompt = {
                    'parameters': state['analysis_parameters'], 'revision': len(state['analysis_parameter_changes']),
                    'input_columns': state['data_processing_input_columns'],
                    'trusted_inputs': state['data_processing_input_sources'],
                    'pending_input_id': state.get('data_processing_pending_input_id'), 'reason': 'user_change',
                    'plan': state['data_processing_metadata'],
                }
                from langchain_core.messages import HumanMessage, SystemMessage
                response = await model.bind_tools([research_tool], tool_choice='auto').ainvoke([
                    SystemMessage(content=data_processing_system_prompt()),
                    HumanMessage(content=json.dumps(prompt, ensure_ascii=False)),
                    *state['messages'][-2:],
                ])
                update = {'messages': [response], 'model_call_count': int(state.get('model_call_count') or 0) + 1}
                if not response.tool_calls:
                    from Agent.data_processing.graph import DataProcessingCompletionMiddleware
                    correction = await DataProcessingCompletionMiddleware().aafter_agent(state, runtime)
                    require(correction and correction.get('jump_to') == 'model', 'completion_did_not_request_pending_spec')
                    update['messages'].extend(correction['messages'])
                    update['data_processing_completion_retry_count'] = correction['data_processing_completion_retry_count']
                return update
            from Agent.deep_agent.context import AgentRunContext
            from langgraph.runtime import Runtime
            propose.__annotations__['runtime'] = Runtime[AgentRunContext]
            builder.add_node('model', propose)
            builder.add_node('research_spec', ToolNode([research_tool]))
            builder.add_edge('tools', 'model')
            builder.add_conditional_edges('model', lambda state: 'research_spec'
                if getattr(state['messages'][-1], 'tool_calls', None) else 'model')
            builder.add_conditional_edges('research_spec', lambda state: 'model' if state.get('data_processing_pending_input_id') else END)
        else:
            builder.add_edge('tools', END)
        config = {'configurable': {'thread_id': job['job_id']}}
        initial = {'analysis_parameters': ({'target': 'y', 'treatment': 'x',
            'analysis_question': 'Study the relation between x and y.', 'measurement_semantics': [],
            'observation_unit': 'one observation', 'time_window': 'one week', 'aggregation_scope': None}
            if real_model else {}), 'analysis_parameter_changes': [],
            'data_processing_answer_receipts': {},
            'data_processing_draft_script': '# retained artifact; not executed',
            'data_processing_semantic_identity': 'a' * 64,
            'data_processing_metadata': (scenario.get('plan_metadata') if scenario else None) or
                {'summary': 'Retain observed measurements.',
                 'observation_unit': 'one observation', 'transformations': ['preserve columns'],
                 'variable_semantics': {'x': 'measurement', 'y': 'outcome'}, 'limitations': []},
            'messages': [AIMessage(content='', tool_calls=[{'id': tool_id, 'name': tool.name,
                'args': {'question': scenario['question'] if scenario else 'Which observation unit?'}
                    if tool.name == 'ask_data_processing_question' else {}}])]}
        if scenario is not None:
            require(real_model, 'semantic_scenarios_require_real_model')
            initial['analysis_parameters'].update(scenario.get('parameters', {}))

        if real_model:
            # 思考模式必须回传真实模型产生的推理历史；不能补造推理字段来包装显式测试调用。
            from langchain_core.messages import HumanMessage, SystemMessage
            response = await model.bind_tools([tool], tool_choice='auto').ainvoke([
                SystemMessage(content='请调用提供的工具发起这次交互，不修改或回答问题。只调用一次工具。'),
                HumanMessage(content=json.dumps({'tool': tool.name,
                    'arguments': {'question': scenario['question']} if tool.name == 'ask_data_processing_question' else {},
                    'instruction': '严格使用给出的工具参数，问题文字保持原样。'}, ensure_ascii=False))])
            require(len(response.tool_calls) == 1 and response.tool_calls[0]['name'] == tool.name,
                'real_original_tool_call_required')
            if tool.name == 'ask_data_processing_question':
                require(response.tool_calls[0]['args'].get('question') == scenario['question'],
                    'original_question_changed')
            tool_id = response.tool_calls[0]['id']
            initial['messages'] = [response]

        identity = identity_for(job)
        frozen = require_frozen_file_for_job(job['user_id'], job['job_id'], job['input_user_file_id'], job['input_object_id'])
        columns, _ = _inspect_csv_shape(bytes(frozen['file_content']), max_rows=100_000, max_columns=1_000, max_cells=1_000_000)
        sources = read_research_inputs(identity)
        initial.update(data_processing_input_columns=columns, data_processing_research_spec_ready=True,
            data_processing_pending_input_id=None,
            data_processing_input_sources={str(item['source_input_id']): item for item in sources},
            data_processing_plan_parameter_identity=parameter_identity(initial['analysis_parameters']))

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
            failure = next((event for event in events if event['type'] == 'error'), None)
            if real_model and failure is not None:
                raise failure['_diagnostic'].exc_info[1]
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
        answer = scenario['answer'] if scenario else ('请将 target 明确清空为 null；observation_unit 改为 device-day。其他研究参数保持不变。'
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
                expected = scenario['expected'] if scenario else ({'target': None, 'observation_unit': 'device-day'}
                    if tool.name == 'ask_data_processing_question' else
                    {'time_window': 'one day', 'aggregation_scope': 'all devices'})
                mismatched_fields = [key for key, value in expected.items() if parameters.get(key) != value]
                require(not mismatched_fields, 'real_extracted_values_mismatch:' + ','.join(sorted(mismatched_fields)))
                if scenario and scenario.get('expected_measurement_kind'):
                    require({item['column']: item['kind'] for item in parameters['measurement_semantics']}
                        == dict.fromkeys(columns, scenario['expected_measurement_kind']), 'real_measurement_scope_mismatch')
                intent_updates = scenario.get('intent_updates', []) if scenario else []
                for key in intent_updates:
                    if parameters.get(key) != initial['analysis_parameters'].get(key):
                        require(isinstance(parameters.get(key), str) and bool(parameters[key].strip()),
                            'intent_update_requires_explicit_nonempty_value:' + key)
                unexpected_fields = [key for key, value in initial['analysis_parameters'].items()
                    if key not in expected and key not in intent_updates
                    and key != 'measurement_semantics' and parameters.get(key) != value]
                require(not unexpected_fields, 'unmentioned_parameter_changed:' + ','.join(sorted(unexpected_fields)))
                changes = values['analysis_parameter_changes']
                changed = parameters != initial['analysis_parameters']
                require(len(changes) == int(changed), 'real_revision_count_mismatch')
                if changed:
                    require(changes[0]['revision'] == 1
                        and changes[0]['source_input_id'] == trusted['source_input_id'], 'real_revision_provenance_mismatch')
                require(not values.get('data_processing_pending_input_id'), 'real_answer_unresolved')
                require(values.get('data_processing_confirmed_plan_version') is None, 'revision_did_not_clear_confirmation')
                if changed:
                    require(values['data_processing_semantic_identity'] != initial['data_processing_semantic_identity'],
                        'revision_did_not_change_semantic_identity')
                require(str(trusted['source_input_id']) in values['data_processing_answer_receipts'], 'answer_receipt_missing')
                require(job_service.complete_job(job['job_id'], job['worker_id'], job['attempt_count'],
                    {'acceptance': 'real parameter extraction'}, lease_epoch=job['lease_epoch']), 'real_test_job_finalize_failed')
                cases.append(tool.name)
                report['real_model_completed_tools'] = list(cases)
                continue
            require(failure is None, 'answer_delivery_failed')
            values = (await graph.aget_state(config)).values
            require(values['analysis_parameters'] == initial['analysis_parameters'], 'answer_tool_modified_parameters')
            require(values.get('data_processing_pending_input_id') == trusted['source_input_id'], 'answer_not_pending')
            require(values.get('data_processing_confirmed_plan_version') is None, 'answer_preserved_authorization')
            from tests.support.research_specs import spec_from_parameters
            wrong_source = sources[0]['source_input_id']
            pending_builder = StateGraph(ProjectDeepAgentState, context_schema=type(context))
            pending_builder.add_node('research_spec', ToolNode([_research_spec_tool()], handle_tool_errors=False))
            pending_builder.add_edge(START, 'research_spec')
            pending_builder.add_edge('research_spec', END)
            rejected = await pending_builder.compile().ainvoke({**values, 'messages': [
                AIMessage(content='', tool_calls=[{'id': uuid4().hex, 'name': 'set_research_spec', 'args': {
                    'spec': spec_from_parameters(values['analysis_parameters']).model_dump(mode='json'),
                    'expected_revision': 0, 'source_input_id': wrong_source, 'reason': 'correction'}}])]},
                context=context)
            require(json.loads(rejected['messages'][-1].content)['status'] == 'rejected', 'earlier_source_used_for_pending_answer')
            require(rejected['analysis_parameter_changes'] == values['analysis_parameter_changes'], 'rejected_source_changed_history')
            require(rejected['data_processing_pending_input_id'] == trusted['source_input_id'], 'rejected_source_consumed_answer')
        require(job_service.fail_job(job['job_id'], job['worker_id'], job['attempt_count'],
            'Answer delivery verified without a model call.', lease_epoch=job['lease_epoch']), 'test_job_finalize_failed')
        cases.append(tool.name)
    report['original_tool_answer_sources'] = {'status': 'PASS', 'original_tools': cases,
        'production_runner_and_writer': True, 'native_outer_interrupt_id_in_resume_ledger': True,
        'answer_delivery_without_hidden_model_call': not real_model,
        'unrelated_tool_and_answer_rejected': True, 'model_extraction_verified': False,
        'earlier_source_rejected_while_answer_pending': not real_model,
        'model_output_substitute_used': False}
    if real_model:
        report['original_tool_answer_sources']['model_extraction_verified'] = True
