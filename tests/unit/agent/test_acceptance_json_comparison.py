"""跨存储数值比较的边界，身份、结构和离散事实仍严格比较。"""

from tests.acceptance.p2_mcp.profile_dispatch_job_acceptance import equivalent_json


def test_only_numeric_serialization_roundoff_is_tolerated():
    assert equivalent_json({"mean": 2.9766980664352998}, {"mean": 2.9766980664353})
    assert not equivalent_json({"mean": 2.97}, {"mean": 2.98})
    assert not equivalent_json({"result_ref": "pc:0"}, {"result_ref": "cdfm:0"})
    assert not equivalent_json({"valid": True}, {"valid": 1})
    assert not equivalent_json(["A", "B"], ["B", "A"])
    assert not equivalent_json({"graph": None}, {})
