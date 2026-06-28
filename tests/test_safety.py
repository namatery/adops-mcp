"""Tests for the safety layer: confirm tokens, guardrails, and preview/apply flow."""

import pytest

from adops_mcp import safety, server
from adops_mcp.models import CampaignStatus, PlannedAction
from adops_mcp.rules.schema import ActionType, RuleSet


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Write the audit log to a temp file and re-seed the mock before each test."""
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    server.backend.reset()
    yield


def _planned(action, before=None, after=None, field=None):
    return PlannedAction(
        campaign_id="x", campaign_name="X", action=action, field=field,
        before=before, after=after, reason="r", description="d",
    )


# --- Confirm token ---------------------------------------------------------


def test_token_is_stable():
    actions = [_planned(ActionType.set_budget, 40, 80, "daily_budget")]
    assert safety.compute_confirm_token(actions) == safety.compute_confirm_token(actions)


def test_token_changes_when_resolved_value_changes():
    a = [_planned(ActionType.set_budget, 40, 80, "daily_budget")]
    b = [_planned(ActionType.set_budget, 40, 120, "daily_budget")]
    assert safety.compute_confirm_token(a) != safety.compute_confirm_token(b)


# --- Guardrails ------------------------------------------------------------


def test_guardrail_blocks_huge_multiplier():
    # 40 -> 800 is 20x, above the 10x cap.
    assert safety.check_planned_action(_planned(ActionType.set_budget, 40, 800, "daily_budget"))


def test_guardrail_blocks_non_positive_bid():
    assert safety.check_planned_action(_planned(ActionType.set_bid, 0.5, 0, "cpc"))


def test_guardrail_allows_reasonable_change():
    assert safety.check_planned_action(_planned(ActionType.set_budget, 40, 80, "daily_budget")) is None


def test_status_flips_always_allowed():
    assert safety.check_planned_action(_planned(ActionType.pause)) is None


# --- preview / apply integration ------------------------------------------

KILL_RULE = {
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

SCALE_RULE = {
    "rules": [{"conditions": [{"field": "cpa", "operator": "<", "value": 20}], "action": {"type": "set_budget", "multiply_budget": 2.0}}]
}


def test_preview_does_not_mutate():
    before = server.backend.get_campaign("1001").status
    preview = server.preview_rule(RuleSet.model_validate(KILL_RULE))
    assert len(preview.actions) == 2
    assert server.backend.get_campaign("1001").status == before  # unchanged


def test_apply_with_valid_token_executes_and_audits():
    rs = RuleSet.model_validate(KILL_RULE)
    preview = server.preview_rule(rs)
    result = server.apply_rule(rs, preview.confirm_token)
    assert all(r.success for r in result.executed)
    assert server.backend.get_campaign("1001").status is CampaignStatus.PAUSED
    # Audit log has both planned and executed entries.
    entries = safety.read_recent(50)
    statuses = {e["status"] for e in entries}
    assert "planned" in statuses and "executed" in statuses


def test_stale_token_is_refused():
    rs = RuleSet.model_validate(SCALE_RULE)
    preview = server.preview_rule(rs)
    # Change a matched campaign so the recomputed plan differs from the preview.
    server.backend.set_campaign_budget("1004", 999.0)
    with pytest.raises(ValueError, match="stale or invalid"):
        server.apply_rule(rs, preview.confirm_token)


def test_guardrail_rejection_surfaces_in_preview():
    rs = RuleSet.model_validate(
        {"rules": [{"conditions": [{"field": "cpa", "operator": "<", "value": 20}], "action": {"type": "set_budget", "multiply_budget": 50.0}}]}
    )
    preview = server.preview_rule(rs)
    assert preview.actions == []
    assert len(preview.rejected) > 0
