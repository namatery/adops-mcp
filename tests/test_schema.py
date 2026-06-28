"""Tests for the RuleSet schema validation (the client-facing contract)."""

import pytest
from pydantic import ValidationError

from adops_mcp.rules.schema import Action, ActionType, RuleSet


def _ruleset(action: dict) -> dict:
    return {"rules": [{"conditions": [{"field": "cpa", "operator": "<", "value": 20}], "action": action}]}


def test_valid_pause_ruleset():
    rs = RuleSet.model_validate(_ruleset({"type": "pause"}))
    assert rs.rules[0].action.type is ActionType.pause


def test_valid_relative_budget():
    rs = RuleSet.model_validate(_ruleset({"type": "set_budget", "multiply_budget": 2.0}))
    assert rs.rules[0].action.multiply_budget == 2.0


def test_pause_with_params_rejected():
    with pytest.raises(ValidationError):
        Action.model_validate({"type": "pause", "cpc": 0.5})


def test_set_bid_requires_exactly_one_param():
    with pytest.raises(ValidationError):
        Action.model_validate({"type": "set_bid"})  # none
    with pytest.raises(ValidationError):
        Action.model_validate({"type": "set_bid", "cpc": 0.5, "reduce_bid_pct": 10})  # two


def test_set_budget_rejects_bid_params():
    with pytest.raises(ValidationError):
        Action.model_validate({"type": "set_budget", "reduce_bid_pct": 10})


def test_empty_ruleset_rejected():
    with pytest.raises(ValidationError):
        RuleSet.model_validate({"rules": []})


def test_rule_requires_a_condition():
    with pytest.raises(ValidationError):
        RuleSet.model_validate({"rules": [{"conditions": [], "action": {"type": "pause"}}]})


def test_unknown_metric_field_rejected():
    with pytest.raises(ValidationError):
        RuleSet.model_validate(
            {"rules": [{"conditions": [{"field": "nope", "operator": "<", "value": 1}], "action": {"type": "pause"}}]}
        )
