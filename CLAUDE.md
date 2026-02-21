# CLAUDE.md — sysls

## What is sysls?

sysls is an event-driven, asynchronous, systematic multi-asset trading automation framework. It runs as a long-running process inside a Docker container and supports equities, crypto (spot + derivatives), and event markets through a unified abstraction layer.

**Supported venues:** tastytrade, Interactive Brokers (via ib_insync), crypto CEXs/DEXs (via ccxt), Polymarket.
**Supported data providers:** Polygon (Massive), DataBento, local/custom (via ArcticDB).

The framework enables strategies like: cross-venue arb (e.g. NVDA equity vs 10x NVDA-USDT perp on HyperLiquid), equity L/S on ATM implied vol term structure, event market trading with SSVI-parametrized vol surfaces, Avellaneda-Stoikov market making on Polymarket, and anything else a quant can express through the strategy SDK.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLI / API                               │
├─────────────────────────────────────────────────────────────────┤
│                    Analytics Layer (gs-quant inspired)           │
│         risk reports · PnL attribution · factor decomposition   │
├─────────────────────────────────────────────────────────────────┤
│              Strategy & Risk Framework                          │
│    abstract base class · signals · portfolio construction       │
│    position limits · Greeks · exposure monitoring               │
├──────────────────────┬──────────────────────────────────────────┤
│   Execution Layer    │         Backtesting Layer                │
│   OMS · SOR · fills  │   vectorized (vectorbt) · event replay  │
│   reconciliation     │   walk-forward · optimization           │
├──────────────────────┴──────────────────────────────────────────┤
│                    Market Data Layer                             │
│   normalized quotes · orderbook · bars · trades                 │
│   streaming + historical · data connectors                      │
├─────────────────────────────────────────────────────────────────┤
│                    Core Event System                             │
│   async event bus · message types · pub/sub · priority queues   │
├─────────────────────────────────────────────────────────────────┤
│                    Infrastructure                                │
│   config · secrets · logging · telemetry · Docker               │
└─────────────────────────────────────────────────────────────────┘
```

### Layer Responsibilities

**Core Event System** — The backbone. All communication between components flows through a typed, async event bus. Events are the single source of truth: MarketDataEvent, OrderEvent, FillEvent, SignalEvent, RiskEvent, etc. Built on Python asyncio with priority-aware dispatch.

**Market Data Layer** — Unified interface for all data sources. A `DataConnector` ABC with implementations for Polygon/Massive, DataBento, and ArcticDB (local). All connectors emit normalized `MarketDataEvent`s regardless of source. Handles both streaming (live) and historical (backtest) modes transparently.

**Execution Layer** — Order Management System (OMS) tracks order lifecycle. Venue adapters translate normalized orders into venue-specific API calls. Smart Order Router (SOR) selects optimal venue for cross-listed instruments. Paper trading mode uses simulated fills. Reconciliation loop verifies positions against venue state.

**Backtesting Layer** — Two modes: (1) vectorized, numpy/pandas-based fast backtesting for signal research (heavily inspired by vectorbt's Portfolio and indicator APIs), and (2) event-driven replay that feeds historical data through the full live stack for realistic simulation. Supports walk-forward analysis and parameter optimization.

**Strategy & Risk Framework** — Users extend `Strategy` ABC to implement `on_market_data()`, `generate_signals()`, and `construct_portfolio()`. Risk engine enforces limits (position size, notional, sector exposure, Greeks, drawdown). Portfolio construction translates target weights into order instructions.

**Analytics Layer** — Inspired by gs-quant. Risk analytics (VaR, stress testing, scenario analysis), PnL attribution (factor-based, instrument-level), performance metrics. Available programmatically and through CLI reports.

**CLI** — Primary user interface. Commands for: deploying strategies, running backtests, monitoring live positions, generating reports, managing configuration.

---

## Tech Stack & Key Dependencies

- **Language:** Python 3.12+
- **Async runtime:** asyncio (uvloop optional for Linux)
- **Event bus:** custom, built on asyncio.Queue with typed events
- **Data storage:** ArcticDB (time-series), SQLite (state/config)
- **Serialization:** msgpack for wire, Pydantic v2 for models
- **Numerics:** numpy, pandas, polars (where perf-critical)
- **Backtesting core:** numpy vectorized ops (vectorbt-inspired), numba for hot paths
- **Venue SDKs:** ccxt (crypto), ib_insync (IBKR), tastytrade SDK, py-clob-client (Polymarket)
- **Data providers:** polygon-api-client, databento-python
- **CLI:** click or typer
- **Packaging:** uv for dependency management, hatch for builds
- **Testing:** pytest + pytest-asyncio, hypothesis for property-based tests
- **CI:** GitHub Actions
- **Container:** Docker + docker-compose

---

## Project Structure

```
sysls/
├── CLAUDE.md                    # This file
├── pyproject.toml               # Project metadata, dependencies, build config
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── src/
│   └── sysls/
│       ├── __init__.py
│       ├── core/                # Event system, base types, config
│       │   ├── __init__.py
│       │   ├── events.py        # Event types (MarketData, Order, Fill, Signal, Risk)
│       │   ├── bus.py           # Async event bus, pub/sub, priority dispatch
│       │   ├── types.py         # Instrument, Side, OrderType, TimeInForce, etc.
│       │   ├── config.py        # Pydantic settings, YAML/env config loading
│       │   └── clock.py         # Unified clock (live wallclock / backtest simulated)
│       ├── data/                # Market data connectors and normalization
│       │   ├── __init__.py
│       │   ├── connector.py     # DataConnector ABC
│       │   ├── polygon.py       # Polygon / Massive connector
│       │   ├── databento.py     # DataBento connector
│       │   ├── arctic.py        # ArcticDB local connector
│       │   ├── normalize.py     # Schema normalization across sources
│       │   └── store.py         # Time-series storage interface (ArcticDB)
│       ├── execution/           # Order management and venue adapters
│       │   ├── __init__.py
│       │   ├── oms.py           # Order Management System
│       │   ├── router.py        # Smart Order Router
│       │   ├── paper.py         # Paper trading / simulated fills
│       │   ├── reconcile.py     # Position reconciliation
│       │   └── venues/
│       │       ├── __init__.py
│       │       ├── base.py      # VenueAdapter ABC
│       │       ├── ccxt_venue.py    # ccxt-based crypto adapter
│       │       ├── ibkr.py      # Interactive Brokers adapter
│       │       ├── tastytrade.py    # tastytrade adapter
│       │       └── polymarket.py    # Polymarket adapter
│       ├── strategy/            # Strategy and risk framework
│       │   ├── __init__.py
│       │   ├── base.py          # Strategy ABC
│       │   ├── signal.py        # Signal types and combinators
│       │   ├── portfolio.py     # Portfolio construction (weights → orders)
│       │   └── risk.py          # Risk engine (limits, Greeks, exposure)
│       ├── backtest/            # Backtesting engine
│       │   ├── __init__.py
│       │   ├── vectorized.py    # vectorbt-inspired fast vectorized backtester
│       │   ├── replay.py        # Event-driven historical replay
│       │   ├── metrics.py       # Sharpe, drawdown, Calmar, etc.
│       │   └── optimize.py      # Walk-forward, grid search, optimization
│       ├── analytics/           # gs-quant inspired analytics
│       │   ├── __init__.py
│       │   ├── risk_analytics.py    # VaR, stress tests, scenario analysis
│       │   ├── pnl.py           # PnL attribution
│       │   ├── factors.py       # Factor decomposition
│       │   └── reports.py       # Report generation
│       └── cli/                 # Command-line interface
│           ├── __init__.py
│           └── main.py          # CLI entry point
├── tests/
│   ├── conftest.py
│   ├── core/
│   ├── data/
│   ├── execution/
│   ├── strategy/
│   ├── backtest/
│   └── analytics/
├── examples/
│   ├── cross_venue_arb.py       # NVDA equity vs perp arb
│   ├── equity_ls_vol.py         # Equity L/S on implied vol
│   ├── polymarket_btc.py        # BTC up/down on Polymarket
│   └── avellaneda_stoikov.py    # MM on Polymarket
└── docs/
    ├── getting-started.md
    ├── architecture.md
    ├── strategy-guide.md
    └── deployment.md
