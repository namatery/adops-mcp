## 0. TL;DR (what we are building, in two sentences)

An **MCP server** that connects to a native-ads platform (Taboola Backstage) and exposes campaign
reporting + control as MCP tools. The media buyer talks to the campaigns in plain English through their
MCP client (Claude Desktop / Claude Code) — **Claude is the natural-language layer**. Claude translates a
sentence like "pause anything spending over $50 with zero conversions in 48h" into a **structured RuleSet**
(a typed JSON object defined by this server's schema) and passes it to a `preview_rule` tool. The server
then deterministically evaluates that RuleSet against live campaign data and returns an auditable,
dry-run plan that only executes after explicit confirmation.

The pitch in one line: **"Manage your ad campaigns by talking to them."**

> **Key architectural point (read this — it's a common confusion):** We do NOT parse English inside the
> server, and we do NOT make a second LLM call. The MCP client (Claude) is already a language model driving
> this server — it does English→RuleSet for free. The server's job is the *deterministic* part: take a
> typed RuleSet, decide which campaigns match, build a plan, gate it behind confirmation, execute, audit.
> The model picks the **parameters**; our code makes the **decisions**. That split is the safety story.

---

## 1. Why this project exists (context for judgment calls)

This is a submission to a hiring "Build Contest" run by **It's Today Media**, a performance/affiliate
marketing company. Winner gets $5,000 + a full-time "Marketing Development Engineer" offer. Judges are a
**media-buying team**, not VCs. They explicitly stated they will:
- read the code (code quality matters — readable, sensible architecture, extensible),
- value problem selection ("did you pick something that matters to a marketing team?"),
- value working-ness ("ugly and functional beats beautiful and broken"),
- read a README answering three questions (see §9).

They reached out because of the author's open-source **MCP server** work. So the MCP architecture is the
differentiator and must be front-and-center — it is the reason this candidate gets hired over someone
who shipped a generic chatbot. Do not bury it.

**Design consequences of the above:**
- Correctness and clarity beat feature count. A small thing that fully works > a big thing half-wired.
- Every destructive action must be **safe by default** (dry-run, confirmation, audit log). A media buyer
  must never fear that the tool silently nuked live spend. This is itself a selling point — show it off.
- The judge must be able to run the demo in **under 2 minutes with zero credentials**. See §4 (Mock Mode).

---

## 2. Scope discipline (HARD deadline: build over ~1 week)

### IN SCOPE (build this, in this order)
1. **MCP server** exposing a clean, typed set of ad-ops tools (§5).
2. **Mock data backend** so everything runs with no API keys (§4). THIS IS PRIORITY ONE — build it first.
3. **Real Taboola Backstage backend** behind the same interface (§6), toggled by env var.
4. **Structured RuleSet schema + deterministic evaluation engine** — Claude (the client) emits the RuleSet;
   the server evaluates it and previews actions (§7).
5. **Dry-run + confirmation + audit log** safety system (§8).
6. **README** + a short scripted demo (§9).

### OUT OF SCOPE (do NOT build — note as "what's next" in README instead)
- Multi-platform (Meta/TikTok/Google). Architect so it's *easy to add*, but ship **Taboola only**.
- A web UI / dashboard. The "interface" is an MCP client (Claude Desktop / Claude Code) + a CLI.
- User auth, multi-tenant accounts, billing, persistence beyond a local SQLite/JSON audit log.
- Real-time streaming, websockets, cron scheduling. Rules are evaluated **on demand** when invoked.
- ML/predictive bidding. The rules are deterministic threshold logic, and that is the honest, correct scope.

If you find yourself gold-plating, stop and return to the list above.

---

## 3. Tech stack & conventions

- **Language:** Python 3.11+.
- **MCP framework:** the official MCP Python SDK (`mcp`), using `FastMCP` for ergonomics. If the package
  surface differs from your training data, READ the installed package — do not guess decorator/API names.
- **Package/deps:** use `uv` if available, else `pip` + `requirements.txt`. Keep deps minimal:
  `mcp`, `httpx`, `pydantic`, `python-dotenv`. Avoid heavy frameworks.
- **HTTP:** `httpx` (sync is fine; async only if the SDK pushes you there).
- **Validation:** `pydantic` v2 models for every tool input/output and for the RuleSet schema.
- **Config:** `.env` via `python-dotenv`. Never hardcode secrets. Ship `.env.example`.
- **Style:** type hints everywhere, docstrings on every public function, small focused modules.
  Prefer pure functions for rule evaluation so they are unit-testable without network.
- **Tests:** `pytest`. At minimum, unit-test the rule engine (RuleSet evaluation) and the RuleSet schema
  validation against the mock backend. Tests must pass offline with no LLM and no network.
- **Logging:** structured, human-readable. Every executed action writes to the audit log (§8).

### Suggested project layout
```
adops-mcp/
  README.md
  CLAUDE.md                # this file
  pyproject.toml / requirements.txt
  .env.example
  src/adops_mcp/
    __init__.py
    server.py              # FastMCP app: registers tools, wires backend
    backends/
      base.py             # AdPlatform Protocol/ABC — the seam everything depends on
      mock.py             # MockBackend: deterministic fake campaigns (DEFAULT)
      taboola.py          # TaboolaBackend: real Backstage API
    models.py             # pydantic: Campaign, CampaignStats, Action, RuleSet, RuleCondition...
    rules/
      schema.py           # RuleSet / Condition / Action pydantic models (exposed to the MCP client)
      engine.py           # evaluate(RuleSet, campaigns) -> list[PlannedAction]  (PURE, no I/O, no LLM)
    safety.py             # dry-run gating, confirmation tokens, audit log
    config.py             # env loading, backend selection
  tests/
    test_engine.py
    test_schema.py
    test_mock_backend.py
  examples/
    demo.md               # the 2-minute scripted walkthrough
    sample_rules.txt      # example English rules to paste in
```

---

## 4. Mock Mode (BUILD THIS FIRST — it is the demo)

**Problem:** The author will almost certainly NOT have live Taboola Backstage credentials by the deadline
(they're issued by an account manager). The judge also won't. So the entire product MUST be fully usable
with zero credentials. Mock mode is not a fallback — it is the primary demo surface.

Requirements:
- `BACKEND=mock` is the **default**. The server starts and works with no `.env` at all.
- `MockBackend` implements the exact same `AdPlatform` interface as the real one (§5/§6).
- It holds a deterministic, in-memory (or seeded-JSON) set of ~8–12 fake campaigns with realistic fields:
  id, name, status (RUNNING/PAUSED), cpc/bid, daily_budget, spend, impressions, clicks, conversions,
  cpa, ctr, last_modified. Seed it so the rules have obvious winners and losers to act on
  (e.g. one campaign at $63 spend / 0 conversions = a clear "kill"; one at $14 CPA = a clear "scale").
- Mutations (pause/resume/set-bid/set-budget) update the in-memory state so the judge can see the effect
  on a subsequent read. Re-seedable via a `reset` tool or server restart.
- Make the seed data *legible* in the demo: a buyer should look at it and immediately understand why a
  rule fired.

Acceptance check: `git clone … && uv run … ` (or `pip install -r requirements.txt && python -m adops_mcp.server`)
launches a working MCP server with sample campaigns and zero configuration. If that isn't true, fix it
before anything else.

---

## 5. The MCP tool surface (the product's spine)

Expose these MCP tools. Keep names stable, inputs/outputs pydantic-typed, descriptions written for an LLM
caller (clear, imperative, state side effects). Read-only tools are always safe; mutating tools route
through the safety layer (§8).

**Read-only**
- `list_campaigns(status: optional)` → list of Campaign summaries.
- `get_campaign(campaign_id)` → full Campaign + latest stats.
- `get_performance_report(start_date, end_date, dimension="campaign")` → normalized rows of stats.
  (Mock: synthesize; Taboola: `campaign-summary` report, see §6.)
- `get_audit_log(limit)` → recent executed/planned actions with timestamps and reasons.

**Mutating (must go through safety layer)**
- `pause_campaign(campaign_id, reason)`
- `resume_campaign(campaign_id, reason)`
- `set_campaign_bid(campaign_id, cpc, reason)`
- `set_campaign_budget(campaign_id, daily_budget, reason)`

**Rule layer (the structured "scale & kill" tools)**
- `preview_rule(ruleset: RuleSet)` → take a **structured RuleSet** (Claude builds this from the user's
  English — the server does NOT parse text), evaluate it against current campaigns, and return the list of
  **PlannedActions** WITHOUT executing, plus a `confirm_token`. This is the headline tool: it shows which
  campaigns matched, the human-readable reason each matched, and exactly what *would* happen. Always safe.
- `apply_rule(ruleset: RuleSet, confirm_token: str)` → execute the planned actions. Requires the token that
  the matching `preview_rule` returned, so nothing fires without an explicit two-step confirm. Writes audit
  entries.

Why a RuleSet and not raw text: the MCP client (Claude) is already a language model — it translates the
user's sentence into the typed RuleSet schema (§7) and calls these tools with it. Keeping the server's
input typed makes evaluation **deterministic, reproducible, and unit-testable**, and gives us a concrete
plan object to hash for the confirm token. Expose the RuleSet schema clearly in the tool signature so the
client knows exactly what shape to emit.

Design note: `preview_rule` returns a confirm_token that `apply_rule` consumes. The token is a hash of the
ordered planned-action set, so if campaigns changed between preview and apply, the old token is stale and
`apply_rule` refuses.

Every tool description should make the safety model obvious to the calling LLM, e.g. pause's description
says "Pauses a live campaign immediately; spend stops. Use preview_rule first for bulk changes."

---

## 6. Real backend: Taboola Backstage API (verified facts)

Implement `TaboolaBackend` against these **verified** specifics. If anything 404s or shapes differ, READ
the live docs index for AI agents at `https://developers.taboola.com/llms.txt` (it indexes all pages as
Markdown + endpoints as OpenAPI) and the reference under `https://developers.taboola.com/backstage-api/reference/`.

- **Base URL:** `https://backstage.taboola.com/backstage/api/1.0`
- **Auth:** OAuth2 **client-credentials**. POST `client_id` + `client_secret` to the token endpoint
  (`https://backstage.taboola.com/backstage/oauth/token`) to get a Bearer access token; then send
  `Authorization: Bearer <token>` on every call. Cache the token; on `401`, re-auth and retry once.
- **Resolve account:** the account id is needed in most paths. Fetch token details / allowed accounts to
  discover it (`/users/current/allowed-accounts` style — confirm exact path from llms.txt) rather than
  hardcoding.
- **List campaigns:** `GET /{account_id}/campaigns/` → `{ "results": [ … ] }`. Note: known quirk — the
  base campaigns list may not include a `spent` field; pull spend/performance from the reports endpoint.
- **Performance report:** `GET /{account_id}/reports/campaign-summary/dimensions/day`
  (and/or the `campaign` dimension) with date-range params → `{ "results": [ … ] }`. Use this for spend,
  clicks, conversions, CPA, CTR. Normalize into the same `CampaignStats` model the mock uses.
- **Mutations:** campaign update endpoints under `/{account_id}/campaigns/{campaign_id}/` (POST/PUT per
  docs) for status (pause/resume via `is_active`/status field), `cpc`, and budget fields. Confirm exact
  field names from the reference before writing — do not invent field names.
- **Errors:** `401` → refresh token & retry once. `429` → respect `Retry-After`, exponential backoff.
  `4xx` → surface a clean error to the MCP caller (don't crash the server).
- **Pagination:** list/report endpoints use `results` arrays with `limit`/`offset` (or report date params).
  Implement simple paging.

**Crucial:** `TaboolaBackend` and `MockBackend` implement the **same `AdPlatform` interface** (`base.py`).
Nothing above the backend layer knows which one is live. Selecting backend = one env var (`BACKEND=mock|taboola`).

---

## 7. The RuleSet layer — Claude is the natural-language interface

Goal: a media buyer types plain English into their MCP client (Claude):
- "pause anything spending over $50 with no conversions in the last 48 hours"
- "double the budget on any campaign with CPA under $20"
- "drop bids 15% on campaigns with CTR below 0.5%"

…and gets back a **structured, reviewable plan**, not a black box — and nothing executes without confirming.

### Who does what (this is the whole design — internalize it)
- **The human** writes the English.
- **Claude (the MCP client)** translates that English into a typed `RuleSet` JSON object and calls
  `preview_rule(ruleset=…)`. This is the "natural-language layer." We do **not** build it — it is provided
  for free by the fact that an LLM is driving the server. There is **no parser module** and **no second
  LLM call inside the server**.
- **This server** does the deterministic part: validate the RuleSet (pydantic), evaluate it against current
  campaign stats, build the plan with human-readable reasons, hash it into a confirm token, and on
  `apply_rule` execute + audit.

The model picks the **parameters** (thresholds, which action); our code makes the **decisions** (which
campaigns match, what happens). That separation is what makes this explainable, reproducible, and testable
— and it's the safety story we sell to engineer-judges.

### What to build
1. **`rules/schema.py` — the RuleSet schema (pydantic v2).** A `RuleSet` is a list of rules; each rule =
   `{conditions: [Condition, …], action: Action}`. A `Condition` is a typed comparison on a known metric
   field (`spend`, `conversions`, `cpa`, `ctr`, `daily_budget`, `cpc`, with a `lookback_hours` where
   relevant) using a typed operator (`>`, `>=`, `<`, `<=`, `==`). An `Action` is one of the mutating
   operations (`pause`, `resume`, `set_bid`, `set_budget`) with typed params, **including relative ops**
   (e.g. `multiply_budget: 2.0`, `reduce_bid_pct: 15`). Make this schema clean and well-documented — it is
   literally the contract the MCP client reads to know what JSON to emit, so its field names and docstrings
   are part of the product surface.
2. **`rules/engine.py` — `evaluate(ruleset, campaigns) -> list[PlannedAction]`.** A **pure function**: no
   network, no LLM, no I/O. Each `PlannedAction` carries the target campaign, the resolved concrete action
   (relative ops resolved to absolute values, e.g. "budget $40 → $80"), and a human `reason` string like
   `"spend $63 > $50 and conversions 0 over 48h"`. This is the most-tested file in the repo.
3. Wire both into `preview_rule` / `apply_rule` (§5) with the confirm-token gate (§8).

### Robustness
- Validate the incoming RuleSet strictly; on invalid input return a clear error describing the expected
  schema so the client can correct itself and retry. The client can only ever produce bounded, valid
  actions — it cannot emit arbitrary API calls or code.
- **No `ANTHROPIC_API_KEY` is needed by the server**, ever. The whole rule flow runs offline against the
  mock backend, which is also why the engine and schema are trivially unit-testable.

### Optional convenience (only if time permits — NOT required)
If you want the server to *also* be usable from a plain CLI without an LLM client, you may add a tiny,
clearly-scoped keyword matcher that turns the three documented example sentences into RuleSets. Mark it
explicitly as a demo convenience, keep it out of the core path, and do not let the main product depend on
it. Skip this entirely if it costs time — the MCP-client-driven path is the real product.

---

## 8. Safety system (a feature, not an afterthought — demo it explicitly)

- **Dry-run by default.** `preview_rule` and any "what would happen" path never mutate.
- **Two-step confirm.** `apply_rule` requires the `confirm_token` returned by the matching `preview_rule`.
  Token = hash over the ordered planned-action set; if campaigns changed since preview, token is stale →
  refuse and tell the user to re-preview.
- **Per-action guard rails.** Refuse implausible actions (e.g. budget multiplier > 10×, negative bids).
  Make the caps constants in one place.
- **Audit log.** Append every planned and every executed action to a local store (SQLite or JSONL) with
  timestamp, rule text, matched reason, before/after values, and mock-vs-live flag. Expose via
  `get_audit_log`. In the demo, showing the audit trail after applying a rule is a strong moment.
- **Mock/live banner.** Every response from a mutating tool states whether it ran against MOCK or LIVE.

---

## 9. Deliverables & the README (judges read this)

The README must answer their three required questions, clearly and near the top:
1. **What does this tool do?** — one tight paragraph + the one-line pitch.
2. **Why did you build THIS one?** — connect to their own stated need ("MCP connectors to ad platforms")
   and the real pain it removes (managing campaigns across platforms is repetitive, error-prone, and
   currently done by hand in clunky dashboards). Note the MCP architecture is *why* it generalizes.
3. **What would you build next if this were your full-time job?** — the OUT-OF-SCOPE list as a roadmap:
   add Meta/TikTok/Google behind the same `AdPlatform` seam; scheduled rule runs; anomaly detection;
   creative-fatigue signals; a permissioned multi-user action approval flow.

Also include:
- **Quickstart** that works with zero credentials (mock mode) in <2 min — copy-pasteable.
- **How to connect to Claude Desktop / Claude Code** (the MCP client config snippet pointing at the server).
- **Architecture diagram** (even ASCII): MCP client → MCP server → AdPlatform interface → {Mock | Taboola}.
- **A "switch to live Taboola" section** documenting the env vars and where credentials come from.
- `examples/demo.md`: a literal scripted walkthrough — the exact prompts to type into the MCP client
  ("list my campaigns", "preview: pause anything over $50 with no conversions", "apply it", "show the
  audit log") and what the judge will see. This is your safety net if a live demo wobbles.

Submission checklist (from the contest): working demo (live URL preferred — for an MCP server, a hosted
option is awkward, so a **Loom-style recorded walkthrough + the runnable repo** is the right fallback they
explicitly allow; say so in the README), the GitHub repo (they WILL read the code), and this README.

---

## 10. Definition of done (verify before submitting)

- [ ] `BACKEND=mock` server boots with no `.env` and serves ~10 seeded campaigns.
- [ ] All read tools return clean typed data from the mock.
- [ ] `preview_rule` accepts a structured RuleSet for each scenario in `sample_rules.txt` and returns a
      correct plan with human-readable reasons.
- [ ] `apply_rule` mutates mock state only with a valid confirm_token; stale tokens are refused.
- [ ] Audit log records every action; `get_audit_log` shows it.
- [ ] `TaboolaBackend` implemented against the §6 endpoints behind the same interface (even if untested
      live, it must be real, reviewable code — judges read it).
- [ ] `pytest` passes offline (engine + schema + mock backend), with no LLM and no network.
- [ ] README answers the 3 questions, has the <2-min quickstart and the Claude client config snippet.
- [ ] `examples/demo.md` walkthrough is accurate end-to-end.

## 11. Things NOT to do (common failure modes)
- Don't parse English inside the server or add a second LLM call. Claude (the client) emits a typed
  RuleSet; the server only validates and deterministically evaluates it.
- Don't let the RuleSet express anything beyond the bounded schema — no raw API calls, no arbitrary code.
- Don't make the demo depend on live Taboola creds or any `ANTHROPIC_API_KEY` in the server.
- Don't skip the safety layer to save time — it's a differentiator, not overhead.
- Don't sprawl into multi-platform or a web UI. Depth on Taboola + MCP wins.
- Don't invent Taboola field/endpoint names from memory — verify against llms.txt / the reference.
- Don't over-format the README into a wall of bullets; write like an engineer explaining a real tool.