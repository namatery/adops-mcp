"""Tests for the pure rule engine — the most-tested file in the repo."""

from adops_mcp.backends.mock import MockBackend
from adops_mcp.rules.engine import evaluate
from adops_mcp.rules.schema import ActionType, RuleSet


def campaigns():
    return MockBackend().list_campaigns()


def matched_ids(ruleset):
    return {a.campaign_id for a in evaluate(ruleset, campaigns())}


def test_kill_rule_matches_high_spend_zero_conversions():
    rs = RuleSet.model_validate(
        {
            "rules": [
                {
                    "conditions": [
                        {"field": "spend", "operator": ">", "value": 50, "lookback_hours": 48},
                        {"field": "conversions", "operator": "==", "value": 0, "lookback_hours": 48},
                    ],
                    "action": {"type": "pause"},
                }
            ]
        }
    )
    plan = evaluate(rs, campaigns())
    assert {a.campaign_id for a in plan} == {"1001", "1002"}
    assert all(a.action is ActionType.pause for a in plan)
    # Reason is human-readable and includes the lookback window.
    assert "spend $63.40 > $50.00 over 48h" in plan[0].reason
    assert "conversions 0 == 0" in plan[0].reason


def test_cpa_none_never_matches():
    # Campaign 1001/1002 have 0 conversions -> cpa is None -> must not match a cpa rule.
    rs = RuleSet.model_validate(
        {"rules": [{"conditions": [{"field": "cpa", "operator": "<", "value": 1000}], "action": {"type": "pause"}}]}
    )
    ids = matched_ids(rs)
    assert "1001" not in ids and "1002" not in ids


def test_scale_rule_resolves_relative_budget():
    rs = RuleSet.model_validate(
        {"rules": [{"conditions": [{"field": "cpa", "operator": "<", "value": 20}], "action": {"type": "set_budget", "multiply_budget": 2.0}}]}
    )
    plan = evaluate(rs, campaigns())
    by_id = {a.campaign_id: a for a in plan}
    # Winter Sale (1004) budget 75 -> 150
    assert by_id["1004"].before == 75.0
    assert by_id["1004"].after == 150.0
    assert by_id["1004"].field == "daily_budget"


def test_reduce_bid_pct_resolves():
    rs = RuleSet.model_validate(
        {"rules": [{"conditions": [{"field": "ctr", "operator": "<", "value": 0.5}], "action": {"type": "set_bid", "reduce_bid_pct": 15}}]}
    )
    plan = evaluate(rs, campaigns())
    by_id = {a.campaign_id: a for a in plan}
    # Brand Awareness (1006): cpc 0.15 -> 0.1275
    assert by_id["1006"].before == 0.15
    assert round(by_id["1006"].after, 4) == 0.1275


def test_multiple_rules_accumulate():
    rs = RuleSet.model_validate(
        {
            "rules": [
                {"conditions": [{"field": "conversions", "operator": "==", "value": 0}], "action": {"type": "pause"}},
                {"conditions": [{"field": "cpa", "operator": "<", "value": 10}], "action": {"type": "set_budget", "multiply_budget": 1.5}},
            ]
        }
    )
    plan = evaluate(rs, campaigns())
    actions = {a.action for a in plan}
    assert ActionType.pause in actions and ActionType.set_budget in actions


def test_no_match_returns_empty():
    rs = RuleSet.model_validate(
        {"rules": [{"conditions": [{"field": "spend", "operator": ">", "value": 1_000_000}], "action": {"type": "pause"}}]}
    )
    assert evaluate(rs, campaigns()) == []
