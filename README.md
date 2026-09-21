# krx-stock-scoring

**English** · [한국어](README_KO.md)

A daily, rule-based **state score** for every listed stock on KOSPI and KOSDAQ.

Each trading day the batch snapshots its inputs, scores every ticker out of 100 points across five
axes, and publishes an immutable result set that can be traced back to the exact rule file, source
rows, and report versions that produced it.

> **Status: work in progress (M0–M2 of 8 milestones).** Scores are marked `experimental` and no
> confirmed grades are published until the M6 validation report is accepted. This is a personal
> analytics project — **not investment advice**, and not a trading system.

---

## What it does

- Builds a daily universe snapshot of KOSPI/KOSDAQ tickers (~2,770) and keeps every ticker in one of
  four explicit states — `scored`, `provisional`, `insufficient_data`, `excluded`. Nothing is
  silently dropped.
- Scores five axes out of **100 points**, with no rescaling. News can be negative, so a total below
  zero is possible and is never clipped to zero.
- Records, for every item, **what was actually observed** — the raw inputs, the missing days, and why
  an item was skipped — so a score can be re-derived from the stored evidence alone.
- Computes valuation directly from the latest DART report instead of trusting published multiples
  (see [Why we compute PER ourselves](#why-we-compute-per-ourselves)).
- Publishes atomically: a run writes its rows first, then flips a pointer under an advisory lock, so
  readers never see a half-written day.

### Score model (rules v0, 100 points)

| Axis | Points | Items |
|---|---:|---|
| Technical | **35** | alignment 11 · trend 6 · volume 6 · volume-by-price 7 · RSI/MACD 5 |
| Fundamental | **36** | PER 6 · PBR 5 · operating margin 7 · growth 6 · ROE 5 · debt ratio 3 · dividend yield 4 |
| Disclosure | **7** | DART filings over a 30-day window |
| Flow | **13** | foreign 6 · institutional 5 · short-selling share 2 |
| News | **9** | 3 related articles × ±3 (range −9 … +9) |

Margin trading (2 points) is defined but inactive. Profiles let a partial model run before every axis
is implemented: `technical` (35), `partial` (84 = technical + fundamental + flow), `common` (100).

---

## Design decisions worth knowing

**One source of truth for the rules.** `rules/v0.toml` holds every threshold, window, tier and
rounding rule. It is hashed canonically, and the hash is stored with each run — two runs with
different hashes are never joined into one time series. Tests assert the point table against values
transcribed independently from the spec, so the rule file cannot silently drift from the spec.

**A missing value is not a zero.** Investor-flow rows can be absent because a ticker had no trades
from that investor class, or because the upstream collection failed. The five investor classes sum to
zero by construction, so a NULL is resolved to 0 **only when the other classes on that row already
sum to zero** (`derived_zero`); otherwise the day counts as missing and the item can fall below its
coverage floor.

**Consecutive-buying streaks never skip a gap.** A streak stops at the first missing day rather than
jumping over it, so a data outage cannot manufacture a signal.

**Every run is immutable.** Scores, evidence rows, financial report versions and corp-code mappings
are stored per run and kept by content hash — re-running an unchanged day adds no rows. Older detail
is archived to Parquet rather than deleted.

### Why we compute PER ourselves

KRX publishes PER, PBR and ROE against the **last annual report**, which is stale for most of the
year. For Samsung Electronics on 2026-09-18 the published PER was 39.52 while the trailing-twelve-month
figure was **11.47** — different enough to reorder the entire ranking.

So six of the seven fundamental items are computed from the latest DART filing, following the
convention Korean retail portals use:

- **TTM net income** = previous fiscal year + current cumulative − prior-year same cumulative, with
  an annualization fallback when a comparable period is unavailable.
- **EPS** = TTM net income ÷ adjusted average shares, **common and preferred shares combined**.
- **BPS** = latest quarter total equity ÷ the same combined share count.
- **ROE** = TTM net income ÷ average equity.

Dividend yield is the one exception — it uses the KRX published value (`DIV`), with `DPS` kept as
evidence. Measured coverage: TTM for 2,499 tickers, annualized fallback 44, annual report 17,
not computable 25.

---

## Architecture

![architecture](docs/img/architecture.png)

The batch reads upstream data **read-only** (the session is opened with
`default_transaction_read_only=on`) and writes only to its own `kss_*` tables in a dedicated Supabase
project.

| Source | Used for | Access |
|---|---|---|
| `krx-stock-charts` (`ksc_*`) | daily bars, investor flows, short-selling, market cap, index | read-only |
| `krx-signal-alerts` (`ksa_*`) | cross-checking signal dates against score dates | read-only |
| OpenDART | financial statements, filings | REST |
| KRX via pykrx | dividend yield (`DIV`/`DPS`) | login required |

The pipeline is wired as a thin [LangGraph](docs/GRAPH.md) graph — only `scoring/graph.py` imports it,
and bulk data never enters the graph state.

![graph](docs/img/graph.png)

---

## Tech stack

Python 3.11+ · psycopg 3 · LangGraph · pykrx · Supabase (PostgreSQL) · pytest · ruff · mypy (strict)

---

## Getting started

```bash
python3 -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                  # then fill in the values
```

`.env` needs an upstream read-only connection string, the dedicated scoring database, an OpenDART key,
the role passwords used when applying the schema, and KRX credentials for pykrx. Names and notes are
in [.env.example](.env.example); values never belong in the repository.

Apply the schema to the scoring database, then run a day:

```bash
python scripts/apply_schema.py                        # 11 tables, RLS on, no anon policy
python -m scoring.run score --profile partial --dry-run       # compute only, writes nothing
python -m scoring.run score --profile partial --date 2026-09-18
python -m scoring.run score --profile technical --tickers 005930,000660
```

### Verify

```bash
ruff check .
mypy                 # scoring · scripts · tests (pyproject)
pytest tests/ -v
```

170 tests pass today. Beyond unit tests, `scripts/m2_crosscheck.py` re-derives 20 random tickers from
scratch — calling DART again and recounting flows in SQL, without touching pipeline code — and
compares them against the published run. The last pass checked 109 values with zero mismatches.

---

## Repository layout

```
scoring/
  run.py            CLI entry point
  graph.py          LangGraph wiring (the only file that imports it)
  nodes.py          pipeline steps: load → score → persist → publish
  rules.py          rule file loading, validation, canonical hashing
  domain/           pure scoring functions (technical, fundamental, financial, flow, aggregate)
  sources/          DART, KRX, corp-code adapters
  store/            schema.sql and writers
rules/v0.toml       single source of truth for thresholds and points
scripts/            schema application, probes, cross-checks, diagram generation
docs/               SPEC · PLAN · TASKS · GRAPH (Korean)
tests/              170 tests including fixture-based regression tests
```

Design documents are written in Korean: [SPEC](docs/SPEC.md) (requirements and the score model),
[PLAN](docs/PLAN.md) (architecture and milestones), [TASKS](docs/TASKS.md) (progress and a
troubleshooting log).

---

## Progress

| Milestone | Scope | State |
|---|---|---|
| M0 | Upstream measurement, contracts, skeleton | ✅ 2026-09-19 |
| M1 | Universe, technical 35, minimal storage | ✅ done, awaiting review |
| M2 | Fundamental 36, flow 13, rules frozen | ✅ done, awaiting review |
| M3 | Disclosure 7, lexicon, news 9 | planned |
| M4 | Operations, publishing, recovery | planned |
| M5 | Web dashboard behind access protection | planned |
| M6 | Score validation and release decision | planned |
| M7 | Scorer MCP server | planned |

Latest full run (`partial` profile, 84 points, 2026-09-18): 148 seconds for the whole market —
2,084 scored · 132 provisional · 369 insufficient data · 186 excluded, with 41,565 evidence rows.

---

## Disclaimer

Personal study and analysis tooling. It does not predict prices, recommend trades, or produce entry
and exit levels. Scores are rule-based descriptions of a stock's current state, remain
`experimental` until the M6 validation report, and must not be read as investment advice.
