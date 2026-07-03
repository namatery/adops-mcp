# adops-mcp — manage your ad campaigns by talking to them

An **MCP server** that connects to a native-ads platform (Taboola Backstage) and exposes campaign
reporting and control as MCP tools. A media buyer talks to their campaigns in plain English through their
MCP client (Claude Desktop / Claude Code), and gets back an **auditable, dry-run plan** that only executes
after explicit confirmation.

> **One line:** *Manage your ad campaigns by talking to them.*

---

## The three questions

**1. What does this tool do?**
It lets a media buyer manage native-ad campaigns conversationally and safely. You say *"pause anything
spending over $50 with no conversions in the last 48 hours"*; the tool shows you exactly which campaigns
would be paused and why, and pauses them only after you confirm. Read campaign performance, change bids and
budgets, and run bulk "scale & kill" rules — all through plain-English chat, with every action logged.

**2. Why did you build *this* one?**
You reached out about ad-platform MCP connectors, so I built one end-to-end. Managing campaigns across
clunky dashboards is repetitive and error-prone, and the work is mostly the same loop: *read the numbers,
decide thresholds, pause the losers, scale the winners.* That loop is a perfect fit for an MCP server: the
**LLM client is the natural-language layer** (it turns your sentence into a typed rule), and the **server is
the deterministic, safe layer** (it decides which campaigns match, builds a plan, gates execution behind a
confirm token, and audits everything). The architecture is the point — the same `AdPlatform` seam that
holds Taboola today holds Meta/TikTok/Google tomorrow with no change above the backend.

**3. What would you build next, full-time?**
- More platforms behind the same `AdPlatform` interface: Meta, TikTok, Google native.
- Scheduled rule runs (evaluate "kill" rules every morning) and anomaly detection.
- Creative-fatigue signals and bid pacing.
- A permissioned, multi-user action-approval flow on top of the existing audit log.

---

## How it works (the architecture)

We do **not** parse English in the server and we make **no** second LLM call. The MCP client (Claude) is
already a language model driving the server — it does English → `RuleSet` for free. The server's job is the
*deterministic* part. **The model picks the parameters; our code makes the decisions.** That split is the
safety story.

```
┌──────────────────────┐   plain English ("pause wasteful campaigns")
│  Media buyer          │
└──────────┬───────────┘
           ▼
┌──────────────────────┐   Claude translates English → typed RuleSet, calls tools
│  MCP client (Claude)  │
└──────────┬───────────┘
           ▼  MCP (stdio)
┌──────────────────────────────────────────────┐
│  adops-mcp server                              │
│   • tools: list/get/report, pause/resume/bid/  │
│            budget, preview_rule / apply_rule   │
│   • rules/engine.py  — PURE evaluate()         │
│   • safety.py — dry-run, confirm token, audit  │
└──────────┬─────────────────────────────────────┘
           ▼  AdPlatform interface (backends/base.py)
     ┌─────────────┬───────────────────┐
     │ MockBackend │  TaboolaBackend    │
     │ (default)   │  (BACKEND=taboola) │
     └─────────────┘───────────────────┘
```

The headline tools are **`preview_rule`** (dry run → returns matched campaigns, human reasons, and a
`confirm_token`; nothing executes) and **`apply_rule`** (executes only with a matching, non-stale token).

---

## Quickstart (zero credentials, < 2 minutes)

The server runs fully against an in-memory **mock** of ~10 campaigns. No API keys, no `.env`.

