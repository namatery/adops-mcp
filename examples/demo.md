# 2-minute scripted demo

This is the exact walkthrough to run in your MCP client (Claude Desktop, Claude Code, Codex, or another MCP client) against the **mock
backend** - no credentials needed. It doubles as the script for the recorded demo. Outputs below are real,
captured from the seeded mock data.

## Setup

```bash
uv sync
claude mcp add adops -- uv run --directory "$(pwd)" python -m adops_mcp
# or point Claude Desktop / Codex at the server (see README)
```

Optional: start clean any time by asking *"reset the mock data"* (calls `reset_mock_data`).

---

## 1. Look at the campaigns

**You:** *"List my campaigns."*

Claude calls `list_campaigns`. You'll see ~10 seeded campaigns. Two stand out as obvious waste:

| Campaign | Status | Spend | Conversions | CPA |
|----------|--------|------:|------------:|----:|
| Holiday Gift Guide - Desktop | RUNNING | $63.40 | 0 | n/a |
| Cold Prospecting - Broad | RUNNING | $71.50 | 0 | n/a |
| Retargeting - Warm Cart | RUNNING | $88.00 | 12 | $7.33 |
| Winter Sale - Mobile | RUNNING | $112.00 | 8 | $14.00 |

---

## 2. Preview a "kill" rule (dry run - nothing happens yet)

**You:** *"Preview: pause anything spending over $50 with no conversions in the last 48 hours."*

Claude translates this into a typed RuleSet and calls `preview_rule`. The server returns:

```
[MOCK] 2 action(s) would run. Review, then call apply_rule with the confirm_token to execute.
  - Pause 'Holiday Gift Guide - Desktop' (currently RUNNING).
      - spend $63.40 > $50.00 over 48h and conversions 0 == 0 over 48h
  - Pause 'Cold Prospecting - Broad' (currently RUNNING).
      - spend $71.50 > $50.00 over 48h and conversions 0 == 0 over 48h
  confirm_token: aff1180e9f6f4b58
```

Note the **plain-English reason** on each match and that **nothing was changed**. This is a dry run.

---

## 3. Apply it (two-step confirm)

**You:** *"Looks right - apply it."*

Claude calls `apply_rule` with the `confirm_token` from the preview:

```
[MOCK] Executed 2/2 action(s).
```

Ask *"list running campaigns"* again and the two wasteful campaigns are now PAUSED.

> If the campaigns had changed between preview and apply, the token would be stale and the server would
> refuse: "run preview_rule again to get a fresh token." That's the safety gate.

---

## 4. Scale the winners

**You:** *"Double the daily budget on any campaign with CPA under $20, preview first."*

`preview_rule` shows resolved before/after numbers, e.g.:

```
  - Set daily budget on 'Retargeting - Warm Cart': $100.00 -> $200.00. cpa $7.33 < $20.00
  - Set daily budget on 'Winter Sale - Mobile':    $75.00  -> $150.00. cpa $14.00 < $20.00
```

**You:** *"Apply."* - budgets updated.

---

## 5. Show the audit trail

**You:** *"Show me the audit log."*

Claude calls `get_audit_log`. Every planned and executed action is there with timestamp, MOCK/LIVE flag,
before/after values, and the reason:

```json
{"status": "planned",  "backend": "MOCK", "campaign_name": "Cold Prospecting - Broad", "action": "pause"}
{"status": "executed", "backend": "MOCK", "campaign_name": "Holiday Gift Guide - Desktop", "action": "pause"}
{"status": "executed", "backend": "MOCK", "campaign_name": "Cold Prospecting - Broad", "action": "pause"}
```

---

That's the whole loop: **read -> preview -> confirm -> apply -> audit**, driven entirely by plain English, with
the destructive step gated and logged.
