import pytest

from general_agent_eval import cli


def test_parse_picks_accepts_commas_and_spaces():
    assert cli._parse_picks("1,3", 3) == [0, 2]
    assert cli._parse_picks("2 1", 3) == [1, 0]


@pytest.mark.parametrize("raw", ["", "x", "0", "4", "1,1"])
def test_parse_picks_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        cli._parse_picks(raw, 3)


def test_expand_all_and_plain():
    assert cli._expand_list(["all"], [10, 20]) == [10, 20]
    assert cli._expand_list([10], [10, 20]) == [10]
    assert cli._expand_csv("Linear,CPT", ["Linear"]) == ["Linear", "CPT"]
    assert cli._expand_csv("all", ["Linear", "CPT"]) == ["Linear", "CPT"]


def test_estimate_text_uses_constants():
    config = {"estimate": {"seconds_per_case": 600, "cost_usd_per_case": 0.6}}
    text = cli.estimate_text(3, config)
    assert "30.0 分钟" in text
    assert "$1.80" in text


def test_estimate_text_falls_back_to_defaults():
    text = cli.estimate_text(1, {})
    assert "10.0 分钟" in text
    assert "$0.60" in text
