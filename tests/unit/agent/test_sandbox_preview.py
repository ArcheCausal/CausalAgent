"""纯函数验证受限预览；不替换 SDK，也不访问沙盒服务。"""

import pytest

from Agent.data_processing.opensandbox_runtime import _decode_preview


@pytest.mark.parametrize(
    ("text", "max_bytes"),
    [("", 1), ("a,b\n1,2\n", 8), ("中文🙂", 10), ("中文🙂", 32)],
)
def test_complete_preview_preserves_utf8_text(text: str, max_bytes: int) -> None:
    assert _decode_preview(text.encode("utf-8"), max_bytes) == text


def test_large_preview_has_a_bounded_body_and_an_explicit_marker() -> None:
    preview = _decode_preview(b"a" * (1024 * 1024), 32)

    body, marker = preview.split("\n")
    assert body == "a" * 32
    assert marker == "[Preview truncated at the configured byte limit]"


@pytest.mark.parametrize("character", ["é", "中", "🙂"])
def test_truncation_preserves_only_complete_utf8_characters(character: str) -> None:
    payload = ("a" + character + "z").encode("utf-8")

    for max_bytes in range(1, len(payload)):
        preview = _decode_preview(payload[: max_bytes + 1], max_bytes)
        body, marker = preview.split("\n")
        expected = "a" if max_bytes < len(payload) - 1 else "a" + character
        assert body == expected
        assert len(body.encode("utf-8")) <= max_bytes
        assert "truncated" in marker
        assert "�" not in preview


@pytest.mark.parametrize("payload", [b"abc\xffxyz", b"\x80abc", b"\xe4xabc"])
def test_truncated_preview_rejects_invalid_utf8_before_the_boundary(payload: bytes) -> None:
    with pytest.raises(ValueError, match="not UTF-8"):
        _decode_preview(payload, len(payload) - 1)


@pytest.mark.parametrize("payload", [b"abc\xc3", b"abc\xe4\xb8", b"abc\xf0\x9f\x99"])
def test_incomplete_utf8_at_eof_is_rejected(payload: bytes) -> None:
    with pytest.raises(ValueError, match="not UTF-8"):
        _decode_preview(payload, len(payload))


def test_probe_byte_is_not_part_of_the_preview_text() -> None:
    preview = _decode_preview(b"abc\xff", 3)

    assert preview == "abc\n[Preview truncated at the configured byte limit]"