```

---

## Coding Conventions

- **Type hints everywhere.** All function signatures fully typed. Use `from __future__ import annotations`.
- **Pydantic v2 models** for all data structures that cross boundaries (events, config, API responses). Use `model_validator` over `__post_init__` hacks.
- **ABCs for extension points.** `DataConnector`, `VenueAdapter`, `Strategy` — all abstract. Users never subclass concrete classes.
- **Events are immutable dataclasses** (frozen=True) or Pydantic models. Never mutate an event after creation.
- **Async by default.** All I/O-bound code is async. Use `asyncio.TaskGroup` for structured concurrency (Python 3.11+). Never use raw threads for I/O.
- **No global state.** All components receive dependencies via constructor injection. The `Engine` wires everything together at startup.
- **Logging via structlog.** Structured, JSON-serializable log output. No `print()` statements.
- **Error handling:** Custom exception hierarchy rooted in `SyslsError`. Venue errors wrapped in `VenueError`. Never catch bare `Exception` in business logic.
- **Tests:** Each module has a corresponding test module. Use `pytest-asyncio` for async tests. Use `hypothesis` for property-based tests on serialization round-trips, order state machines, etc. Target >80% coverage on core/.
- **Naming:** snake_case for everything Python. Classes are PascalCase. Constants are UPPER_SNAKE. No abbreviations except universally understood ones (PnL, OMS, SOR, VaR).
- **Imports:** Absolute imports only (`from sysls.core.events import MarketDataEvent`). No relative imports.
- **Docstrings:** Google style. Required on all public classes and functions.

---

## Event Type Hierarchy

```python
Event (base)
├── MarketDataEvent
│   ├── QuoteEvent          # bid/ask update
│   ├── TradeEvent          # individual trade/tick
│   ├── BarEvent            # OHLCV bar
│   └── OrderBookEvent      # L2/L3 snapshot or delta
├── OrderEvent
│   ├── OrderSubmitted
│   ├── OrderAccepted
│   ├── OrderRejected
│   ├── OrderCancelled
│   └── OrderAmended
├── FillEvent               # partial or full fill
├── PositionEvent           # position change notification
├── SignalEvent             # strategy signal emission
├── RiskEvent               # risk limit breach or warning
├── SystemEvent
│   ├── HeartbeatEvent
│   ├── ConnectionEvent     # venue connect/disconnect
│   └── ErrorEvent
└── TimerEvent              # scheduled callbacks
```

---

## Key Design Decisions

1. **Event bus, not function calls.** Components communicate exclusively through the event bus. This enables: backtesting by replaying events, easy logging/audit, loose coupling, and the ability to add new consumers without modifying producers.

2. **Unified clock abstraction.** A `Clock` provides `now()` and timer scheduling. In live mode it's wallclock time. In backtest mode it's simulated time advancing with the data. This lets strategies be written once and run in both contexts without modification.

3. **Vectorized backtesting is separate from event replay.** Vectorized mode (vectorbt-style) operates on numpy arrays for speed — suitable for signal research and parameter sweeps. Event replay mode feeds historical data through the real OMS/risk stack — suitable for realistic fill simulation and integration testing. Both share metrics computation.

4. **Venue adapters are thin.** They translate between sysls's normalized types and venue APIs. Business logic (retry, rate limiting, batching) lives in the OMS, not in adapters.

5. **Risk engine is synchronous on the hot path.** Pre-trade risk checks must not add latency. The risk engine maintains in-memory state and evaluates limits synchronously. Async risk (e.g. periodic portfolio VaR) runs on separate timers.

6. **ArcticDB as the canonical local store.** All historical data, backtest results, and analytics outputs are stored in ArcticDB. It handles versioned, columnar time-series storage efficiently and is the backbone of the local data experience.

7. **Configuration is layered.** Defaults → YAML config file → environment variables → CLI flags. Pydantic Settings handles this natively.

---

## Development Phases

### Phase 0 — Foundation (Weeks 1–3)
Core event system, base types, config, logging, project scaffolding, CI/CD.
Deliverable: Event bus with pub/sub, typed events, async dispatch. Full CI pipeline.

### Phase 1 — Data Layer (Weeks 4–6)
DataConnector ABC + first implementation (Polygon or DataBento). ArcticDB storage adapter. Normalized market data schema. Streaming infrastructure.
Deliverable: Can ingest and store live + historical market data from one source.

### Phase 2 — Execution: Single Venue (Weeks 7–10)
OMS core, first venue adapter (ccxt/Binance — most accessible API). Position tracking, fills, reconciliation. Paper trading mode.
Deliverable: Can submit, track, and reconcile orders on one live venue + paper mode.

### Phase 3 — Strategy Framework (Weeks 11–13)
Strategy ABC, signal framework, basic risk limits, portfolio construction.
Deliverable: A user can write a strategy subclass that receives data, emits signals, and generates orders.

### Phase 4 — Backtesting (Weeks 14–17)
Vectorized backtester (vectorbt-inspired). Event-driven replay. Metrics (Sharpe, drawdown, Calmar). Walk-forward optimization.
Deliverable: Can backtest strategies fast (vectorized) and realistically (event replay).

### Phase 5 — Multi-Venue & Cross-Asset (Weeks 18–22)
Additional venue adapters (tastytrade, IBKR, Polymarket). Cross-venue SOR. Multi-asset position aggregation. Cross-venue risk.
Deliverable: Can run strategies spanning multiple venues and asset classes simultaneously.

### Phase 6 — Analytics & CLI (Weeks 23–26)
gs-quant inspired analytics. Risk reports, PnL attribution, factor decomposition. CLI for deployment, monitoring, backtesting. Real-time dashboards (optional).
Deliverable: Full analytics suite + production-ready CLI.

### Phase 7 — Production Hardening (Weeks 27–30)
Docker deployment with health checks. Reconnection logic, failover, circuit breakers. Audit logging. Documentation, examples, contributor guide.
Deliverable: Production-ready, documented, open-source release.

---

## Agent Hierarchy for Development

When using Claude Code or multi-agent workflows to develop sysls, the following hierarchy applies:

```
Architect Agent (owns CLAUDE.md, cross-cutting decisions, API surface design)
├── Core Agent (core/, event system, types, config, clock)
│   └── Owns: events.py, bus.py, types.py, config.py, clock.py
├── Data Agent (data/, connectors, normalization, storage)
│   └── Owns: connector.py, polygon.py, databento.py, arctic.py, normalize.py, store.py
├── Execution Agent (execution/, OMS, venues, routing)
│   └── Owns: oms.py, router.py, paper.py, reconcile.py, venues/*
├── Strategy Agent (strategy/, risk, portfolio construction)
│   └── Owns: base.py, signal.py, portfolio.py, risk.py
├── Backtest Agent (backtest/, vectorized, replay, optimization)
│   └── Owns: vectorized.py, replay.py, metrics.py, optimize.py
├── Analytics Agent (analytics/, reports, factors, PnL)
│   └── Owns: risk_analytics.py, pnl.py, factors.py, reports.py
└── DevOps Agent (docker/, CI, docs, packaging)
    └── Owns: Dockerfile, docker-compose.yml, .github/, docs/
```

**Coordination rules:**
- Agents only modify files they own. Cross-cutting changes go through Architect.
- The event type hierarchy in `core/events.py` is the contract between agents. Changes require Architect approval.
- Each agent writes tests for its own modules. Integration tests spanning multiple layers are owned by Architect.
- The Architect agent is responsible for ensuring API consistency across layers and resolving conflicts.

---

## Agent Operational Rules

These rules govern how agents behave during development. Every agent and subagent MUST follow these rules. The human owner operates as a **project manager** — they set direction, unblock decisions, and review milestones, but they do not want to be involved in the day-to-day execution. Agents should be autonomous, self-correcting, and communicate through Slack like a development team reporting to their PM.

### 1. Autonomy & Web Search

All agents and subagents are **pre-authorized to perform web searches** without requesting human permission. This includes searching documentation for third-party libraries, looking up API references, error codes, known issues, researching best practices, and checking library version compatibility.

**Do not ask the human for permission to search.** Just search. The human does not want to be a bottleneck for information retrieval.

### 2. Slack Communication (MCP — Bidirectional)

Communication with the human PM and between agents happens through Slack via the Slack MCP server. This is **not a one-way notification pipe** — it's a conversation channel. Agents post updates, the PM replies with feedback or course corrections, and agents read and act on those replies.

**MCP Server Configuration:** The project uses `slack-mcp-server` (korotovsky) configured via `.mcp.json` in the project root. Copy `.mcp.json.example` to `.mcp.json` and add your Slack token. See `docs/slack-setup.md` for full setup instructions. The MCP server provides these tools:

| MCP Tool | Purpose |
|---|---|
| `mcp__slack__conversations_add_message` | Post a message to a channel or reply in a thread |
| `mcp__slack__conversations_history` | Read recent messages from a channel |
| `mcp__slack__conversations_replies` | Read all replies in a specific thread |
| `mcp__slack__conversations_search_messages` | Search for messages across channels |
| `mcp__slack__channels_list` | List available channels |
| `mcp__slack__reactions_add` | React to a message (e.g. :white_check_mark: to acknowledge) |

**Slack Channel Structure:**

```
#sysls-announcements    — Milestones, phase completions, releases. PM monitors this.
#sysls-dev              — Day-to-day progress, commits, technical discussion between agents.
#sysls-review           — Code review requests and review feedback.
#sysls-blocked          — Escalations that need human input. PM has notifications ON for this.
#sysls-architecture     — Cross-cutting design decisions. Architect agent owns this channel.
```

**How agents communicate — by scenario:**

**Progress update (agent → PM):**
Post to `#sysls-dev`. Keep it concise. Include what was completed, what's next.
```
mcp__slack__conversations_add_message(
  channel_id="#sysls-dev",
  payload="*Core Agent* — Event bus implementation complete. 4 commits, all tests green. Starting config/clock module next."
)
```

**Milestone reached (agent → PM):**
Post to `#sysls-announcements`. This is the "hey boss, a meaningful chunk is done" channel.
```
mcp__slack__conversations_add_message(
  channel_id="#sysls-announcements",
  payload=":white_check_mark: *Phase 0 Complete*\nEvent bus, typed events, config loader, unified clock, CI pipeline — all implemented and tested. 94% coverage on core/. Ready to begin Phase 1 (Data Layer)."
)
```

**Code review request (sub-agent → parent agent):**
Post to `#sysls-review`. Tag the reviewing agent. Include what to review and where.
```
mcp__slack__conversations_add_message(
  channel_id="#sysls-review",
  payload=":mag: *Review Request*\nModule: `src/sysls/data/polygon.py` + tests\nAuthor: Data Agent\nReviewer: Architect Agent\nBranch: `phase-1/polygon-connector`\n847 lines, 92% test coverage. Implements historical bars, streaming quotes, and normalization."
)
```

**Code review feedback (parent agent → sub-agent):**
Reply **in the thread** of the original review request. This keeps the conversation together.
```
mcp__slack__conversations_add_message(
  channel_id="#sysls-review",
  thread_ts="<timestamp of the review request message>",
  payload="REVIEW: CHANGES REQUESTED\n\n- [MUST FIX] `normalize_bar()` silently drops bars with zero volume — should emit a warning via structlog.\n- [SHOULD FIX] Missing type hint on `_parse_timestamp` return.\n- [GOOD] Clean separation between HTTP client and normalization logic."
)
```

**Blocked / needs human input (agent → PM):**
Post to `#sysls-blocked`. This channel is reserved for genuine blockers. The PM has notifications ON here.
```
mcp__slack__conversations_add_message(
  channel_id="#sysls-blocked",
  payload=":sos: *Data Agent — BLOCKED*\nPolygon API returning 403 on historical bars endpoint.\n\n*Tried:*\n1. Rotated API key — same error\n2. Tested with curl directly — same 403\n3. Checked Polygon status page — no incidents\n4. Searched GitHub issues — found similar report from 2 weeks ago, unresolved\n\n*Suspect:* Account tier doesn't include equities historical. Need human to verify Polygon subscription level.\n\n*Impact:* Cannot proceed with Phase 1 data connector until resolved. Can work on DataBento connector in parallel."
)
```

**Checking for PM replies before proceeding on a blocked item:**
After posting to `#sysls-blocked`, periodically check for thread replies:
```
# Get recent messages from #sysls-blocked to find your thread
mcp__slack__conversations_history(channel_id="#sysls-blocked", limit=10)
# Then check for replies on your specific thread
mcp__slack__conversations_replies(channel_id="#sysls-blocked", thread_ts="<your message timestamp>")
```

**Architecture discussion (any agent → Architect → PM if needed):**
Post design questions to `#sysls-architecture`. The Architect agent monitors this and responds. If the Architect can't resolve it, they escalate to the PM by cross-posting to `#sysls-blocked`.
```
mcp__slack__conversations_add_message(
  channel_id="#sysls-architecture",
  payload="*Execution Agent* — Design question: should the OMS emit a `PositionEvent` on every fill, or should we batch position updates and emit on a timer? Tradeoff: per-fill is simpler and always consistent, but could flood the bus during high-frequency strategies. Timer-based is more efficient but introduces staleness."
)
```

**Searching for past decisions:**
Before making a design choice, search Slack for prior discussion:
```
mcp__slack__conversations_search_messages(
  search_query="position event batching",
  filter_in_channel="#sysls-architecture"
)
```

**Acknowledging a message:**
React instead of posting a reply when a simple acknowledgment suffices:
```
mcp__slack__reactions_add(channel_id="#sysls-dev", timestamp="<msg_ts>", emoji="eyes")       # "I've seen this"
mcp__slack__reactions_add(channel_id="#sysls-review", timestamp="<msg_ts>", emoji="white_check_mark")  # "Approved"
mcp__slack__reactions_add(channel_id="#sysls-dev", timestamp="<msg_ts>", emoji="rocket")     # "Shipped"
```

**Communication frequency guidelines:**
- `#sysls-announcements`: ~1-3 posts per phase. Milestones only.
- `#sysls-dev`: Several posts per day during active development. Natural breakpoints — when starting a sub-task, when something meaningful works, when a batch of commits lands.
- `#sysls-review`: One post per reviewable unit of work. Thread replies for feedback.
- `#sysls-blocked`: Rarely. Only after the troubleshooting protocol in section 3 is exhausted.
- `#sysls-architecture`: As needed for design discussions. Search before posting to avoid re-litigating settled decisions.

**Reading human responses:**
Agents MUST check for replies on their threads in `#sysls-blocked` before continuing work on a blocked item. For other channels, agents should periodically read recent history (e.g. at the start of each work session) to pick up any direction changes, feedback, or context the PM has posted.
```
# Start-of-session: read recent messages from key channels
mcp__slack__conversations_history(channel_id="#sysls-announcements", limit=5)
mcp__slack__conversations_history(channel_id="#sysls-blocked", limit=10)
mcp__slack__conversations_history(channel_id="#sysls-architecture", limit=10)
```

### 3. Troubleshooting Before Escalation

Before posting to `#sysls-blocked`, an agent MUST have attempted ALL of the following:

1. **Read the error carefully.** Parse the full traceback, not just the last line.
2. **Search the web.** Look up the error message, check GitHub issues, Stack Overflow, library docs.
3. **Check the project context.** Re-read CLAUDE.md, check if another agent's code introduced the issue, verify assumptions about the event contract.
4. **Try at least 3 different approaches** to fix the problem.
5. **Isolate the problem.** Write a minimal reproduction. Determine if it's a bug in our code, a library issue, an environment issue, or a misunderstanding.
6. **Document what was tried.** The `#sysls-blocked` post MUST include what was attempted so the PM doesn't waste time re-treading the same ground.
7. **Search Slack for prior discussion.** Maybe this was already solved or discussed.

If an agent gets stuck on something that is clearly outside its domain (e.g., the Data Agent discovers the event bus has a bug), it should first raise it in `#sysls-dev` tagging the responsible agent (Core Agent in this case) before escalating to `#sysls-blocked`.

**Escalation chain:** Sub-agent → Parent agent (via `#sysls-dev` thread) → Architect (via `#sysls-architecture`) → Human PM (via `#sysls-blocked`).

### 4. Git Commit Discipline

Code MUST be committed in meaningful, atomic units **as soon as it is written and passing tests.** Do NOT accumulate uncommitted work.

**Commit rules:**
- **Commit early, commit often.** A commit should represent one logical change: a new function, a completed test suite, a bug fix, a refactor. Not an entire feature at once.
- **Every commit must pass tests.** Run `uv run pytest` (at minimum for the affected module) before committing. Never commit broken code to main.
- **Commit message format:**
  ```
  <layer>: <concise description>

  <optional body explaining why, not what>

  Refs: #<issue> (if applicable)
  ```
  Examples:
  ```
  core: implement async event bus with priority dispatch
  data: add Polygon connector for historical bars
  execution: fix race condition in OMS fill processing

  Previously, concurrent fills for the same order could cause
  double-counting in position tracking. Added a lock on the
  order state machine transitions.
  backtest: add vectorized Sharpe/drawdown metrics
  ```
- **Layer prefixes:** `core`, `data`, `execution`, `strategy`, `backtest`, `analytics`, `cli`, `infra`, `docs`, `test`
- **Branch strategy:** Feature branches named `phase-N/description` (e.g., `phase-0/event-bus`, `phase-2/ccxt-adapter`). Merge to `main` when the feature is complete and reviewed.
- **Never rewrite history on main.** Rebase feature branches before merge, but never force-push to main.
- **Typical commit size:** 50–300 lines changed. If a commit is >500 lines, it should almost certainly be split. If a commit is <10 lines, consider whether it can be batched with the next related change.
- **Post to `#sysls-dev`** after landing a batch of related commits (not every single one).

### 5. Hierarchical Code Review

Every significant piece of code MUST be self-reviewed before merging. Since we run a single agent session, we use Claude Code's `Task` tool to spawn a dedicated review subagent with a fresh perspective.

**Review workflow:**
1. Finish implementation + tests for a logical unit of work.
2. Commit to a feature branch (e.g., `feature/phase-0-event-bus`).
3. Push the branch: `git push -u origin <branch>`.
4. **Self-review via Task subagent:** Spawn a `Task` with this prompt:
   > "Review the diff between main and the current branch. Check against: CLAUDE.md coding conventions, layer separation, event contract compliance, test coverage, error handling, performance in hot paths. Output a structured review with MUST FIX / SHOULD FIX / NIT / GOOD findings."
5. Fix any MUST FIX items found by the review subagent. Re-commit.
6. Open a PR: `gh pr create --base main --title "<layer>: <description>" --body "<summary of changes>"`.
7. Post to `#sysls-review` with the PR link and a summary.
8. If the PM replies in thread with change requests, address them. Otherwise, silence = approval.
9. Merge after review: `gh pr merge --squash --delete-branch` (only if CI passes — check with `gh pr checks <number>`).

**What triggers a PR + review:**
- Any new public API (ABC method, event type, config schema)
- Completion of a module (e.g., `oms.py` is fully implemented)
- Changes to shared contracts (`core/events.py`, `core/types.py`)
- Any change >200 lines
- End of each phase (phase completion PR)

**What can be committed directly to main (no PR):**
- Internal refactors within a module that don't change the public API
- Test additions for existing code
- Documentation updates
- Bug fixes <50 lines with obvious correctness

**Review feedback format** (from Task subagent, also posted to `#sysls-review` thread):
```
REVIEW: [APPROVED | CHANGES REQUESTED]
Module: <file or module reviewed>

Findings:
- [MUST FIX] <description> (blocks merge)
- [SHOULD FIX] <description> (fix before phase completion)
- [NIT] <description> (optional improvement)
- [GOOD] <description> (positive callout)
```

### 6. Agent Self-Governance Summary

```
┌──────────────────────────────────────────────────────────┐
│              AGENT DECISION TREE                         │
│                                                          │
│  Need information?                                       │
│  └─→ Web search immediately. Don't ask.                  │
│                                                          │
│  Finished a logical unit of code?                        │
│  └─→ Run tests → Commit → Continue.                      │
│                                                          │
│  Finished a reviewable chunk (public API, module, >200L)?│
│  └─→ Push branch → Task subagent review → Fix MUST FIXes │
│      → gh pr create → Post to #sysls-review → Continue.  │
│                                                          │
│  Small fix / internal refactor / docs / tests?           │
│  └─→ Commit directly to main. No PR needed.              │
│                                                          │
│  Hit a problem?                                          │
│  └─→ Search → Try 3 fixes → Isolate →                   │
│      Post to #sysls-dev with details →                   │
│      ONLY THEN post to #sysls-blocked.                   │
│                                                          │
│  Completed a milestone?                                  │
│  └─→ Post to #sysls-announcements. Don't wait.           │
│                                                          │
│  Unsure about architecture?                              │
│  └─→ Search #sysls-architecture for prior discussion →   │
│      Post question there → Continue with best judgment.   │
│      Escalate to #sysls-blocked only if truly stuck.     │
│                                                          │
│  Starting a new work session?                            │
│  └─→ Read recent history from #sysls-blocked,            │
│      #sysls-architecture, #sysls-announcements.          │
│      Pick up any direction changes from the PM.          │
│                                                          │
│  Human PM is silent?                                     │
│  └─→ That means "continue." Keep working.                │
│                                                          │
│  Human PM posted feedback in a thread?                   │
│  └─→ Read it. Act on it. Reply confirming.               │
└──────────────────────────────────────────────────────────┘
```

---

## Multi-Agent Operation

This project supports running multiple Claude Code instances simultaneously. Each agent runs in its own terminal with a specific role and its own git worktree (so agents never conflict on filesystem).

### Roles

**Architect Agent (1 instance):**
- Runs in the **main worktree** (the original repo clone)
- Monitors `#sysls-review` for incoming PRs from junior agents
- Reviews PRs using `gh pr review` — checks architecture, conventions, contracts
- Posts architectural decisions to `#sysls-architecture`
- Assigns work by posting task descriptions to `#sysls-dev`
- Merges approved PRs: `gh pr merge --squash --delete-branch`
- Does NOT write implementation code (only reviews, refactors if needed after merge)
- Handles escalations from `#sysls-blocked`

**Junior Agent(s) (1-3 instances):**
- Each runs in a **separate git worktree** (e.g., `worktrees/junior-1/`)
- Reads `#sysls-dev` at session start for task assignments from the Architect
- Works ONLY on its assigned module/feature on a feature branch
- Pushes branch, opens PR via `gh pr create`, posts to `#sysls-review`
- Reads review feedback from PR comments and Slack threads, fixes, re-pushes
- **Never merges its own PRs** — only the Architect merges
- Posts progress to `#sysls-dev`, blockers to `#sysls-blocked`

### Coordination Protocol

```
Architect                          Junior Agent(s)
   │                                     │
   │ ─── Post task to #sysls-dev ──────→ │
   │                                     │ read task, create branch
   │                                     │ implement + test
   │                                     │ git push, gh pr create
   │ ←── PR link in #sysls-review ────── │
   │                                     │
   │ review PR (gh pr review)            │
   │ post feedback in PR + Slack thread  │
   │                                     │
   │ ─── APPROVED or CHANGES REQ ──────→ │
   │                                     │ (if changes: fix, re-push)
   │ ←── "Ready for re-review" ──────── │
   │                                     │
   │ gh pr merge --squash                │
   │ post to #sysls-dev: "merged X"      │
   │                                     │ pull main, start next task
```

### Git Worktree Setup

Claude Code has **built-in worktree support**. No manual setup needed.

**CLI (multi-terminal mode):** Each junior launches with `--worktree` which auto-creates an isolated worktree:
```bash
claude --worktree junior-1 -p "..."   # auto-creates worktree named junior-1
claude --worktree junior-2 -p "..."   # separate worktree
```

**Subagent mode (single orchestrator):** The Architect spawns juniors using the `junior` custom agent in `.claude/agents/junior.md`, which has `isolation: worktree` in its frontmatter. Each junior subagent automatically gets its own worktree.

**`.worktreeinclude`** ensures `.env` and `.mcp.json` are copied into every auto-created worktree so juniors have Slack access and credentials.

**Important:** Juniors must `git pull origin main` before starting new tasks to pick up merged changes from other agents.

### Slack Channel Usage (Multi-Agent)

| Channel | Who posts | Who reads |
|---|---|---|
| `#sysls-announcements` | Architect (milestones) | PM |
| `#sysls-dev` | Architect (task assignments), Juniors (progress) | Everyone |
| `#sysls-review` | Juniors (PR submissions) | Architect (reviews in threads) |
| `#sysls-blocked` | Anyone stuck after protocol | PM responds |
| `#sysls-architecture` | Architect (decisions), Juniors (questions) | Everyone |

### Task Assignment Format (Architect → `#sysls-dev`)

```
📋 *Task Assignment*
Agent: Junior-1
Module: src/sysls/core/event_bus.py
Branch: phase-0/event-bus
Priority: P0

*Requirements:*
- Implement async event bus with priority dispatch
- Support subscribe/unsubscribe by event type
- Support wildcard subscriptions
- Must be fully async (asyncio)
- Emit metrics on queue depth and dispatch latency

*Acceptance criteria:*
- All tests in tests/core/test_event_bus.py pass
- >90% coverage on the module
- Passes ruff check + format
- Google-style docstrings on all public methods

*Dependencies:* None (first module)
*Refs:* See CLAUDE.md Architecture → Core Event System
```

### Junior Agent PR Submission Format (Junior → `#sysls-review`)

```
🔍 *Pull Request*
PR: <link from gh pr create>
Agent: Junior-1
Module: src/sysls/core/event_bus.py
Branch: phase-0/event-bus → main
Lines: +280 / -0

*Summary:*
Implemented async event bus with priority dispatch, wildcard subs, metrics.
12 tests, all passing. ruff clean. Full docstrings.

*Ready for review @Architect*
```

### Architect Review Format (Architect → Slack thread + `gh pr review`)

The Architect reviews by:
1. Reading the PR diff: `gh pr diff <number>`
2. Checking test results: `gh pr checks <number>`
3. Posting review: `gh pr review <number> --approve` or `gh pr review <number> --request-changes --body "..."`
4. Replying in the Slack thread with the structured review feedback

---

## Important Patterns & Anti-Patterns

### Do
- Use the event bus for all inter-component communication
- Write strategies that are clock-agnostic (work in live and backtest)
- Keep venue adapters stateless and thin
- Use Pydantic for validation at boundaries, plain dataclasses internally for speed
- Test with both unit tests and integration tests that spin up the event bus
- Use `decimal.Decimal` for prices and quantities in order/fill types (not float)

### Don't
- Don't use threads for I/O (use async)
- Don't put business logic in venue adapters
- Don't mutate events after creation
- Don't use global singletons or module-level mutable state
- Don't import from `execution` in `data` or vice versa (maintain layer separation)
- Don't use `float` for financial calculations in the execution path
- Don't block the event loop — offload CPU-heavy work to `asyncio.to_thread()` or a ProcessPoolExecutor

---

## Running the Project

```bash
# Setup
uv sync
cp .env.example .env          # Fill in API keys and venue credentials
cp .mcp.json.example .mcp.json  # Fill in your Slack token (see docs/slack-setup.md)

# Verify Slack MCP connectivity (from Claude Code session)
# mcp__slack__channels_list()

# Run tests
uv run pytest

# Lint and format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Run a backtest
uv run sysls backtest --strategy examples/cross_venue_arb.py --start 2024-01-01 --end 2024-12-31

# Deploy live (paper mode)
uv run sysls run --strategy examples/cross_venue_arb.py --paper

# Deploy live (real)
uv run sysls run --strategy examples/cross_venue_arb.py --config config.yaml

# Docker
docker compose -f docker/docker-compose.yml up
```

## CI/CD

GitHub Actions runs on every push to `main` and every PR:
- `ruff check` — lint
- `ruff format --check` — formatting
- `mypy` — type checking (advisory, non-blocking in early phases)
- `pytest` — full test suite

Branch protection on `main` requires CI to pass before merge. See `.github/workflows/ci.yml`.
