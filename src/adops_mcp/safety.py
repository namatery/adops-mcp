"""The safety system: dry-run gating, confirm tokens, guardrails, and the audit log.

This is a feature, not an afterthought. A media buyer must never fear the tool
silently nuked live spend, so:

* ``preview_rule`` is always a dry run — nothing here mutates.
* ``apply_rule`` requires a ``confirm_token`` that is a hash over the exact
  ordered set of planned actions. If campaigns changed since preview, the
  recomputed token won't match and the apply is refused (stale token).
* Per-action guardrails refuse implausible changes (caps live here as constants).
* Every planned and executed action is appended to a JSONL audit log.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import get_audit_log_path
from .models import PlannedAction
from .rules.schema import ActionType

# --- Guardrail caps (one place, easy to find and tune) ---
MAX_RELATIVE_MULTIPLIER = 10.0  # never raise a bid/budget to >10x its current value
MIN_BID = 0.01
MAX_BID = 10.0
MIN_BUDGET = 1.0
MAX_BUDGET = 100_000.0


def check_planned_action(action: PlannedAction) -> Optional[str]:
    """Return a refusal reason if the action violates a guardrail, else ``None``."""
    if action.action in (ActionType.pause, ActionType.resume):
        return None  # status flips are always safe

    new_value = action.after
    if new_value is None:
        return "missing resolved value"
    if new_value <= 0:
        return f"refusing non-positive {action.field} (${new_value:.2f})"

    if action.before and new_value > action.before * MAX_RELATIVE_MULTIPLIER:
        return (
            f"refusing to raise {action.field} more than {MAX_RELATIVE_MULTIPLIER:g}x "
            f"(${action.before:.2f} → ${new_value:.2f})"
        )

    if action.action is ActionType.set_bid:
        if new_value < MIN_BID:
            return f"bid ${new_value:.2f} below minimum ${MIN_BID:.2f}"
        if new_value > MAX_BID:
            return f"bid ${new_value:.2f} above maximum ${MAX_BID:.2f}"
    elif action.action is ActionType.set_budget:
        if new_value < MIN_BUDGET:
            return f"budget ${new_value:.2f} below minimum ${MIN_BUDGET:.2f}"
        if new_value > MAX_BUDGET:
            return f"budget ${new_value:,.2f} above maximum ${MAX_BUDGET:,.2f}"
    return None


def compute_confirm_token(actions: list[PlannedAction]) -> str:
    """Hash the ordered planned-action set into a short, stable confirm token.

    The token captures target, action type, field, and resolved value. If any of
    those change between preview and apply, the token changes — so a stale plan
    cannot be applied.
    """
    canonical = [
        {
            "id": a.campaign_id,
            "action": a.action.value,
            "field": a.field,
            "after": a.after,
        }
        for a in actions
    ]
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# --- Audit log (JSONL: one action per line) ---


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit_entry(action: PlannedAction, status: str, backend_label: str) -> dict:
    return {
        "timestamp": _now_iso(),
        "status": status,  # "planned" or "executed"
        "backend": backend_label,  # "MOCK" or "LIVE"
        "campaign_id": action.campaign_id,
        "campaign_name": action.campaign_name,
        "action": action.action.value,
        "field": action.field,
        "before": action.before,
        "after": action.after,
        "reason": action.reason,
    }


def log_actions(actions: list[PlannedAction], status: str, backend_label: str) -> None:
    """Append a batch of planned/executed actions to the JSONL audit log."""
    if not actions:
        return
    path: Path = get_audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for action in actions:
            fh.write(json.dumps(_audit_entry(action, status, backend_label)) + "\n")


def read_recent(limit: int = 20) -> list[dict]:
    """Return the most recent audit entries (newest last), up to ``limit``."""
    path: Path = get_audit_log_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    recent = lines[-limit:] if limit > 0 else lines
    entries: list[dict] = []
    for line in recent:
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries
