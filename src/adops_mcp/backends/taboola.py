"""The Taboola Backstage backend — real Backstage API behind the same interface.

Implemented against the verified Backstage specifics (base URL, OAuth2
client-credentials token endpoint, allowed-accounts resolution, campaigns list,
campaign-summary report, and campaign update). It shares the exact ``AdPlatform``
contract with the mock, so selecting it is a single env var (``BACKEND=taboola``).

Key Backstage facts encoded here:
* Base:  https://backstage.taboola.com/backstage/api/1.0
* Token: POST https://backstage.taboola.com/backstage/oauth/token  (NO trailing
         slash — a trailing slash returns 403), form-encoded client_credentials.
* The campaigns list does NOT carry reliable spend; spend/perf come from the
  campaign-summary report, which we merge in by campaign id.
* Mutations POST changed fields only: ``is_active`` (bool), ``cpc``, ``daily_cap``.

Network behavior: the access token is cached and refreshed on 401 (retry once);
429 honors ``Retry-After`` with exponential backoff; other 4xx surface a clean
error rather than crashing the server.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from ..models import Campaign, CampaignStats, CampaignStatus
from .base import AdPlatform, CampaignNotFound

API_BASE = "https://backstage.taboola.com/backstage/api/1.0"
TOKEN_URL = "https://backstage.taboola.com/backstage/oauth/token"  # no trailing slash!

_MAX_429_RETRIES = 3


class TaboolaError(Exception):
    """A clean, caller-facing error for a failed Backstage request."""


class TaboolaBackend(AdPlatform):
    """Live Taboola Backstage implementation of :class:`AdPlatform`."""

    label = "LIVE"
    is_live = True

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        account_id: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._account_id = account_id  # resolved lazily if None
        self._client = httpx.Client(timeout=timeout)
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0

    # --- Auth ---

    def _ensure_token(self) -> str:
        """Return a valid bearer token, fetching/refreshing if needed."""
        if self._token and time.time() < self._token_expiry:
            return self._token
        resp = self._client.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise TaboolaError(f"Token request failed ({resp.status_code}): {resp.text[:300]}")
        payload = resp.json()
        self._token = payload["access_token"]
        # Refresh a minute early to avoid races near expiry.
        self._token_expiry = time.time() + int(payload.get("expires_in", 43200)) - 60
        return self._token

    def _account(self) -> str:
        """Return the account_id to use in paths, resolving from the API if unset."""
        if self._account_id:
            return self._account_id
        data = self._api_get("/users/current/allowed-accounts")
        results = data.get("results", [])
        if not results:
            raise TaboolaError("No allowed accounts returned for these credentials.")
        # Use the string account_id (e.g. "advertiser-1"), not the numeric id or name.
        self._account_id = results[0]["account_id"]
        return self._account_id

    # --- HTTP plumbing with 401 refresh + 429 backoff ---

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{API_BASE}{path}"
        for attempt in range(_MAX_429_RETRIES + 1):
            token = self._ensure_token()
            headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
            resp = self._client.request(method, url, headers=headers, **kwargs)

            if resp.status_code == 401:
                # Token may have been revoked early — force one refresh + retry.
                self._token = None
                token = self._ensure_token()
                headers["Authorization"] = f"Bearer {token}"
                resp = self._client.request(method, url, headers=headers, **kwargs)

            if resp.status_code == 429 and attempt < _MAX_429_RETRIES:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else 2.0 ** attempt
                time.sleep(delay)
                continue

            if resp.status_code >= 400:
                raise TaboolaError(
                    f"{method} {path} failed ({resp.status_code}): {resp.text[:300]}"
                )
            return resp
        raise TaboolaError(f"{method} {path} failed after retries (rate limited).")

    def _api_get(self, path: str, params: Optional[dict] = None) -> dict:
        return self._request("GET", path, params=params).json()

    def _api_post(self, path: str, json_body: dict) -> dict:
        return self._request("POST", path, json=json_body).json()

    # --- Read-only ---

    def list_campaigns(self, status: Optional[CampaignStatus] = None) -> list[Campaign]:
        account = self._account()
        raw = self._api_get(f"/{account}/campaigns/").get("results", [])
        stats = self._stats_by_id(*self._default_window())
        campaigns = [self._normalize_campaign(c, stats) for c in raw]
        if status is not None:
            campaigns = [c for c in campaigns if c.status == status]
        return campaigns

    def get_campaign(self, campaign_id: str) -> Campaign:
        account = self._account()
        try:
            raw = self._api_get(f"/{account}/campaigns/{campaign_id}/")
        except TaboolaError as exc:
            raise CampaignNotFound(str(exc)) from exc
        stats = self._stats_by_id(*self._default_window())
        return self._normalize_campaign(raw, stats)

    def get_performance_report(
        self, start_date: str, end_date: str, dimension: str = "campaign"
    ) -> list[CampaignStats]:
        account = self._account()
        data = self._api_get(
            f"/{account}/reports/campaign-summary/dimensions/{dimension}",
            params={"start_date": start_date, "end_date": end_date},
        )
        rows: list[CampaignStats] = []
        for row in data.get("results", []):
            cid = str(row.get("campaign") or row.get("campaign_id") or row.get("id") or "")
            spend = _to_float(row.get("spent"))
            impressions = _to_int(row.get("impressions"))
            clicks = _to_int(row.get("clicks"))
            conversions = _to_int(row.get("actions") or row.get("cpa_actions_num") or row.get("conversions"))
            rows.append(
                CampaignStats(
                    campaign_id=cid,
                    campaign_name=row.get("campaign_name", ""),
                    start_date=start_date,
                    end_date=end_date,
                    spend=spend,
                    impressions=impressions,
                    clicks=clicks,
                    conversions=conversions,
                    cpa=_to_optional_float(row.get("cpa")),
                    ctr=_to_float(row.get("ctr")),
                    cpc=_to_float(row.get("cpc")),
                )
            )
        return rows

    # --- Mutating (POST changed fields only) ---

    def pause_campaign(self, campaign_id: str) -> Campaign:
        return self._update(campaign_id, {"is_active": False})

    def resume_campaign(self, campaign_id: str) -> Campaign:
        return self._update(campaign_id, {"is_active": True})

    def set_campaign_bid(self, campaign_id: str, cpc: float) -> Campaign:
        return self._update(campaign_id, {"cpc": round(cpc, 4)})

    def set_campaign_budget(self, campaign_id: str, daily_budget: float) -> Campaign:
        return self._update(campaign_id, {"daily_cap": round(daily_budget, 2)})

    def _update(self, campaign_id: str, body: dict) -> Campaign:
        account = self._account()
        raw = self._api_post(f"/{account}/campaigns/{campaign_id}/", body)
        stats = self._stats_by_id(*self._default_window())
        return self._normalize_campaign(raw, stats)

    # --- Helpers ---

    def _stats_by_id(self, start_date: str, end_date: str) -> dict[str, CampaignStats]:
        try:
            rows = self.get_performance_report(start_date, end_date, dimension="campaign")
        except TaboolaError:
            # Reports can lag or 4xx on brand-new accounts; degrade gracefully to no spend.
            return {}
        return {r.campaign_id: r for r in rows}

    def _normalize_campaign(self, raw: dict, stats: dict[str, CampaignStats]) -> Campaign:
        """Map a raw Backstage campaign + its report row into our :class:`Campaign`."""
        cid = str(raw.get("id"))
        row = stats.get(cid)
        is_active = raw.get("is_active")
        if is_active is None:
            is_active = str(raw.get("status", "")).upper() not in {"PAUSED", "STOPPED"}
        return Campaign(
            id=cid,
            name=raw.get("name", ""),
            status=CampaignStatus.RUNNING if is_active else CampaignStatus.PAUSED,
            cpc=_to_float(raw.get("cpc")),
            daily_budget=_to_float(raw.get("daily_cap")),
            spend=row.spend if row else 0.0,
            impressions=row.impressions if row else 0,
            clicks=row.clicks if row else 0,
            conversions=row.conversions if row else 0,
            last_modified=datetime.now(timezone.utc),
        )

    @staticmethod
    def _default_window() -> tuple[str, str]:
        """A sensible default report window (last 7 days) for list/get spend merge."""
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=7)
        return start.isoformat(), today.isoformat()


# --- Lenient numeric coercion (Backstage occasionally returns null/strings) ---


def _to_float(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _to_optional_float(value: Any) -> Optional[float]:
    if value in (None, "", 0, 0.0):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int:
    try:
        return int(float(value)) if value is not None else 0
    except (TypeError, ValueError):
        return 0
