"""守护预发网关弃用日志与后端真实弃用面的对齐。

缺陷背景：网关 JSONL 只在后端返回 ``Deprecation`` 头时落盘，而候选观察路由
不返回该头；同时六个 run 兼容端点族曾完全缺失 route 映射。本测试直接解析
``app/rag_eval/routes.py`` 与 ``deploy/staging/nginx.conf``，用 nginx map 语义
模拟匹配，防止两侧再次漂移。
"""

from __future__ import annotations

import re
import shlex
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ROUTES_PY = REPO_ROOT / "app" / "rag_eval" / "routes.py"
NGINX_CONF = REPO_ROOT / "deploy" / "staging" / "nginx.conf"
REQUEST_CONTEXT_PY = REPO_ROOT / "app" / "request_context.py"

BLUEPRINT_PREFIX = "/api/rag_eval"
OBSERVATION_CANDIDATES = (
    "/api/rag_eval/status",
    "/api/rag_eval/steps",
    "/api/rag_eval/config",
    "/api/rag_eval/production-config/publish",
    "/api/rag_eval/isolated/candidate-runs/rebound-import",
)
FAMILY_LABELS = {
    "/api/rag_eval/isolated/ingestion-runs/*",
    "/api/rag_eval/isolated/tuning-dataset-runs/*",
    "/api/rag_eval/isolated/candidate-runs/*",
    "/api/rag_eval/gold-v2/governance-runs/*",
    "/api/rag_eval/isolated/rag-runs/*",
    "/api/rag_eval/isolated/evaluation-runs/*",
}

_PLACEHOLDER_SAMPLES = {
    "run_id": "rag_evalrun0123abcdef",
    "path:artifact_name": "reports/summary.json",
}


def _strip_comment(line: str) -> str:
    """去掉 nginx 配置行内的注释（本配置仅使用整行注释）。"""
    return line.split("#", 1)[0]


def parse_map_entries(conf_text: str, map_variable: str) -> tuple[str, list[tuple[str, str, str]]]:
    """解析指定变量的 map 块，返回 (default, [(kind, key, value)])。

    kind 为 ``literal`` 或 ``regex``；key 已去掉引号与 ``~`` 前缀。
    """
    start = re.search(rf'map\s+[^{{]+{re.escape(map_variable)}\s*\{{', conf_text)
    if not start:
        raise AssertionError(f"nginx.conf missing map for {map_variable}")
    tail = conf_text[conf_text.index("{", start.start()) + 1:]
    # map 块闭括号是缩进的，必须按行匹配而不是找列 0 的 "\n}"。
    body_lines: list[str] = []
    for line in tail.splitlines():
        if re.fullmatch(r"\s*\}", line):
            break
        body_lines.append(line)
    default = ""
    entries: list[tuple[str, str, str]] = []
    for raw in body_lines:
        line = _strip_comment(raw).strip()
        if not line or line == "{":
            continue
        try:
            tokens = shlex.split(line.rstrip(";"))
        except ValueError as exc:
            raise AssertionError(f"cannot tokenize {map_variable} entry {raw!r}: {exc}")
        if len(tokens) != 2:
            raise AssertionError(f"unparsed map entry in {map_variable}: {raw!r}")
        key, value = tokens
        if key == "default":
            default = value
            continue
        if key.startswith("~"):
            entries.append(("regex", key[1:], value))
        else:
            entries.append(("literal", key, value))
    return default, entries


