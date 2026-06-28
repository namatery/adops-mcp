"""The RuleSet schema — the typed contract between the MCP client and this server.

This is the heart of the design. The human writes English; the MCP client
(Claude) translates that English into a :class:`RuleSet` JSON object and calls
``preview_rule(ruleset=...)``. The server NEVER parses English and makes NO second
LLM call — it only validates and deterministically evaluates this typed object.

Because this schema is what the client reads to know what JSON to emit, the field
names and docstrings below are part of the product surface: keep them clear.

Example RuleSet for "pause anything spending over $50 with no conversions in 48h"::

    {
      "description": "Kill wasteful spenders",
      "rules": [
        {
          "conditions": [
            {"field": "spend", "operator": ">", "value": 50, "lookback_hours": 48},
            {"field": "conversions", "operator": "==", "value": 0, "lookback_hours": 48}
          ],
          "action": {"type": "pause"}
        }
      ]
    }
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class MetricField(str, Enum):
    """A campaign metric a condition can compare against.

    Money fields (``spend``, ``cpa``, ``cpc``, ``daily_budget``) are in the
    account currency. ``ctr`` is a percentage (e.g. ``0.5`` means 0.5%).
    """

    spend = "spend"
    conversions = "conversions"
    cpa = "cpa"
    ctr = "ctr"
    daily_budget = "daily_budget"
    cpc = "cpc"
    clicks = "clicks"
    impressions = "impressions"


class Operator(str, Enum):
    """A comparison operator. Values are the literal symbols for legibility."""

    gt = ">"
    gte = ">="
    lt = "<"
    lte = "<="
    eq = "=="


class ActionType(str, Enum):
    """What to do to a campaign that matches a rule's conditions."""

    pause = "pause"
    resume = "resume"
    set_bid = "set_bid"
    set_budget = "set_budget"


class Condition(BaseModel):
    """A single typed comparison on one campaign metric.

    All conditions in a rule are ANDed together: a campaign matches only when
    every condition is true.
    """

    field: MetricField = Field(description="The campaign metric to compare.")
    operator: Operator = Field(description="Comparison operator: >, >=, <, <=, or ==.")
    value: float = Field(description="The threshold to compare the metric against.")
    lookback_hours: Optional[int] = Field(
        default=None,
        description=(
            "Optional reporting window in hours this condition refers to (e.g. 48). "
            "Used to label the matched reason; the current campaign snapshot is evaluated."
        ),
    )


class Action(BaseModel):
    """The action to apply to matching campaigns.

    ``pause`` / ``resume`` take no parameters. ``set_bid`` and ``set_budget``
    accept EITHER an absolute target OR exactly one relative operator, so the
    client can express "double the budget" or "drop bids 15%" without computing
    numbers itself — the engine resolves relative ops to concrete values.
    """

    type: ActionType = Field(description="Which mutating operation to perform.")

    # --- set_bid: provide exactly one of these ---
    cpc: Optional[float] = Field(default=None, description="set_bid: absolute new CPC bid.")
    multiply_bid: Optional[float] = Field(
        default=None, description="set_bid: multiply current bid (e.g. 1.2 = +20%)."
    )
    increase_bid_pct: Optional[float] = Field(
        default=None, description="set_bid: raise bid by this percent (e.g. 10 = +10%)."
    )
    reduce_bid_pct: Optional[float] = Field(
        default=None, description="set_bid: lower bid by this percent (e.g. 15 = -15%)."
    )

    # --- set_budget: provide exactly one of these ---
    daily_budget: Optional[float] = Field(
        default=None, description="set_budget: absolute new daily budget."
    )
    multiply_budget: Optional[float] = Field(
        default=None, description="set_budget: multiply current daily budget (e.g. 2.0 = double)."
    )
    increase_budget_pct: Optional[float] = Field(
        default=None, description="set_budget: raise daily budget by this percent."
    )
    reduce_budget_pct: Optional[float] = Field(
        default=None, description="set_budget: lower daily budget by this percent."
    )

    _BID_PARAMS = ("cpc", "multiply_bid", "increase_bid_pct", "reduce_bid_pct")
    _BUDGET_PARAMS = ("daily_budget", "multiply_budget", "increase_budget_pct", "reduce_budget_pct")

    @model_validator(mode="after")
    def _check_params(self) -> "Action":
        """Enforce that exactly the right parameter is present for the action type."""
        set_bid = [p for p in self._BID_PARAMS if getattr(self, p) is not None]
        set_budget = [p for p in self._BUDGET_PARAMS if getattr(self, p) is not None]

        if self.type in (ActionType.pause, ActionType.resume):
            if set_bid or set_budget:
                raise ValueError(f"{self.type.value} takes no parameters.")
        elif self.type is ActionType.set_bid:
            if set_budget:
                raise ValueError("set_bid cannot take budget parameters.")
            if len(set_bid) != 1:
                raise ValueError(
                    "set_bid requires exactly one of: " + ", ".join(self._BID_PARAMS)
                )
        elif self.type is ActionType.set_budget:
            if set_bid:
                raise ValueError("set_budget cannot take bid parameters.")
            if len(set_budget) != 1:
                raise ValueError(
                    "set_budget requires exactly one of: " + ", ".join(self._BUDGET_PARAMS)
                )
        return self


class Rule(BaseModel):
    """One rule: a set of ANDed conditions and the action to take when they match."""

    conditions: list[Condition] = Field(
        min_length=1, description="Conditions, all of which must hold for a campaign to match."
    )
    action: Action = Field(description="What to do to each matching campaign.")


class RuleSet(BaseModel):
    """A batch of rules to preview/apply against current campaigns.

    This is the single typed object the MCP client builds from the user's English
    sentence and passes to ``preview_rule`` / ``apply_rule``.
    """

    description: Optional[str] = Field(
        default=None, description="Optional human label for this batch (e.g. the original sentence)."
    )
    rules: list[Rule] = Field(min_length=1, description="One or more rules to evaluate.")
