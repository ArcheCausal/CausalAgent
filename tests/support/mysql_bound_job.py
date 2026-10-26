"""在已初始化的隔离 MySQL 中持久化已绑定 Job，供父图和会话清理验收复用。

调用者先运行真实 database fixture；本模块导入时不加载数据库配置。
只创建测试记录，不执行沙盒、模型或业务删除，验收记录留在隔离库中。
"""

from hashlib import sha256
import json
from uuid import uuid4


def create_bound_job(database):
    """返回拥有真实冻结文件、输入账本、上下文和已校验候选的可信身份。"""
    from Agent.data_processing import persistence
    from Agent.data_processing.parameters import parameter_identity
    from Agent.data_processing.plans import build_processing_plan_version
    from Agent.data_processing.validation import validate_candidate
    from Agent.deep_agent.context import AnalysisInputRef, FrozenInputRef, TrustedJobIdentity
    from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS
    from tests.e2e.database.test_sandbox_sessions_mysql import _sql
    from tests.support.local_algorithm_execution import PC_CSV

    data_bytes = PC_CSV.encode("utf-8")
    prepared_newline = b"\r\n" if b"\r\n" in data_bytes else b"\n"
    source_newline = b"\n" if prepared_newline == b"\r\n" else b"\r\n"
    source_bytes = data_bytes.replace(prepared_newline, source_newline)
    source_hash = sha256(source_bytes).hexdigest()
    data_hash = sha256(data_bytes).hexdigest()
    assert source_bytes != data_bytes and source_hash != data_hash
    script_bytes = (
        "from pathlib import Path\n"
        "Path('/workspace/output/prepared.csv').write_bytes("
        "Path('/workspace/raw/original.csv').read_bytes()"
        f".replace({source_newline!r}, {prepared_newline!r}))\n"
    ).encode("utf-8")
    question = "检查本地观测数据的候选因果结构。"
    parameters = {"analysis_question": question}
    metadata = {
        "summary": "规范化 CSV 换行符，保留观测数据",
        "observation_unit": "CSV 中的一条观测记录",
        "transformations": ["在 CRLF 和 LF 之间转换换行符，保留原始行列和值"],
        "variable_semantics": {},
        "output_filename": "prepared.csv",
    }
    user_id = _sql(database,
        "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
        ("bound-job-" + uuid4().hex, "acceptance-unused-password"))
    session_id, job_id, context_id = (str(uuid4()) for _ in range(3))
    _sql(database, "INSERT INTO sessions (id, user_id) VALUES (%s, %s)", (session_id, user_id))
    object_id = _sql(database,
        "INSERT INTO file_objects (owner_user_id, content_hash, file_size, mime_type, file_content) "
        "VALUES (%s, %s, %s, 'text/csv', %s)",
        (user_id, source_hash, len(source_bytes), source_bytes))
    user_file_id = _sql(database,
        "INSERT INTO user_files (user_id, object_id, filename, mime_type, file_size) "
        "VALUES (%s, %s, 'original.csv', 'text/csv', %s)",
        (user_id, object_id, len(source_bytes)))
    _sql(database,
        """INSERT INTO analysis_contexts
           (analysis_context_id, session_id, user_id, input_user_file_id,
            file_object_id, file_hash, filename, analysis_question)
           VALUES (%s, %s, %s, %s, %s, %s, 'original.csv', %s)""",
        (context_id, session_id, user_id, user_file_id, object_id, source_hash, question))
    _sql(database,
        "UPDATE sessions SET active_analysis_context_id = %s WHERE id = %s AND user_id = %s",
        (context_id, session_id, user_id))
    identity = TrustedJobIdentity(
        job_id=job_id, session_id=session_id, user_id=user_id,
        attempt_count=1, lease_epoch=1, worker_id="bound-job-acceptance-worker",
        input_identity=source_hash,
        frozen_input=FrozenInputRef(
            user_file_id=user_file_id, object_id=object_id,
            content_hash=source_hash, filename="original.csv",
        ),
        analysis_input=AnalysisInputRef(),
    )
    _sql(database,
        """INSERT INTO analysis_jobs
           (job_id, user_id, session_id, message, status, worker_id,
            attempt_count, lease_epoch, execution_state, locked_at, heartbeat_at,
            input_user_file_id, input_object_id, input_file_hash, input_filename,
            analysis_context_id, active_session_key)
           VALUES (%s, %s, %s, %s, 'running', %s, %s, %s, 'leased',
                   UTC_TIMESTAMP(6), UTC_TIMESTAMP(6), %s, %s, %s, 'original.csv', %s, %s)""",
        (job_id, user_id, session_id, question, identity.worker_id,
         identity.attempt_count, identity.lease_epoch, user_file_id, object_id,
         source_hash, context_id, f"{user_id}:{session_id}"))
    semantic_identity = parameter_identity(parameters)
    plan_version = build_processing_plan_version(
        job_id=job_id, source_hash=source_hash,
        semantic_identity=semantic_identity, metadata=metadata,
    )
    for sequence, input_type, text in (
        (0, "initial", question),
        (1, "resume", json.dumps({"action": "confirm", "plan_version": plan_version})),
    ):
        _sql(database,
            """INSERT INTO analysis_job_inputs
               (job_id, sequence, input_type, input_text, idempotency_key,
                request_fingerprint, analysis_context_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (job_id, sequence, input_type, text, str(uuid4()),
             sha256(text.encode("utf-8")).hexdigest(), context_id))
    saved = persistence.save_candidate(
        identity, source_hash=source_hash, data_bytes=data_bytes, script_bytes=script_bytes,
        metadata=metadata, plan_version=plan_version, semantic_identity=semantic_identity,
        analysis_parameters=parameters, analysis_parameter_changes=[],
    )
    dataset_id = saved["dataset_id"]
    candidate = persistence.read_candidate(identity, dataset_id=dataset_id, source_hash=source_hash)
    report = validate_candidate(
        data_bytes=candidate["data_bytes"], script_bytes=candidate["script_bytes"],
        metadata=candidate["metadata"], analysis_parameters=parameters,
        specifications=DEFAULT_ALGORITHM_SPECS, max_bytes=len(data_bytes),
        expected_parameter_identity=parameter_identity(parameters),
    )
    assert report["accepted"] is True, report["errors"]
    persistence.record_validation(
        identity, dataset_id=dataset_id, source_hash=source_hash, report=report, accepted=True,
    )
    persistence.bind_candidate(
        identity, dataset_id=dataset_id, source_hash=source_hash,
        analysis_parameters=parameters, analysis_parameter_changes=[],
    )
    bound = persistence.read_bound_dataset_identity(identity)
    assert bound is not None and bound["dataset_id"] == dataset_id
    assert bound["data_hash"] == data_hash
    assert bound["data_object_id"] != object_id
    identity.analysis_input.bind(
        dataset_id=dataset_id, object_id=bound["data_object_id"],
        content_hash=bound["data_hash"], filename=bound["filename"],
        semantic_kinds=bound["semantic_kinds"],
    )
    return identity
