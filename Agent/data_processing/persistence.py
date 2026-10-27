"""MySQL 持久化边界：保存、校验、绑定并读取 Job 数据处理产物。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.db import get_write_connection
from Agent.measurement_semantics import semantic_kinds_from_validation_report
from Agent.data_processing.parameters import (
    PARAMETER_BINDING_KEY, parameter_binding, restore_parameter_binding,
    parameter_identity, validate_parameter_history,
)
from Agent.data_processing.plans import (
    _answer_object,
    build_processing_plan_version,
    plan_confirmation_version,
)


class DatasetPersistenceError(RuntimeError):
    """持久化边界拒绝请求或发现持久化数据不一致。"""


_PROCESSING_PLAN_KEY = "processing_plan_identity"


def _verify_parameter_sources(
    cursor: Any, job_id: str, changes: list[dict[str, Any]], *, include_initial: bool = False,
) -> str:
    evidence: list[str] = []
    previous_sequence = -1
    for record in changes:
        cursor.execute(
            "SELECT input_id, input_text, sequence FROM analysis_job_inputs "
            "WHERE job_id = %s AND input_id = %s AND input_type = 'resume'",
            (job_id, record["source_input_id"]),
        )
        row = cursor.fetchone()
        if not row:
            raise DatasetPersistenceError("parameter revision source does not belong to this Job")
        if int(row["sequence"]) <= previous_sequence:
            raise DatasetPersistenceError("parameter revision sources are not in input order")
        previous_sequence = int(row["sequence"])
        if include_initial:
            evidence.append(str(row["input_text"]))
    if include_initial:
        cursor.execute(
            "SELECT input_text FROM analysis_job_inputs WHERE job_id = %s AND input_type = 'initial' "
            "ORDER BY sequence LIMIT 1", (job_id,),
        )
        initial = cursor.fetchone()
        if not initial:
            raise DatasetPersistenceError("parameter evidence has no initial Job input")
        evidence.insert(0, str(initial["input_text"]))
    return "\n".join(evidence)


def read_parameter_answer(identity: Any, *, question_id: str, answer: Any) -> dict[str, Any]:
    """经持久化 interrupt 关联工具 ID 与外层问题 ID，读取真实答复来源。"""
    with get_write_connection() as connection:
        try:
            connection.start_transaction()
            cursor = connection.cursor(dictionary=True)
            job_id, _worker, _attempt, _epoch, _user, source_hash = _identity_values(identity)
            _lock_current_job(cursor, identity, source_hash)
            cursor.execute(
                "SELECT i.input_id, i.input_text FROM analysis_job_inputs AS i "
                "WHERE i.job_id = %s AND i.input_type = 'resume' AND EXISTS ("
                "SELECT 1 FROM analysis_job_events AS e WHERE e.job_id = i.job_id "
                "AND e.event_type = 'interrupt' "
                "AND JSON_UNQUOTE(JSON_EXTRACT(e.payload_json, '$.question_id')) = i.question_id "
                "AND JSON_UNQUOTE(JSON_EXTRACT(e.payload_json, '$.interaction.question_id')) = %s "
                "AND JSON_UNQUOTE(JSON_EXTRACT(e.payload_json, '$.interaction.interaction_type')) "
                "IN ('data_processing_question', 'data_processing_plan_confirmation')) "
                "ORDER BY i.sequence DESC LIMIT 2 FOR UPDATE",
                (job_id, question_id),
            )
            rows = cursor.fetchall()
            if len(rows) != 1 or _answer_object(rows[0]["input_text"]) != _answer_object(answer):
                raise DatasetPersistenceError("answer does not match the trusted question input")
            row = rows[0]
            connection.commit()
            return {"source_input_id": int(row["input_id"]), "answer": _answer_object(row["input_text"])}
        except BaseException:
            connection.rollback()
            raise


def require_candidate_parameters(
    metadata: dict[str, Any], *, job_id: str, parameters: dict[str, Any],
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    restored = restore_parameter_binding(metadata, job_id=job_id)
    validate_parameter_history(parameters, changes, job_id=job_id)
    if (parameter_identity(parameters) != parameter_identity(restored["analysis_parameters"])
            or changes != restored["analysis_parameter_changes"]):
        raise DatasetPersistenceError("candidate parameters differ from the final parent parameters")
    return restored


def _candidate_plan_binding(
    *,
    job_id: str,
    source_hash: str,
    metadata: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    """校验 MySQL 产物中保存的方案版本，并返回原始说明、版本和语义身份。"""
    stored_metadata = dict(metadata)
    restore_parameter_binding(stored_metadata, job_id=job_id)
    stored_metadata.pop(PARAMETER_BINDING_KEY)
    binding = stored_metadata.pop(_PROCESSING_PLAN_KEY, None)
    if not isinstance(binding, dict):
        raise DatasetPersistenceError("candidate has no confirmed processing plan identity")
    plan_version = _check_hash(
        binding.get("plan_version"), field_name="processing plan version"
    )
    semantic_identity = _check_hash(
        binding.get("semantic_identity"), field_name="processing semantic identity"
    )
    try:
        expected = build_processing_plan_version(
            job_id=job_id,
            source_hash=source_hash,
            semantic_identity=semantic_identity,
            metadata=stored_metadata,
        )
    except ValueError as exc:
        raise DatasetPersistenceError("candidate processing plan identity is invalid") from exc
    if expected != plan_version:
        raise DatasetPersistenceError("candidate processing plan identity does not match its content")
    return stored_metadata, plan_version, semantic_identity


def _latest_plan_confirmation_version(cursor: Any, job_id: str) -> str | None:
    cursor.execute(
        """
        SELECT input_type, input_text
        FROM analysis_job_inputs
        WHERE job_id = %s
        ORDER BY sequence DESC
        LIMIT 1 FOR UPDATE
        """,
        (job_id,),
    )
    latest = cursor.fetchone()
    if not latest or latest.get("input_type") != "resume":
        return None
    return plan_confirmation_version(latest.get("input_text"))


def _canonical_json(value: dict[str, Any], *, field_name: str) -> bytes:
    if not isinstance(value, dict):
        raise DatasetPersistenceError(f"{field_name} must be a JSON object")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DatasetPersistenceError(f"{field_name} must contain valid JSON values") from exc


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identity_values(identity: Any) -> tuple[str, str, int, int, int, str]:
    """读取 AgentRunContext 或 MCP 上下文，不依赖其定义模块。"""
    nested_identity = getattr(identity, "trusted_identity", None)
    if nested_identity is not None:
        identity = nested_identity
    try:
        job_id = str(identity.job_id)
        worker_id = identity.worker_id
        attempt_count = int(identity.attempt_count)
        lease_epoch = int(identity.lease_epoch)
        source_resolver = getattr(identity, "source_input_identity", None)
        if callable(source_resolver):
            source_hash = source_resolver()
        elif getattr(identity, "source_input_identity", None):
            source_hash = identity.source_input_identity
        elif callable(getattr(identity, "current_input_identity", None)):
            source_hash = identity.current_input_identity()
        elif getattr(identity, "source_input_identity", None) is None:
            source_hash = identity.input_snapshot_digest
        else:
            source_hash = identity.input_snapshot_digest
        user_id = int(identity.user_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise DatasetPersistenceError("trusted execution identity is incomplete") from exc

    if (
        not job_id
        or not isinstance(worker_id, str)
        or not worker_id
        or attempt_count < 0
        or lease_epoch < 0
        or user_id <= 0
        or not isinstance(source_hash, str)
    ):
        raise DatasetPersistenceError("trusted execution identity is invalid")
    return job_id, worker_id, attempt_count, lease_epoch, user_id, source_hash


def _check_hash(value: str, *, field_name: str) -> str:
    normalized = value.lower() if isinstance(value, str) else ""
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise DatasetPersistenceError(f"{field_name} must be a SHA-256 hex digest")
    return normalized


def _lock_current_job(cursor: Any, identity: Any, source_hash: str) -> dict[str, Any]:
    job_id, worker_id, attempt_count, lease_epoch, user_id, trusted_source_hash = (
        _identity_values(identity)
    )
    source_hash = _check_hash(source_hash, field_name="source_hash")
    trusted_source_hash = _check_hash(trusted_source_hash, field_name="trusted input hash")
    if source_hash != trusted_source_hash:
        raise DatasetPersistenceError("source hash does not match trusted input identity")

    cursor.execute(
        """
        SELECT job_id, user_id, session_id, analysis_context_id, input_object_id,
               status, worker_id, attempt_count, lease_epoch,
               execution_state, input_file_hash, prepared_dataset_id
        FROM analysis_jobs
        WHERE job_id = %s
        FOR UPDATE
        """,
        (job_id,),
    )
    job = cursor.fetchone()
    if not job:
        raise DatasetPersistenceError("analysis job does not exist")
    if (
        int(job["user_id"]) != user_id
        or job["status"] != "running"
        or job["execution_state"] != "leased"
        or job["worker_id"] != worker_id
        or int(job["attempt_count"]) != attempt_count
        or int(job["lease_epoch"]) != lease_epoch
        or job["input_file_hash"] != source_hash
    ):
        raise DatasetPersistenceError("current Job execution authority or input snapshot is invalid")
    return job


def _get_or_create_file_object(
    cursor: Any,
    *,
    user_id: int,
    content: bytes,
    mime_type: str,
) -> int:
    content_hash = _sha256(content)
    cursor.execute(
        """
        INSERT INTO file_objects
            (owner_user_id, content_hash, file_size, mime_type, file_content)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
        """,
        (user_id, content_hash, len(content), mime_type, content),
    )
    object_id = int(cursor.lastrowid)
    cursor.execute(
        """
        SELECT id, owner_user_id, content_hash, file_size, file_content
        FROM file_objects
        WHERE id = %s
        FOR UPDATE
        """,
        (object_id,),
    )
    stored = cursor.fetchone()
    if (
        not stored
        or int(stored["owner_user_id"]) != user_id
        or stored["content_hash"] != content_hash
        or int(stored["file_size"]) != len(content)
        or bytes(stored["file_content"]) != content
    ):
        raise DatasetPersistenceError("stored file object failed integrity verification")
    return object_id


def save_candidate(
    identity: Any,
    *,
    source_hash: str,
    data_bytes: bytes,
    script_bytes: bytes,
    metadata: dict[str, Any],
    plan_version: str,
    semantic_identity: str,
    analysis_parameters: dict[str, Any],
    analysis_parameter_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """幂等保存候选 CSV、脚本和元数据，并按 Job 锁分配单调 revision。"""
    if not isinstance(data_bytes, bytes) or not isinstance(script_bytes, bytes):
        raise DatasetPersistenceError("dataset and script must be bytes")
    source_hash = _check_hash(source_hash, field_name="source_hash")
    data_hash = _sha256(data_bytes)
    script_hash = _sha256(script_bytes)
    plan_version = _check_hash(plan_version, field_name="processing plan version")
    semantic_identity = _check_hash(
        semantic_identity, field_name="processing semantic identity"
    )
    if not isinstance(metadata, dict):
        raise DatasetPersistenceError("metadata must be a JSON object")
    if _PROCESSING_PLAN_KEY in metadata or PARAMETER_BINDING_KEY in metadata:
        raise DatasetPersistenceError("metadata uses a reserved processing plan field")
    try:
        expected_plan_version = build_processing_plan_version(
            job_id=_identity_values(identity)[0],
            source_hash=source_hash,
            semantic_identity=semantic_identity,
            metadata=metadata,
        )
    except ValueError as exc:
        raise DatasetPersistenceError("processing plan identity is invalid") from exc
    if expected_plan_version != plan_version:
        raise DatasetPersistenceError("confirmed plan version does not match candidate content")
    stored_metadata = {
        **metadata,
        PARAMETER_BINDING_KEY: parameter_binding(
            analysis_parameters, analysis_parameter_changes, job_id=_identity_values(identity)[0],
        ),
        _PROCESSING_PLAN_KEY: {
            "plan_version": plan_version,
            "semantic_identity": semantic_identity,
        },
    }
    metadata_bytes = _canonical_json(stored_metadata, field_name="metadata")
    if len(metadata_bytes) > 128 * 1024:
        raise DatasetPersistenceError("metadata exceeds the configured size limit")
    idempotency_key = _sha256(
        b"\0".join((source_hash.encode(), data_hash.encode(), script_hash.encode(), metadata_bytes))
    )

    with get_write_connection() as connection:
        try:
            connection.start_transaction()
            cursor = connection.cursor(dictionary=True)
            job = _lock_current_job(cursor, identity, source_hash)
            job_id = str(job["job_id"])
            _verify_parameter_sources(cursor, job_id, analysis_parameter_changes)
            if _latest_plan_confirmation_version(cursor, job_id) != plan_version:
                raise DatasetPersistenceError(
                    "the current Job input does not confirm this processing plan"
                )
            cursor.execute(
                """
                SELECT id, revision, source_hash, data_hash, script_hash, metadata,
                       status, validation_report
                FROM analysis_job_datasets
                WHERE job_id = %s AND idempotency_key = %s
                FOR UPDATE
                """,
                (job_id, idempotency_key),
            )
            existing = cursor.fetchone()
            if existing:
                existing_metadata = existing["metadata"]
                if isinstance(existing_metadata, str):
                    existing_metadata = json.loads(existing_metadata)
                if (
                    existing["source_hash"] != source_hash
                    or existing["data_hash"] != data_hash
                    or existing["script_hash"] != script_hash
                    or _canonical_json(existing_metadata, field_name="metadata") != metadata_bytes
                ):
                    raise DatasetPersistenceError("idempotency record conflicts with candidate content")
                existing_report = existing["validation_report"]
                if isinstance(existing_report, str):
                    existing_report = json.loads(existing_report)
                connection.commit()
                return {
                    "dataset_id": int(existing["id"]),
                    "revision": int(existing["revision"]),
                    "status": str(existing["status"]),
                    "validation_report": existing_report,
                }

            data_object_id = _get_or_create_file_object(
                cursor,
                user_id=int(job["user_id"]),
                content=data_bytes,
                mime_type="text/csv",
            )
            script_object_id = _get_or_create_file_object(
                cursor,
                user_id=int(job["user_id"]),
                content=script_bytes,
                mime_type="text/x-python",
            )
            cursor.execute(
                "SELECT COALESCE(MAX(revision), 0) + 1 AS next_revision "
                "FROM analysis_job_datasets WHERE job_id = %s",
                (job_id,),
            )
            revision = int(cursor.fetchone()["next_revision"])
            cursor.execute(
                """
                INSERT INTO analysis_job_datasets
                    (job_id, revision, idempotency_key, source_hash,
                     data_object_id, script_object_id, data_hash, script_hash, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    job_id,
                    revision,
                    idempotency_key,
                    source_hash,
                    data_object_id,
                    script_object_id,
                    data_hash,
                    script_hash,
                    metadata_bytes.decode("utf-8"),
                ),
            )
            dataset_id = int(cursor.lastrowid)
            connection.commit()
            return {
                "dataset_id": dataset_id,
                "revision": revision,
                "status": "pending_validation",
                "validation_report": None,
            }
        except BaseException:
            connection.rollback()
            raise


