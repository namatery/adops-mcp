"""Tests for the mock backend (the default demo surface)."""

import pytest

from adops_mcp.backends.base import CampaignNotFound
from adops_mcp.backends.mock import MockBackend
from adops_mcp.models import CampaignStatus


def test_seed_count_and_status_filter():
    b = MockBackend()
    assert len(b.list_campaigns()) == 10
    running = b.list_campaigns(CampaignStatus.RUNNING)
    paused = b.list_campaigns(CampaignStatus.PAUSED)
    assert len(running) == 9 and len(paused) == 1
    assert paused[0].id == "1010"


def test_computed_cpa_and_ctr():
    b = MockBackend()
    winter = b.get_campaign("1004")  # spend 112 / 8 conversions = 14.0
    assert winter.cpa == 14.0
    holiday = b.get_campaign("1001")  # 0 conversions -> cpa None
    assert holiday.cpa is None
    assert holiday.ctr == round(210 / 48000 * 100, 4)


def test_get_unknown_campaign_raises():
    with pytest.raises(CampaignNotFound):
        MockBackend().get_campaign("does-not-exist")


def test_mutations_persist():
    b = MockBackend()
    b.pause_campaign("1001")
    assert b.get_campaign("1001").status is CampaignStatus.PAUSED
    b.set_campaign_bid("1001", 0.99)
    assert b.get_campaign("1001").cpc == 0.99
    b.set_campaign_budget("1001", 250.0)
    assert b.get_campaign("1001").daily_budget == 250.0


def test_list_returns_copies_not_references():
    b = MockBackend()
    c = b.list_campaigns()[0]
    c.cpc = 99.0
    assert b.get_campaign(c.id).cpc != 99.0  # internal state untouched


def test_reset_reseeds():
    b = MockBackend()
    b.pause_campaign("1001")
    assert b.reset() == 10
    assert b.get_campaign("1001").status is CampaignStatus.RUNNING


def test_performance_report_shape():
    b = MockBackend()
    rows = b.get_performance_report("2026-06-01", "2026-06-28")
    assert len(rows) == 10
    row = next(r for r in rows if r.campaign_id == "1004")
    assert row.start_date == "2026-06-01" and row.end_date == "2026-06-28"
    assert row.cpa == 14.0