def _pcre_to_python(pattern: str) -> str:
    """把 nginx/PCRE 命名组 (?<name>...) 归一为 Python re 的 (?P<name>...)。"""
    return re.sub(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>", r"(?P<\1>", pattern)


def simulate_map(default: str, entries: list[tuple[str, str, str]], source_value: str) -> str:
    """按 nginx map 语义求值：字面量优先、正则按序；结果变量取同名捕获组。"""
    for kind, key, value in entries:
        if kind == "literal" and source_value == key:
            return value
    for kind, key, value in entries:
        if kind != "regex":
            continue
        found = re.search(_pcre_to_python(key), source_value)
        if not found:
            continue
        if value.startswith("$"):
            return found.groupdict().get(value[1:], "")
        return value
    return default


def collect_deprecated_uris() -> list[str]:
    """从 routes.py 提取所有调用 _deprecated_run_response 的具体请求路径。"""
    source = ROUTES_PY.read_text(encoding="utf-8")
    uris: list[str] = []
    lines = source.splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].startswith("@rag_eval_bp.route("):
            index += 1
            continue
        paths: list[str] = []
        while index < len(lines) and lines[index].startswith("@rag_eval_bp.route("):
            found = re.search(r'@rag_eval_bp\.route\("([^"]+)"', lines[index])
            if found:
                paths.append(found.group(1))
            index += 1
        body: list[str] = []
        while index < len(lines) and not lines[index].startswith("@rag_eval_bp.route("):
            body.append(lines[index])
            index += 1
        # 必须匹配 return 调用形态，避免把第 381 行的函数定义行误判为弃用端点。
        if any(line.strip().startswith("return _deprecated_run_response(")
               for line in body):
            uris.extend(_concrete_uri(path) for path in paths)
    return uris


def _concrete_uri(flask_path: str) -> str:
    """把 Flask 路由模板替换为可参与 nginx 正则匹配的具体样例 URI。"""

    def replace(match: re.Match[str]) -> str:
        converter = match.group(1).split(":")[-1]
        return _PLACEHOLDER_SAMPLES.get(converter, f"seg_{converter}")

    return BLUEPRINT_PREFIX + re.sub(r"<([^>]+)>", replace, flask_path)


class StagingGatewayDeprecationLogTests(unittest.TestCase):
    """锁定弃用 JSONL 的记录条件、route 映射完整性与隐私字段。"""

    def setUp(self) -> None:
        self.conf = NGINX_CONF.read_text(encoding="utf-8")
        self.deprecated_default, self.deprecated_entries = parse_map_entries(
            self.conf, "$deprecated_route")
        self.observed_default, self.observed_entries = parse_map_entries(
            self.conf, "$observed_candidate_route")

    def test_every_backend_deprecated_endpoint_resolves_to_family_label(self) -> None:
        """后端每个发 Deprecation 头的路径都必须命中家族标签，而非空或 unmapped。"""
        uris = collect_deprecated_uris()
        self.assertGreaterEqual(len(uris), 26)
        resolved = {simulate_map(self.deprecated_default, self.deprecated_entries, uri)
                    for uri in uris}
        for label in resolved:
            self.assertTrue(label.endswith("/*"),
                            f"{label} is not a masked family label")
        self.assertTrue(FAMILY_LABELS.issubset(resolved),
                        f"missing families: {FAMILY_LABELS - resolved}")

    def test_rebound_import_is_not_swallowed_by_candidate_family(self) -> None:
        """rebound-import 必须先于 candidate-runs 家族命中，保留精确路由。"""
        candidate = OBSERVATION_CANDIDATES[-1]
        self.assertEqual(
            simulate_map(self.deprecated_default, self.deprecated_entries, candidate),
            candidate)

    def test_observation_candidates_are_exactly_locked(self) -> None:
        """45 天规则对象固定为五个候选路由；增删必须显式修改本测试。"""
        observed = {simulate_map(self.observed_default, self.observed_entries, candidate)
                    for candidate in OBSERVATION_CANDIDATES}
        self.assertEqual(observed, set(OBSERVATION_CANDIDATES))
        listed = [value for _, _, value in self.observed_entries]
        self.assertEqual(sorted(listed), sorted(OBSERVATION_CANDIDATES))

    def test_canonical_lifecycle_is_not_logged_as_deprecated(self) -> None:
        """canonical /isolated/runs 路径不属于任何弃用面。"""
        canonical = f"{BLUEPRINT_PREFIX}/isolated/runs/rag_evalrun0123abcdef/result"
        self.assertEqual(simulate_map(self.observed_default, self.observed_entries,
                                      canonical), "")
        self.assertFalse(simulate_map(self.deprecated_default,
                                      self.deprecated_entries, canonical))

    def test_gate_truth_table_and_label_precedence(self) -> None:
        """上游头或候选命中任一即写日志；两者皆否关闭；家族标签优先于候选。"""
        gate_default, gate_entries = parse_map_entries(
            self.conf, "$deprecation_log_enabled")
        truth = {"0:0": "0", "0:1": "1", "1:0": "1", "1:1": "1"}
        for source, expected in truth.items():
            self.assertEqual(simulate_map(gate_default, gate_entries, source), expected)

        label_default, label_entries = parse_map_entries(
            self.conf, "$logged_deprecation_route")
        self.assertEqual(
            simulate_map(label_default, label_entries,
                         "/api/rag_eval/isolated/evaluation-runs/*:"),
            "/api/rag_eval/isolated/evaluation-runs/*")
        self.assertEqual(
            simulate_map(label_default, label_entries, ":/api/rag_eval/status"),
            "/api/rag_eval/status")
        self.assertEqual(simulate_map(label_default, label_entries, ":"), "unmapped")

    def test_log_format_privacy_and_gate_wiring(self) -> None:
        """JSONL 保持白名单字段；access_log 由合并门禁变量控制。"""
        format_line = next(line for line in self.conf.splitlines()
                           if "log_format deprecation_json" in line)
        for field in ("timestamp", "request_id", "method", "route",
                      "deprecation", "status"):
            self.assertIn(field, format_line)
        # 禁止项只约束落盘格式行；$uri 允许作为 map 内部分类源，
        # 但绝不允许把原始 URI 或其等价变量写进 JSONL。
        forbidden = ("$uri", "$args", "$is_args", "$query_string",
                     "$http_cookie", "$remote_addr", "$http_user_agent",
                     "$request_body")
        for token in forbidden:
            self.assertNotIn(token, format_line)
        self.assertIsNone(re.search(r"\$request(?![A-Za-z_])", format_line),
                          "log_format must not contain raw $request")
        access_line = next(line for line in self.conf.splitlines()
                           if "access_log /var/log/nginx/deprecation.jsonl" in line)
        self.assertIn("if=$deprecation_log_enabled", access_line)

    def test_all_map_regexes_compile(self) -> None:
        """映射中的正则必须可编译，避免上线后静默失配。"""
        for variable in ("$deprecated_route", "$observed_candidate_route",
                         "$request_id_from_gateway"):
            _, entries = parse_map_entries(self.conf, variable)
            for kind, key, _ in entries:
                if kind == "regex":
                    re.compile(_pcre_to_python(key))

    def test_request_id_rule_matches_application_layer(self) -> None:
        """网关放行正则必须与 REQUEST_ID_PATTERN 字面一致；兜底 ID 也要合法。"""
        source = REQUEST_CONTEXT_PY.read_text(encoding="utf-8")
        found = re.search(r'REQUEST_ID_PATTERN = re\.compile\(r"([^"]+)"\)', source)
        self.assertIsNotNone(found, "REQUEST_ID_PATTERN not found")
        app_pattern = found.group(1)
        _, entries = parse_map_entries(self.conf, "$request_id_from_gateway")
        gateway_patterns = [key for kind, key, _ in entries if kind == "regex"]
        self.assertEqual(gateway_patterns, [app_pattern])
        # 网关自生成的 $request_id 是 32 位十六进制，必须被应用层沿用而非重生。
        self.assertRegex("0123456789abcdef0123456789abcdef", app_pattern)

    def test_sse_read_timeout_exceeds_nginx_default(self) -> None:
        """读超时必须显式大于 nginx 默认 60s，长静默 SSE 才不会被网关切断。"""
        found = re.search(r"proxy_read_timeout\s+(\d+)s;", self.conf)
        self.assertIsNotNone(found, "proxy_read_timeout missing in location /")
        self.assertGreaterEqual(int(found.group(1)), 300)


if __name__ == "__main__":
    unittest.main()