With [`uv`](https://docs.astral.sh/uv/) (recommended):

```bash
git clone <this-repo> && cd adops-mcp
uv sync                                   # install deps
uv run --extra dev pytest                 # 31 tests, fully offline
uv run mcp dev src/adops_mcp/server.py    # launch + open the MCP Inspector
```

Or with pip:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m adops_mcp            # runs the server over stdio
```

---

## Connect it to Claude Desktop / Claude Code

**Claude Desktop** — add to `claude_desktop_config.json`
(`~/Library/Application Support/Claude/` on macOS):

```json
{
  "mcpServers": {
    "adops": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/adops-mcp", "python", "-m", "adops_mcp"]
    }
  }
}
```

**Claude Code** — from the repo root:

```bash
claude mcp add adops -- uv run --directory "$(pwd)" python -m adops_mcp
```

**Codex** — from the repo root:

```bash
codex mcp add adops -- uv run --directory "$(pwd)" python -m adops_mcp
```

Then restart/open Codex in this repo and run `/mcp` to confirm that `adops` is connected.

You can also configure it manually in `~/.codex/config.toml` or project-scoped `.codex/config.toml`:

```toml
[mcp_servers.adops]
command = "uv"
args = ["run", "--directory", "/absolute/path/to/adops-mcp", "python", "-m", "adops_mcp"]
startup_timeout_sec = 20
tool_timeout_sec = 60
```

Then just talk to it: *"list my campaigns"*, *"preview: pause anything over $50 with no conversions"*,
*"apply it"*, *"show the audit log"*. See [`examples/demo.md`](examples/demo.md) for the full walkthrough
and [`examples/sample_rules.txt`](examples/sample_rules.txt) for rules to paste.

---

## Tool surface

| Tool | Kind | What it does |
|------|------|--------------|
| `list_campaigns(status?)` | read | List campaigns + stats (cpa/ctr computed) |
| `get_campaign(id)` | read | One campaign with latest stats |
| `get_performance_report(start, end, dimension?)` | read | Normalized per-campaign report rows |
| `get_audit_log(limit?)` | read | Recent planned + executed actions |
| `pause_campaign / resume_campaign(id, reason)` | write | Flip status; audited |
| `set_campaign_bid(id, cpc, reason)` | write | Set CPC; guardrailed + audited |
| `set_campaign_budget(id, daily_budget, reason)` | write | Set daily budget; guardrailed + audited |
| **`preview_rule(ruleset)`** | safe | Dry-run a RuleSet → plan + `confirm_token` |
| **`apply_rule(ruleset, confirm_token)`** | write | Execute a previewed plan (token-gated) |
| `reset_mock_data()` | util | Re-seed the mock for a fresh demo |

### Safety model
- **Dry-run by default** — `preview_rule` never mutates.
- **Two-step confirm** — `apply_rule` needs the `confirm_token`, a hash over the exact planned actions. If
  campaigns changed since preview, the token is stale and the apply is refused.
- **Guardrails** (in `safety.py`) — refuse non-positive bids/budgets and raises beyond 10×.
- **Audit log** — every planned and executed action is appended to `audit_log.jsonl` with before/after
  values and a MOCK/LIVE flag.

---

## Switch to live Taboola

`MockBackend` and `TaboolaBackend` implement the same `AdPlatform` interface, so going live is one env var.
Copy `.env.example` to `.env` and set:

```bash
BACKEND=taboola
TABOOLA_CLIENT_ID=...        # issued by your Taboola account manager
TABOOLA_CLIENT_SECRET=...
# TABOOLA_ACCOUNT_ID=advertiser-1   # optional; auto-resolved from allowed-accounts if unset
```

The client handles OAuth2 client-credentials auth (token cached, refreshed on 401), resolves the account
from `/users/current/allowed-accounts`, merges spend from the campaign-summary report (the campaigns list
doesn't carry reliable spend), and applies mutations via `is_active` / `cpc` / `daily_cap`. Implemented
against the verified Backstage API; see `src/adops_mcp/backends/taboola.py`.

---

## Project layout

```
src/adops_mcp/
  server.py            # FastMCP app: registers tools, wires the backend
  config.py            # env loading + backend selection
  models.py            # Campaign, CampaignStats, PlannedAction, RulePreview, ...
  safety.py            # dry-run gating, confirm tokens, guardrails, JSONL audit
  backends/
    base.py            # AdPlatform interface — the seam
    mock.py            # MockBackend (default, seeded)
    taboola.py         # TaboolaBackend (real Backstage API)
  rules/
    schema.py          # RuleSet / Condition / Action (the client-facing contract)
    engine.py          # evaluate(ruleset, campaigns) -> PlannedAction[]  (PURE)
tests/                 # engine, schema, mock backend, safety/token — all offline
examples/              # demo.md walkthrough + sample_rules.txt
```

## Demo deliverable
An MCP server has no public URL to share, so the demo is a **recorded walkthrough plus this runnable repo**
(the contest explicitly allows this). The exact script is in [`examples/demo.md`](examples/demo.md).

## Status / honesty notes
- The mock backend is the primary, fully-working demo surface.
- The Taboola backend is real, reviewable code written against the verified Backstage API. It has not been
  run against a live account (credentials are issued by an account manager), so treat it as production-shaped
  but un-smoke-tested against the wire.