def require_current_plan_confirmation(
    identity: Any,
    *,
    source_hash: str,
    plan_version: str,
) -> None:
    """确认当前 Job 租约仍有效，且最新的服务端输入明确确认该方案。"""
    source_hash = _check_hash(source_hash, field_name="source_hash")
    plan_version = _check_hash(plan_version, field_name="processing plan version")
    with get_write_connection() as connection:
        try:
            connection.start_transaction()
            cursor = connection.cursor(dictionary=True)
            job = _lock_current_job(cursor, identity, source_hash)
            if _latest_plan_confirmation_version(cursor, str(job["job_id"])) != plan_version:
                raise DatasetPersistenceError(
                    "the current Job input does not confirm this processing plan"
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def record_validation(
    identity: Any,
    *,
    dataset_id: int,
    source_hash: str,
    report: dict[str, Any],
    accepted: bool,
) -> dict[str, Any]:
    """持久化独立校验报告，并将 pending candidate 收敛为 validated/rejected。"""
    if isinstance(dataset_id, bool) or not isinstance(dataset_id, int) or dataset_id <= 0:
        raise DatasetPersistenceError("dataset_id must be a positive integer")
    if not isinstance(accepted, bool):
        raise DatasetPersistenceError("accepted must be a boolean")
    report_bytes = _canonical_json(report, field_name="validation report")
    if report.get("accepted") is not accepted:
        raise DatasetPersistenceError("validation status does not match its report")
    if accepted:
        semantic_kinds_from_validation_report(report)
    source_hash = _check_hash(source_hash, field_name="source_hash")

    with get_write_connection() as connection:
        try:
            connection.start_transaction()
            cursor = connection.cursor(dictionary=True)
            job = _lock_current_job(cursor, identity, source_hash)
            cursor.execute(
                """
                SELECT id, status, source_hash, validation_report, metadata
                FROM analysis_job_datasets
                WHERE id = %s AND job_id = %s
                FOR UPDATE
                """,
                (dataset_id, job["job_id"]),
            )
            dataset = cursor.fetchone()
            if not dataset or dataset["source_hash"] != source_hash:
                raise DatasetPersistenceError("candidate ownership or source hash is invalid")
            metadata = dataset["metadata"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            binding = metadata.get(_PROCESSING_PLAN_KEY) if isinstance(metadata, dict) else None
            stored_version = (
                binding.get("plan_version") if isinstance(binding, dict) else None
            )
            if (
                not isinstance(stored_version, str)
                or _latest_plan_confirmation_version(cursor, str(job["job_id"]))
                != stored_version
            ):
                raise DatasetPersistenceError(
                    "candidate processing plan is no longer confirmed by the current Job input"
                )
            status = "validated" if accepted else "rejected"
            existing_report = dataset["validation_report"]
            if isinstance(existing_report, str):
                existing_report = json.loads(existing_report)
            decoded_report = json.loads(report_bytes.decode("utf-8"))
            if dataset["status"] != "pending_validation":
                if dataset["status"] == status and existing_report == decoded_report:
                    connection.commit()
                    return {"dataset_id": dataset_id, "status": status}
                raise DatasetPersistenceError("candidate validation has already been finalized")
            cursor.execute(
                """
                UPDATE analysis_job_datasets
                SET status = %s, validation_report = %s,
                    validated_at = UTC_TIMESTAMP(6)
                WHERE id = %s AND job_id = %s AND status = 'pending_validation'
                """,
                (status, report_bytes.decode("utf-8"), dataset_id, job["job_id"]),
            )
            if cursor.rowcount != 1:
                raise DatasetPersistenceError("candidate validation state changed concurrently")
            connection.commit()
            return {"dataset_id": dataset_id, "status": status}
        except BaseException:
            connection.rollback()
            raise


def bind_candidate(
    identity: Any, *, dataset_id: int, source_hash: str,
    analysis_parameters: dict[str, Any], analysis_parameter_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """只把同一 Job 的 validated candidate 绑定为其 prepared dataset。"""
    if isinstance(dataset_id, bool) or not isinstance(dataset_id, int) or dataset_id <= 0:
        raise DatasetPersistenceError("dataset_id must be a positive integer")
    source_hash = _check_hash(source_hash, field_name="source_hash")

    with get_write_connection() as connection:
        try:
            connection.start_transaction()
            cursor = connection.cursor(dictionary=True)
            job = _lock_current_job(cursor, identity, source_hash)
            cursor.execute(
                """
                SELECT id, status, source_hash, data_object_id, data_hash, metadata,
                       validation_report
                FROM analysis_job_datasets
                WHERE id = %s AND job_id = %s
                FOR UPDATE
                """,
                (dataset_id, job["job_id"]),
            )
            dataset = cursor.fetchone()
            if (
                not dataset
                or dataset["status"] != "validated"
                or dataset["source_hash"] != source_hash
            ):
                raise DatasetPersistenceError("only this Job's validated candidate can be bound")
            semantic_kinds_from_validation_report(dataset["validation_report"])
            metadata = dataset["metadata"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            require_candidate_parameters(
                metadata, job_id=str(job["job_id"]), parameters=analysis_parameters,
                changes=analysis_parameter_changes,
            )
            _verify_parameter_sources(cursor, str(job["job_id"]), analysis_parameter_changes)
            _candidate_plan_binding(job_id=str(job["job_id"]), source_hash=source_hash, metadata=metadata)
            binding = metadata.get(_PROCESSING_PLAN_KEY) if isinstance(metadata, dict) else None
            stored_version = (
                binding.get("plan_version") if isinstance(binding, dict) else None
            )
            if (
                not isinstance(stored_version, str)
                or _latest_plan_confirmation_version(cursor, str(job["job_id"]))
                != stored_version
            ):
                raise DatasetPersistenceError(
                    "candidate processing plan is no longer confirmed by the current Job input"
                )
            if job["prepared_dataset_id"] is not None:
                if int(job["prepared_dataset_id"]) != dataset_id:
                    raise DatasetPersistenceError(
                        "Job already has a different prepared dataset bound"
                    )
                connection.commit()
                return {
                    "dataset_id": int(dataset["id"]),
                    "data_object_id": int(dataset["data_object_id"]),
                    "data_hash": str(dataset["data_hash"]),
                    "metadata": metadata,
                }
            cursor.execute(
                """
                UPDATE analysis_jobs
                SET prepared_dataset_id = %s
                WHERE job_id = %s AND status = 'running' AND execution_state = 'leased'
                  AND worker_id = %s AND attempt_count = %s AND lease_epoch = %s
                  AND input_file_hash = %s
                """,
                (
                    dataset_id,
                    job["job_id"],
                    job["worker_id"],
                    int(job["attempt_count"]),
                    int(job["lease_epoch"]),
                    source_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise DatasetPersistenceError("Job authority changed before candidate binding")
            connection.commit()
            return {
                "dataset_id": int(dataset["id"]),
                "data_object_id": int(dataset["data_object_id"]),
                "data_hash": str(dataset["data_hash"]),
                "metadata": metadata,
            }
        except BaseException:
            connection.rollback()
            raise


def read_bound_dataset_identity(identity: Any) -> dict[str, Any] | None:
    """Rehydrate a previously bound dataset identity without loading its BLOB."""
    from Database.analysis_contexts import read_job_analysis_context
    nested_identity = getattr(identity, "trusted_identity", None)
    if nested_identity is not None:
        identity = nested_identity
    source_resolver = getattr(identity, "source_input_identity", None)
    source_hash = (
        source_resolver()
        if callable(source_resolver)
        else getattr(identity, "source_input_identity", None)
    )
    if not isinstance(source_hash, str):
        raise DatasetPersistenceError("trusted source input identity is missing")
    source_hash = _check_hash(source_hash, field_name="source_hash")

    with get_write_connection() as connection:
        try:
            connection.start_transaction()
            cursor = connection.cursor(dictionary=True)
            job = _lock_current_job(cursor, identity, source_hash)
            if job["prepared_dataset_id"] is None:
                connection.commit()
                return None
            analysis_context = read_job_analysis_context(cursor, job=job)
            cursor.execute(
                """
                SELECT d.id, d.status, d.source_hash, d.data_hash, d.data_object_id,
                       d.metadata, d.validation_report,
                       data.owner_user_id AS data_owner_id,
                       data.content_hash AS stored_data_hash,
                       data.file_size AS stored_data_size
                FROM analysis_job_datasets AS d
                JOIN file_objects AS data ON data.id = d.data_object_id
                WHERE d.id = %s AND d.job_id = %s
                FOR UPDATE
                """,
                (int(job["prepared_dataset_id"]), job["job_id"]),
            )
            row = cursor.fetchone()
            if (
                not row
                or row["status"] != "validated"
                or row["source_hash"] != source_hash
                or int(row["data_owner_id"]) != int(job["user_id"])
                or row["stored_data_hash"] != row["data_hash"]
                or int(row["stored_data_size"]) < 0
            ):
                raise DatasetPersistenceError("bound dataset identity is invalid")
            metadata = row["metadata"]
            report = row["validation_report"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            if isinstance(report, str):
                report = json.loads(report)
            if not isinstance(metadata, dict) or not isinstance(report, dict):
                raise DatasetPersistenceError("bound dataset metadata or report is invalid")
            semantic_kinds = semantic_kinds_from_validation_report(report)
            restored = restore_parameter_binding(metadata, job_id=str(job["job_id"]))
            if any(analysis_context.get(key) != restored["analysis_parameters"].get(key)
                   for key in ("target", "treatment", "analysis_question")):
                raise DatasetPersistenceError("bound dataset parameters differ from its Job context")
            evidence_text = _verify_parameter_sources(
                cursor, str(job["job_id"]), restored["analysis_parameter_changes"], include_initial=True,
            )
            _candidate_plan_binding(job_id=str(job["job_id"]), source_hash=source_hash, metadata=metadata)
            connection.commit()
            return {
                "dataset_id": int(row["id"]),
                "data_object_id": int(row["data_object_id"]),
                "data_hash": str(row["data_hash"]),
                "filename": str(metadata.get("output_filename") or "prepared.csv"),
                "semantic_kinds": semantic_kinds,
                "metadata": metadata,
                "analysis_context": analysis_context,
                **restored,
                "parameter_evidence_text": evidence_text,
            }
        except BaseException:
            connection.rollback()
            raise


def read_bound_dataset(identity: Any, *, source_hash: str) -> dict[str, Any]:
    """读取 Job 当前绑定的数据与脚本，并核对归属、来源和内容摘要。"""
    source_hash = _check_hash(source_hash, field_name="source_hash")
    with get_write_connection() as connection:
        try:
            connection.start_transaction()
            cursor = connection.cursor(dictionary=True)
            job = _lock_current_job(cursor, identity, source_hash)
            if job["prepared_dataset_id"] is None:
                raise DatasetPersistenceError("Job has no bound prepared dataset")
            cursor.execute(
                """
                SELECT d.id, d.job_id, d.status, d.source_hash, d.data_hash, d.script_hash,
                       d.data_object_id,
                       d.metadata, d.validation_report,
                       data.owner_user_id AS data_owner_id,
                       data.content_hash AS stored_data_hash,
                       data.file_size AS stored_data_size,
                       data.file_content AS data_bytes,
                       script.owner_user_id AS script_owner_id,
                       script.content_hash AS stored_script_hash,
                       script.file_size AS stored_script_size,
                       script.file_content AS script_bytes
                FROM analysis_job_datasets AS d
                JOIN file_objects AS data ON data.id = d.data_object_id
                JOIN file_objects AS script ON script.id = d.script_object_id
                WHERE d.id = %s AND d.job_id = %s
                FOR UPDATE
                """,
                (int(job["prepared_dataset_id"]), job["job_id"]),
            )
            row = cursor.fetchone()
            if (
                not row
                or row["status"] != "validated"
                or row["source_hash"] != source_hash
                or int(row["data_owner_id"]) != int(job["user_id"])
                or int(row["script_owner_id"]) != int(job["user_id"])
            ):
                raise DatasetPersistenceError("bound dataset ownership or source is invalid")
            resolver = getattr(identity, "current_input_identity", None)
            expected_analysis_hash = (
                resolver()
                if callable(resolver)
                else getattr(identity, "input_snapshot_digest", None)
            )
            if expected_analysis_hash not in (None, source_hash, row["data_hash"]):
                raise DatasetPersistenceError("bound dataset does not match current analysis identity")
            bound_dataset_id = getattr(identity, "analysis_dataset_id", None)
            analysis_input = getattr(identity, "analysis_input", None)
            if analysis_input is not None and analysis_input.dataset_id is not None:
                bound_dataset_id = analysis_input.dataset_id
            if bound_dataset_id is not None and int(bound_dataset_id) != int(row["id"]):
                raise DatasetPersistenceError("MCP dataset identity does not match the bound Job dataset")
            data_bytes = bytes(row["data_bytes"])
            script_bytes = bytes(row["script_bytes"])
            if (
                _sha256(data_bytes) != row["data_hash"]
                or row["stored_data_hash"] != row["data_hash"]
                or int(row["stored_data_size"]) != len(data_bytes)
                or _sha256(script_bytes) != row["script_hash"]
                or row["stored_script_hash"] != row["script_hash"]
                or int(row["stored_script_size"]) != len(script_bytes)
            ):
                raise DatasetPersistenceError("bound dataset content integrity check failed")
            metadata = row["metadata"]
            report = row["validation_report"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            if isinstance(report, str):
                report = json.loads(report)
            if not isinstance(metadata, dict) or not isinstance(report, dict):
                raise DatasetPersistenceError("bound dataset metadata or validation report is invalid")
            semantic_kinds_from_validation_report(report)
            connection.commit()
            return {
                "dataset_id": int(row["id"]),
                "data_object_id": int(row["data_object_id"]),
                "source_hash": source_hash,
                "data_hash": row["data_hash"],
                "script_hash": row["script_hash"],
                "data_bytes": data_bytes,
                "script_bytes": script_bytes,
                "metadata": metadata,
                "validation_report": report,
            }
        except BaseException:
            connection.rollback()
            raise


def read_candidate(
    identity: Any,
    *,
    dataset_id: int,
    source_hash: str,
) -> dict[str, Any]:
    """Strong-read one Job-owned pending candidate for independent validation."""
    if isinstance(dataset_id, bool) or not isinstance(dataset_id, int) or dataset_id <= 0:
        raise DatasetPersistenceError("dataset_id must be a positive integer")
    source_hash = _check_hash(source_hash, field_name="source_hash")
    with get_write_connection() as connection:
        try:
            connection.start_transaction()
            cursor = connection.cursor(dictionary=True)
            job = _lock_current_job(cursor, identity, source_hash)
            cursor.execute(
                """
                SELECT d.id, d.revision, d.job_id, d.status, d.source_hash, d.data_hash, d.script_hash,
                       d.data_object_id, d.script_object_id, d.metadata,
                       d.validation_report,
                       data.owner_user_id AS data_owner_id,
                       data.content_hash AS stored_data_hash,
                       data.file_size AS stored_data_size,
                       data.file_content AS data_bytes,
                       script.owner_user_id AS script_owner_id,
                       script.content_hash AS stored_script_hash,
                       script.file_size AS stored_script_size,
                       script.file_content AS script_bytes
                FROM analysis_job_datasets AS d
                JOIN file_objects AS data ON data.id = d.data_object_id
                JOIN file_objects AS script ON script.id = d.script_object_id
                WHERE d.id = %s AND d.job_id = %s
                FOR UPDATE
                """,
                (dataset_id, job["job_id"]),
            )
            row = cursor.fetchone()
            if (
                not row
                or row["status"] not in {"pending_validation", "validated", "rejected"}
                or row["source_hash"] != source_hash
                or int(row["data_owner_id"]) != int(job["user_id"])
                or int(row["script_owner_id"]) != int(job["user_id"])
            ):
                raise DatasetPersistenceError("candidate ownership or source is invalid")
            data_bytes = bytes(row["data_bytes"])
            script_bytes = bytes(row["script_bytes"])
            if (
                _sha256(data_bytes) != row["data_hash"]
                or row["stored_data_hash"] != row["data_hash"]
                or int(row["stored_data_size"]) != len(data_bytes)
                or _sha256(script_bytes) != row["script_hash"]
                or row["stored_script_hash"] != row["script_hash"]
                or int(row["stored_script_size"]) != len(script_bytes)
            ):
                raise DatasetPersistenceError("candidate file integrity check failed")
            metadata = row["metadata"]
            validation_report = row["validation_report"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            if isinstance(validation_report, str):
                validation_report = json.loads(validation_report)
            if not isinstance(metadata, dict):
                raise DatasetPersistenceError("candidate metadata is invalid")
            _candidate_plan_metadata, plan_version, _semantic_identity = (
                _candidate_plan_binding(
                    job_id=str(job["job_id"]),
                    source_hash=source_hash,
                    metadata=metadata,
                )
            )
            restored = restore_parameter_binding(metadata, job_id=str(job["job_id"]))
            _verify_parameter_sources(cursor, str(job["job_id"]), restored["analysis_parameter_changes"])
            if _latest_plan_confirmation_version(cursor, str(job["job_id"])) != plan_version:
                raise DatasetPersistenceError(
                    "candidate processing plan is no longer confirmed by the current Job input"
                )
            if row["status"] != "pending_validation" and not isinstance(
                validation_report, dict
            ):
                raise DatasetPersistenceError("finalized candidate is missing its validation report")
            connection.commit()
            return {
                "dataset_id": int(row["id"]),
                "revision": int(row["revision"]),
                "source_hash": source_hash,
                "data_hash": row["data_hash"],
                "script_hash": row["script_hash"],
                "data_object_id": int(row["data_object_id"]),
                "script_object_id": int(row["script_object_id"]),
                "data_bytes": data_bytes,
                "script_bytes": script_bytes,
                "metadata": metadata,
                "status": str(row["status"]),
                "validation_report": validation_report,
            }
        except BaseException:
            connection.rollback()
            raise
