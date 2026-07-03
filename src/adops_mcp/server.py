"""The FastMCP app: registers the ad-ops tool surface and wires the backend.

Tool descriptions are written for an LLM caller — imperative, explicit about side
effects, and pointing bulk changes through the safe preview/apply flow. The
headline tools are ``preview_rule`` (dry run, returns a confirm token) and
``apply_rule`` (executes only with a matching, non-stale token).
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from adops_mcp import safety
from adops_mcp.backends.base import CampaignNotFound
from adops_mcp.config import get_backend
from adops_mcp.models import (
    ActionResult,
    ApplyResult,
    Campaign,
    CampaignStats,
    CampaignStatus,
    MutationResult,
    PlannedAction,
    RejectedAction,
    RulePreview,
)
from adops_mcp.rules.engine import evaluate
from adops_mcp.rules.schema import ActionType, RuleSet

mcp = FastMCP("adops")
backend = get_backend()


# --- Helpers ---------------------------------------------------------------


def _parse_status(status: Optional[str]) -> Optional[CampaignStatus]:
    if status is None:
        return None
    try:
        return CampaignStatus(status.strip().upper())
    except ValueError as exc:
        raise ValueError("status must be 'RUNNING' or 'PAUSED'") from exc


def _get_or_raise(campaign_id: str) -> Campaign:
    try:
        return backend.get_campaign(campaign_id)
    except CampaignNotFound as exc:
        raise ValueError(str(exc)) from exc


def _execute_planned(action: PlannedAction) -> ActionResult:
    """Apply one already-validated planned action against the backend."""
    try:
        if action.action is ActionType.pause:
            backend.pause_campaign(action.campaign_id)
        elif action.action is ActionType.resume:
            backend.resume_campaign(action.campaign_id)
        elif action.action is ActionType.set_bid:
            backend.set_campaign_bid(action.campaign_id, action.after)  # type: ignore[arg-type]
        elif action.action is ActionType.set_budget:
            backend.set_campaign_budget(action.campaign_id, action.after)  # type: ignore[arg-type]
        return ActionResult(
            campaign_id=action.campaign_id,
            campaign_name=action.campaign_name,
            action=action.action,
            success=True,
            detail=action.description,
        )
    except CampaignNotFound as exc:
        return ActionResult(
            campaign_id=action.campaign_id,
            campaign_name=action.campaign_name,
            action=action.action,
            success=False,
            detail=f"skipped: {exc}",
        )


def _log_single(
    campaign_before: Campaign,
    campaign_after: Campaign,
    action: ActionType,
    field: Optional[str],
    before: Optional[float],
    after: Optional[float],
    reason: str,
) -> None:
    safety.log_actions(
        [
            PlannedAction(
                campaign_id=campaign_after.id,
                campaign_name=campaign_after.name,
                action=action,
                field=field,
                before=before,
                after=after,
                reason=reason,
                description=f"{action.value} via direct tool call",
            )
        ],
        status="executed",
        backend_label=backend.label,
    )


# --- Read-only tools -------------------------------------------------------


@mcp.tool()
def list_campaigns(status: Optional[str] = None) -> list[Campaign]:
    """List campaigns with their latest stats. Read-only and always safe.

    Optionally filter by status: 'RUNNING' or 'PAUSED'. Each campaign includes
    cpc, daily_budget, spend, impressions, clicks, conversions, and computed
    cpa and ctr (ctr is a percentage).
    """
    return backend.list_campaigns(_parse_status(status))


@mcp.tool()
def get_campaign(campaign_id: str) -> Campaign:
    """Get one campaign plus its latest stats by id. Read-only and always safe."""
    return _get_or_raise(campaign_id)


@mcp.tool()
def get_performance_report(
    start_date: str, end_date: str, dimension: str = "campaign"
) -> list[CampaignStats]:
    """Get a normalized performance report over a date range (yyyy-mm-dd).

    Returns one row per campaign with spend, impressions, clicks, conversions,
    cpa, ctr, and cpc. Read-only and always safe.
    """
    return backend.get_performance_report(start_date, end_date, dimension)


@mcp.tool()
def get_audit_log(limit: int = 20) -> list[dict]:
    """Return the most recent planned and executed actions from the audit log.

    Each entry records timestamp, MOCK/LIVE backend, campaign, action, before/after
    values, and the human-readable reason. Read-only and always safe.
    """
    return safety.read_recent(limit)


# --- Mutating tools (single campaign) --------------------------------------


@mcp.tool()
def pause_campaign(campaign_id: str, reason: str) -> MutationResult:
    """Pause a live campaign immediately; spend stops. Use preview_rule for bulk changes.

    Writes an audit entry with the given reason.
    """
    before = _get_or_raise(campaign_id)
    after = backend.pause_campaign(campaign_id)
    _log_single(before, after, ActionType.pause, None, None, None, reason)
    return MutationResult(
        backend=backend.label, campaign=after, message=f"[{backend.label}] Paused '{after.name}'."
    )


@mcp.tool()
def resume_campaign(campaign_id: str, reason: str) -> MutationResult:
    """Resume a paused campaign; spend can start again. Writes an audit entry."""
    before = _get_or_raise(campaign_id)
    after = backend.resume_campaign(campaign_id)
    _log_single(before, after, ActionType.resume, None, None, None, reason)
    return MutationResult(
        backend=backend.label, campaign=after, message=f"[{backend.label}] Resumed '{after.name}'."
    )


@mcp.tool()
def set_campaign_bid(campaign_id: str, cpc: float, reason: str) -> MutationResult:
    """Set a campaign's CPC bid to an absolute value. Writes an audit entry.

    Refuses non-positive or out-of-range bids (see the safety guardrails).
    """
    before = _get_or_raise(campaign_id)
    guard = safety.check_planned_action(
        PlannedAction(
            campaign_id=before.id,
            campaign_name=before.name,
            action=ActionType.set_bid,
            field="cpc",
            before=before.cpc,
            after=cpc,
            reason=reason,
            description="direct set_bid",
        )
    )
    if guard:
        raise ValueError(f"Refused by guardrail: {guard}")
    after = backend.set_campaign_bid(campaign_id, cpc)
    _log_single(before, after, ActionType.set_bid, "cpc", before.cpc, after.cpc, reason)
    return MutationResult(
        backend=backend.label,
        campaign=after,
        message=f"[{backend.label}] Set bid on '{after.name}' to ${after.cpc:.2f}.",
    )


@mcp.tool()
def set_campaign_budget(campaign_id: str, daily_budget: float, reason: str) -> MutationResult:
    """Set a campaign's daily budget to an absolute value. Writes an audit entry.

    Refuses non-positive or out-of-range budgets (see the safety guardrails).
    """
    before = _get_or_raise(campaign_id)
    guard = safety.check_planned_action(
        PlannedAction(
            campaign_id=before.id,
            campaign_name=before.name,
            action=ActionType.set_budget,
            field="daily_budget",
            before=before.daily_budget,
            after=daily_budget,
            reason=reason,
            description="direct set_budget",
        )
    )
    if guard:
        raise ValueError(f"Refused by guardrail: {guard}")
    after = backend.set_campaign_budget(campaign_id, daily_budget)
    _log_single(
        before, after, ActionType.set_budget, "daily_budget", before.daily_budget, after.daily_budget, reason
    )
    return MutationResult(
        backend=backend.label,
        campaign=after,
        message=f"[{backend.label}] Set daily budget on '{after.name}' to ${after.daily_budget:.2f}.",
    )


# --- Rule layer (the headline tools) ---------------------------------------


@mcp.tool()
def preview_rule(ruleset: RuleSet) -> RulePreview:
    """Dry-run a RuleSet against current campaigns and return what WOULD happen.

    This is the headline, always-safe tool. You (the MCP client) translate the
    user's plain-English instruction into a structured RuleSet and pass it here.
    The server evaluates it deterministically and returns the matching campaigns,
    a human-readable reason for each match, the exact resolved change, and a
    confirm_token. NOTHING is executed. To apply, call apply_rule with the token.

    Guardrail-blocked actions appear under `rejected` and are excluded from the token.
    """
    campaigns = backend.list_campaigns()
    planned = evaluate(ruleset, campaigns)

    actions: list[PlannedAction] = []
    rejected: list[RejectedAction] = []
    for action in planned:
        block = safety.check_planned_action(action)
        if block:
            rejected.append(
                RejectedAction(
                    campaign_id=action.campaign_id,
                    campaign_name=action.campaign_name,
                    attempted=action.description,
                    reason=block,
                )
            )
        else:
            actions.append(action)

    token = safety.compute_confirm_token(actions)
    safety.log_actions(actions, status="planned", backend_label=backend.label)

    summary = (
        f"[{backend.label}] {len(actions)} action(s) would run"
        + (f", {len(rejected)} blocked by guardrails" if rejected else "")
        + ". Review, then call apply_rule with the confirm_token to execute."
    )
    return RulePreview(
        backend=backend.label,
        actions=actions,
        rejected=rejected,
        confirm_token=token,
        summary=summary,
    )


@mcp.tool()
def apply_rule(ruleset: RuleSet, confirm_token: str) -> ApplyResult:
    """Execute a previewed RuleSet. Requires the confirm_token from preview_rule.

    The token is a hash of the exact planned actions. If campaigns changed since
    the preview, the recomputed token won't match and this refuses to run — re-run
    preview_rule to get a fresh token. Every executed action is written to the audit log.
    """
    campaigns = backend.list_campaigns()
    planned = evaluate(ruleset, campaigns)
    actions = [a for a in planned if not safety.check_planned_action(a)]

    if safety.compute_confirm_token(actions) != confirm_token:
        raise ValueError(
            "Confirm token is stale or invalid — campaigns may have changed since preview. "
            "Run preview_rule again to get a fresh token, then apply that."
        )

    executed = [_execute_planned(a) for a in actions]
    safety.log_actions(
        [a for a, r in zip(actions, executed) if r.success],
        status="executed",
        backend_label=backend.label,
    )
    ok = sum(1 for r in executed if r.success)
    return ApplyResult(
        backend=backend.label,
        executed=executed,
        summary=f"[{backend.label}] Executed {ok}/{len(executed)} action(s).",
    )


@mcp.tool()
def reset_mock_data() -> str:
    """Re-seed the MOCK backend to its original campaigns. No-op on a LIVE backend."""
    reset = getattr(backend, "reset", None)
    if callable(reset):
        count = reset()
        return f"[MOCK] Re-seeded {count} campaigns."
    return f"[{backend.label}] reset is only available on the mock backend."


def main() -> None:
    """Console entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
