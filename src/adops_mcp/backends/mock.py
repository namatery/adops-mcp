"""The mock backend — the primary demo surface (no credentials required).

Holds a deterministic, in-memory set of ~10 campaigns seeded so that rules have
obvious winners and losers: a clear "kill" (high spend, zero conversions), clear
"scale" candidates (low CPA), and low-CTR campaigns worth a bid cut. Mutations
update the in-memory state so the effect is visible on the next read; ``reset()``
re-seeds. Nothing here uses the clock at read time, so the engine stays
reproducible.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..models import Campaign, CampaignStats, CampaignStatus
from .base import AdPlatform, CampaignNotFound

# A fixed timestamp keeps seed data deterministic across restarts and test runs.
_SEED_TS = datetime(2026, 6, 27, 9, 0, tzinfo=timezone.utc)


def _seed_campaigns() -> dict[str, Campaign]:
    """Build the seeded campaign set. Designed to be legible at a glance."""
    rows = [
        # id, name, status, cpc, daily_budget, spend, impressions, clicks, conversions
        # --- clear KILLs: over $50 spend, zero conversions ---
        ("1001", "Holiday Gift Guide - Desktop", CampaignStatus.RUNNING, 0.30, 50.0, 63.40, 48000, 210, 0),
        ("1002", "Cold Prospecting - Broad", CampaignStatus.RUNNING, 0.20, 60.0, 71.50, 130000, 400, 0),
        # --- clear SCALEs: healthy low CPA ---
        ("1003", "Retargeting - Warm Cart", CampaignStatus.RUNNING, 0.45, 100.0, 88.00, 22000, 510, 12),
        ("1004", "Winter Sale - Mobile", CampaignStatus.RUNNING, 0.25, 75.0, 112.00, 60000, 900, 8),
        ("1005", "Lookalike Audience - 1%", CampaignStatus.RUNNING, 0.28, 80.0, 33.00, 50000, 150, 4),
        # --- low CTR: bid-cut candidates ---
        ("1006", "Brand Awareness - Native", CampaignStatus.RUNNING, 0.15, 40.0, 38.00, 95000, 300, 2),
        # --- expensive CPA: borderline ---
        ("1007", "Clearance Deals - Desktop", CampaignStatus.RUNNING, 0.22, 55.0, 54.20, 70000, 260, 1),
        ("1008", "Seasonal Promo - Tablet", CampaignStatus.RUNNING, 0.40, 90.0, 120.00, 38000, 600, 5),
        # --- healthy mid performer ---
        ("1009", "Product Launch - Video", CampaignStatus.RUNNING, 0.35, 120.0, 45.00, 40000, 280, 3),
        # --- already paused / idle ---
        ("1010", "Newsletter Signup - Retarget", CampaignStatus.PAUSED, 0.18, 30.0, 0.00, 0, 0, 0),
    ]
    campaigns: dict[str, Campaign] = {}
    for cid, name, status, cpc, budget, spend, impr, clicks, conv in rows:
        campaigns[cid] = Campaign(
            id=cid,
            name=name,
            status=status,
            cpc=cpc,
            daily_budget=budget,
            spend=spend,
            impressions=impr,
            clicks=clicks,
            conversions=conv,
            last_modified=_SEED_TS,
        )
    return campaigns


class MockBackend(AdPlatform):
    """In-memory implementation of :class:`AdPlatform` for credential-free demos."""

    label = "MOCK"
    is_live = False

    def __init__(self) -> None:
        self._campaigns: dict[str, Campaign] = _seed_campaigns()

    def reset(self) -> int:
        """Re-seed to the original campaign set. Returns the campaign count."""
        self._campaigns = _seed_campaigns()
        return len(self._campaigns)

    # --- Read-only ---

    def list_campaigns(self, status: Optional[CampaignStatus] = None) -> list[Campaign]:
        campaigns = list(self._campaigns.values())
        if status is not None:
            campaigns = [c for c in campaigns if c.status == status]
        # model_copy so callers can't mutate our internal state by reference.
        return [c.model_copy(deep=True) for c in campaigns]

    def get_campaign(self, campaign_id: str) -> Campaign:
        campaign = self._campaigns.get(campaign_id)
        if campaign is None:
            raise CampaignNotFound(f"No campaign with id {campaign_id!r}")
        return campaign.model_copy(deep=True)

    def get_performance_report(
        self, start_date: str, end_date: str, dimension: str = "campaign"
    ) -> list[CampaignStats]:
        # The mock snapshot represents the requested window; we echo the dates back.
        return [
            CampaignStats(
                campaign_id=c.id,
                campaign_name=c.name,
                start_date=start_date,
                end_date=end_date,
                spend=c.spend,
                impressions=c.impressions,
                clicks=c.clicks,
                conversions=c.conversions,
                cpa=c.cpa,
                ctr=c.ctr,
                cpc=c.cpc,
            )
            for c in self._campaigns.values()
        ]

    # --- Mutating ---

    def _require(self, campaign_id: str) -> Campaign:
        campaign = self._campaigns.get(campaign_id)
        if campaign is None:
            raise CampaignNotFound(f"No campaign with id {campaign_id!r}")
        return campaign

    def _touch(self, campaign: Campaign, **changes) -> Campaign:
        updated = campaign.model_copy(update={**changes, "last_modified": datetime.now(timezone.utc)})
        self._campaigns[campaign.id] = updated
        return updated.model_copy(deep=True)

    def pause_campaign(self, campaign_id: str) -> Campaign:
        return self._touch(self._require(campaign_id), status=CampaignStatus.PAUSED)

    def resume_campaign(self, campaign_id: str) -> Campaign:
        return self._touch(self._require(campaign_id), status=CampaignStatus.RUNNING)

    def set_campaign_bid(self, campaign_id: str, cpc: float) -> Campaign:
        return self._touch(self._require(campaign_id), cpc=round(cpc, 4))

    def set_campaign_budget(self, campaign_id: str, daily_budget: float) -> Campaign:
        return self._touch(self._require(campaign_id), daily_budget=round(daily_budget, 2))
