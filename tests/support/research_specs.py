"""测试中将已明确的完整参数转换为研究声明，不替代任何模型或外部服务。"""
from Agent.data_processing.parameters import ResearchSpec, commit_research_spec, research_parameters


def spec_from_parameters(parameters):
    value = research_parameters(parameters)
    value["measurement_semantics"] = [
        {"scope": "columns", "columns": [item["column"]], "kind": item["kind"], "evidence": item["evidence"]}
        for item in value["measurement_semantics"] or []]
    return ResearchSpec.model_validate(value)


def commit(parameters, history, *, patch, job_id, source_input_id, reason="user_change", expected_revision=None):
    spec = spec_from_parameters({**parameters, **patch})
    columns = sorted({item["column"] for item in parameters.get("measurement_semantics") or []}
                     | {column for item in spec.measurement_semantics for column in item.columns}) or ["x", "y"]
    return commit_research_spec(parameters, history, spec=spec, input_columns=columns, job_id=job_id,
        source_input_id=source_input_id, expected_revision=len(history) if expected_revision is None else expected_revision,
        reason=reason)
