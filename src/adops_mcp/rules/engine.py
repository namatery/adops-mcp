"""The rule engine: ``evaluate(ruleset, campaigns) -> list[PlannedAction]``.

This is a PURE function — no network, no LLM, no I/O, no clock. Given a typed
RuleSet and a snapshot of campaigns, it deterministically decides which
campaigns match, resolves relative actions (e.g. "double the budget") to
concrete numbers, and builds a human-readable reason for each. Because it is
pure, it is trivially unit-testable; this is the most-tested file in the repo.
"""

from __future__ import annotations

from typing import Optional

from ..models import Campaign, PlannedAction
from .schema import Action, ActionType, Condition, MetricField, Operator, Rule, RuleSet

# Metrics rendered as currency / percentage when we explain a match.
_MONEY_FIELDS = {MetricField.spend, MetricField.cpa, MetricField.cpc, MetricField.daily_budget}
_PCT_FIELDS = {MetricField.ctr}

_OPERATORS = {
    Operator.gt: lambda a, b: a > b,
    Operator.gte: lambda a, b: a >= b,
    Operator.lt: lambda a, b: a < b,
    Operator.lte: lambda a, b: a <= b,
    Operator.eq: lambda a, b: a == b,
}


def evaluate(ruleset: RuleSet, campaigns: list[Campaign]) -> list[PlannedAction]:
    """Evaluate every rule against every campaign and return the planned actions.

    Order is deterministic: rules in declared order, campaigns in the order given.
    A campaign may appear more than once if it matches multiple rules.
    """
    planned: list[PlannedAction] = []
    for rule in ruleset.rules:
        for campaign in campaigns:
            if _campaign_matches(rule, campaign):
                planned.append(_plan_action(rule, campaign))
    return planned


def _campaign_matches(rule: Rule, campaign: Campaign) -> bool:
    """True when every condition in the rule holds for the campaign (logical AND)."""
    return all(_condition_holds(cond, campaign) for cond in rule.conditions)


def _condition_holds(cond: Condition, campaign: Campaign) -> bool:
    """Evaluate one condition. A ``None`` metric (e.g. CPA with 0 conversions) never matches."""
    actual = _metric_value(campaign, cond.field)
    if actual is None:
        return False
    return _OPERATORS[cond.operator](actual, cond.value)


def _metric_value(campaign: Campaign, field: MetricField) -> Optional[float]:
    """Pull a metric off the campaign by its schema field name."""
    value = getattr(campaign, field.value)
    return None if value is None else float(value)


def _plan_action(rule: Rule, campaign: Campaign) -> PlannedAction:
    """Build a resolved :class:`PlannedAction` for a matching campaign."""
    action = rule.action
    reason = _format_reason(rule, campaign)

    if action.type is ActionType.pause:
        return PlannedAction(
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            action=ActionType.pause,
            reason=reason,
            description=f"Pause '{campaign.name}' (currently {campaign.status.value}).",
        )

    if action.type is ActionType.resume:
        return PlannedAction(
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            action=ActionType.resume,
            reason=reason,
            description=f"Resume '{campaign.name}' (currently {campaign.status.value}).",
        )

    if action.type is ActionType.set_bid:
        new_cpc = _resolve_bid(action, campaign.cpc)
        return PlannedAction(
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            action=ActionType.set_bid,
            field="cpc",
            before=round(campaign.cpc, 4),
            after=new_cpc,
            reason=reason,
            description=f"Set bid on '{campaign.name}': ${campaign.cpc:.2f} → ${new_cpc:.2f}.",
        )

    # set_budget
    new_budget = _resolve_budget(action, campaign.daily_budget)
    return PlannedAction(
        campaign_id=campaign.id,
        campaign_name=campaign.name,
        action=ActionType.set_budget,
        field="daily_budget",
        before=round(campaign.daily_budget, 2),
        after=new_budget,
        reason=reason,
        description=(
            f"Set daily budget on '{campaign.name}': "
            f"${campaign.daily_budget:.2f} → ${new_budget:.2f}."
        ),
    )


def _resolve_bid(action: Action, current: float) -> float:
    """Resolve a (possibly relative) bid action to an absolute CPC, rounded to cents."""
    if action.cpc is not None:
        return round(action.cpc, 4)
    if action.multiply_bid is not None:
        return round(current * action.multiply_bid, 4)
    if action.increase_bid_pct is not None:
        return round(current * (1 + action.increase_bid_pct / 100), 4)
    # reduce_bid_pct (guaranteed present by schema validation)
    return round(current * (1 - action.reduce_bid_pct / 100), 4)


def _resolve_budget(action: Action, current: float) -> float:
    """Resolve a (possibly relative) budget action to an absolute daily budget."""
    if action.daily_budget is not None:
        return round(action.daily_budget, 2)
    if action.multiply_budget is not None:
        return round(current * action.multiply_budget, 2)
    if action.increase_budget_pct is not None:
        return round(current * (1 + action.increase_budget_pct / 100), 2)
    # reduce_budget_pct (guaranteed present by schema validation)
    return round(current * (1 - action.reduce_budget_pct / 100), 2)


def _format_reason(rule: Rule, campaign: Campaign) -> str:
    """Build a plain-words reason, e.g. 'spend $63.40 > $50 and conversions 0 over 48h'."""
    return " and ".join(_format_condition(cond, campaign) for cond in rule.conditions)


def _format_condition(cond: Condition, campaign: Campaign) -> str:
    actual = _metric_value(campaign, cond.field)
    actual_str = _format_metric(cond.field, actual)
    value_str = _format_metric(cond.field, cond.value)
    window = f" over {cond.lookback_hours}h" if cond.lookback_hours else ""
    return f"{cond.field.value} {actual_str} {cond.operator.value} {value_str}{window}"


def _format_metric(field: MetricField, value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if field in _MONEY_FIELDS:
        return f"${value:,.2f}"
    if field in _PCT_FIELDS:
        return f"{value:g}%"
    # counts (conversions, clicks, impressions)
    return f"{int(value)}" if float(value).is_integer() else f"{value:g}"
