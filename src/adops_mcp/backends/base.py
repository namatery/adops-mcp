"""The ``AdPlatform`` interface — the seam everything above depends on.

Both :class:`~adops_mcp.backends.mock.MockBackend` and
:class:`~adops_mcp.backends.taboola.TaboolaBackend` implement this exact contract,
so the server, rule engine, and safety layer never know which platform is live.
Adding a new platform (Meta, TikTok, Google) is "implement this ABC and register
it in config" — that is the extensibility story.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..models import Campaign, CampaignStats, CampaignStatus


class CampaignNotFound(Exception):
    """Raised when a campaign id does not exist on the platform."""


class AdPlatform(ABC):
    """A native-ads platform exposing read + control operations over campaigns."""

    #: Short label shown in every mutating response: "MOCK" or "LIVE".
    label: str = "UNKNOWN"
    #: Whether this backend talks to a real, money-moving platform.
    is_live: bool = False

    # --- Read-only ---

    @abstractmethod
    def list_campaigns(self, status: Optional[CampaignStatus] = None) -> list[Campaign]:
        """Return all campaigns, optionally filtered by run status."""

    @abstractmethod
    def get_campaign(self, campaign_id: str) -> Campaign:
        """Return one campaign with its latest stats, or raise :class:`CampaignNotFound`."""

    @abstractmethod
    def get_performance_report(
        self, start_date: str, end_date: str, dimension: str = "campaign"
    ) -> list[CampaignStats]:
        """Return normalized per-campaign performance rows for a date range (yyyy-mm-dd)."""

    # --- Mutating ---

    @abstractmethod
    def pause_campaign(self, campaign_id: str) -> Campaign:
        """Pause a campaign; spend stops. Returns the updated campaign."""

    @abstractmethod
    def resume_campaign(self, campaign_id: str) -> Campaign:
        """Resume a paused campaign. Returns the updated campaign."""

    @abstractmethod
    def set_campaign_bid(self, campaign_id: str, cpc: float) -> Campaign:
        """Set the campaign's CPC bid. Returns the updated campaign."""

    @abstractmethod
    def set_campaign_budget(self, campaign_id: str, daily_budget: float) -> Campaign:
        """Set the campaign's daily budget. Returns the updated campaign."""
