"""Core domain models shared by every backend, the rule engine, and the tools.

Both the mock and the real Taboola backend normalize their data into these
models, so nothing above the backend layer needs to know which platform is live.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field

from .rules.schema import ActionType


class CampaignStatus(str, Enum):
    """Normalized run state. Taboola's ``is_active`` boolean maps onto this."""

    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


class Campaign(BaseModel):
    """A campaign plus its latest performance, normalized across backends."""

    id: str
    name: str
    status: CampaignStatus
    cpc: float = Field(description="Current cost-per-click bid, in account currency.")
    daily_budget: float = Field(description="Daily spending cap, in account currency.")
    spend: float = Field(description="Amount spent in the reporting window, in account currency.")
    impressions: int
    clicks: int
    conversions: int
    last_modified: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cpa(self) -> Optional[float]:
        """Cost per acquisition (spend / conversions); ``None`` when no conversions."""
        if self.conversions <= 0:
            return None
        return round(self.spend / self.conversions, 2)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ctr(self) -> float:
        """Click-through rate as a percentage (clicks / impressions * 100)."""
        if self.impressions <= 0:
            return 0.0
        return round(self.clicks / self.impressions * 100, 4)


class CampaignStats(BaseModel):
    """A normalized performance-report row for a campaign over a date range."""

    campaign_id: str
    campaign_name: str
    start_date: str
    end_date: str
    spend: float
    impressions: int
    clicks: int
    conversions: int
    cpa: Optional[float]
    ctr: float
    cpc: float


class PlannedAction(BaseModel):
    """A concrete, resolved action the engine would take against one campaign.

    Relative ops are already resolved to absolute values here (e.g. ``before``
    $40 → ``after`` $80), and ``reason`` explains in plain words why it matched.
    """

    campaign_id: str
    campaign_name: str
    action: ActionType
    field: Optional[str] = Field(
        default=None, description="The mutated field ('cpc' or 'daily_budget'), if any."
    )
    before: Optional[float] = Field(default=None, description="Value before the change, if any.")
    after: Optional[float] = Field(default=None, description="Value after the change, if any.")
    reason: str = Field(description="Human-readable explanation of why this campaign matched.")
    description: str = Field(description="One-line summary of what would happen.")


class RejectedAction(BaseModel):
    """An action that matched a rule but was blocked by a safety guardrail."""

    campaign_id: str
    campaign_name: str
    attempted: str = Field(description="What the rule tried to do.")
    reason: str = Field(description="Why the guardrail refused it.")


class RulePreview(BaseModel):
    """The dry-run result of ``preview_rule`` — what WOULD happen, nothing executed."""

    backend: str = Field(description="MOCK or LIVE — which data this plan was built against.")
    actions: list[PlannedAction] = Field(description="Actions that would be applied on confirm.")
    rejected: list[RejectedAction] = Field(
        default_factory=list, description="Matched actions blocked by guardrails."
    )
    confirm_token: str = Field(
        description="Pass this to apply_rule to execute. Goes stale if campaigns change."
    )
    summary: str = Field(description="Human-readable summary of the plan.")


class ActionResult(BaseModel):
    """The outcome of executing one planned action."""

    campaign_id: str
    campaign_name: str
    action: ActionType
    success: bool
    detail: str


class ApplyResult(BaseModel):
    """The result of ``apply_rule`` — what was actually executed."""

    backend: str = Field(description="MOCK or LIVE.")
    executed: list[ActionResult]
    summary: str


class MutationResult(BaseModel):
    """The result of a single mutating tool (pause/resume/set bid/set budget)."""

    backend: str = Field(description="MOCK or LIVE.")
    campaign: Campaign = Field(description="The campaign after the change.")
    message: str
