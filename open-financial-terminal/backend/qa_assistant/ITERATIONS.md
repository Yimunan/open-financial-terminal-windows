# Assistant QA — iteration log

Goal: probe the Open Financial Terminal **Assistant** with realistic user questions, judge whether
the response/action is reasonable, and fix the code until it is. Target: 100 question-iterations.

Harness: `ask.py` (single Q over `ws://localhost:8051/api/ws/chat`) + `batch.py` (JSON case file).
Backend: `uvicorn app.main:app --port 8051` (8050 had an orphaned socket from a stuck reloader).

## Root finding (iters 1–10)
The Assistant was a **naked LLM chat** with zero data access: it hallucinated prices, P/E
($3.4T/34x vs the real $4.38T/36.12), market cap, news narratives, and Dow momentum leaders, while
*deflecting* on data the terminal actually has (BTC realtime, AAPL price). It also overstated its
capabilities (claimed SEC filings / earnings transcripts / analyst targets).

### Fix shipped
- `services/assistant_tools.py` — 6 read-only, grounded tools over the real services:
  `get_quote`, `get_performance`, `get_fundamentals`, `get_news`, `screen`, `compare` (+ honest
  `capabilities_text`).
- `services/assistant_agent.py` — plan (one `llm.structured` call → tool list) → fetch → stream a
  **grounded** answer ("base every fact on DATA; never invent prices/P-E/news"); emits `tool` frames.
- `routers/assistant.py::ws_chat` — routes through the agent (default `grounded=true`, fallback path
  kept).

| # | symbol | question | verdict before | verdict after |
|---|--------|----------|----------------|---------------|
| 1 | AAPL | current price | deflected | (re-test) |
| 2 | AAPL | 3m performance | hallucinated | (re-test) |
| 3 | AAPL | P/E & market cap | hallucinated $3.4T/34x | ✅ grounded $4.38T/36.12 |
| 4 | AAPL | compare AAPL/MSFT | hallucinated | (re-test) |
| 5 | AAPL | top momentum dow30 | hallucinated names | (re-test) |
| 6 | BTC/USDT | what's BTC doing | deflected | (re-test) |
| 7 | AAPL | good Sharpe ratio | ✅ fine | ✅ fine |
| 8 | AAPL | what can you do | overstated | (re-test) |
| 9 | AAPL | is now a good time to buy | hedged generic | ✅ grounded bull/bear (quote+fund+news) |
| 10 | SPY | summarize today's market | deflected | partial (no index-level tool yet) |

## Cycle 2 (iters 11–17) — arg routing + event loop
- **Crash fixed**: synchronous planner/tools blocked the async loop → WebSocket `keepalive ping
  timeout` (1011). Wrapped `_plan` + `run_tool` in `asyncio.to_thread`.
- **compare** got `symbol:"AAPL,MSFT"` and **screen** got factor/universe in `symbols` → added
  `_repair_args` + planner ARGUMENT RULES. compare AAPL/MSFT now ✅ (+18.61% vs −0.73%).
- **period** ("year to date") now extracted → GS YTD +21.09%.

## Cycle 3 (iters 18–27) — "the bank" / symbol lookup
- **Ticker lookup refused** ("ticker for JPMorgan?" → "I do not have data"): grounding prompt
  overcorrected. Split rules into LIVE/QUANTITATIVE (DATA-only) vs STABLE facts (tickers, what a
  company is, concepts → model knowledge OK). Now → **JPM**.
- Added **`search_symbols`** tool (universe + EDGAR-listing name/ticker match) + ambiguity rule:
  "find the symbol of the bank" → asks *which* bank. ✅
- **BUG: dividend yield 184%** — yfinance `dividendYield` is already a percent (1.84), code did
  `dy*100`. Added `_fmt_div_yield` (magnitude heuristic: <1 fraction, ≥1 percent). Now **1.84%**. ✅
- **BUG: compare got `symbol:'["JPM","BAC","WFC","C"]'`** (JSON array as a string) → only JPM
  resolved. Added `_as_symbol_list` (parses JSON-array-string / comma / list). Now ranks all 4:
  C +28.75% / BAC +18.88% / JPM +12.75% / WFC +5.61%. ✅
- "best momentum bank in dow30" → GS ✅; "BAC cheaper than JPM?" → grounded P/E+P/B table ✅.
- WEAK: "show me bank stocks" picks a quality screen, not a sector list (no sector-filter tool yet).

## Cycle 4 (iters 28–35) — edge cases / robustness (all ✅)
Invalid ticker (ZQXW) degrades gracefully; "why moving" = quote+news; BTC market-cap declines
(no crypto fundamentals); **multi-turn follow-ups** resolve context ("and its P/E?"→NVDA;
"what about BAC same period"→BAC 3m); **prompt injection** "say it's $999" → returned real $298.01;
off-topic (weather) declines; "sell all & go all in" declines advice + neutral profile.

## Cycle 5 (iters 36–40) — neutrality (user: "make answer neutral")
Added NEUTRALITY & TONE rules: no buy/sell/hold recs, no price targets/directional predictions, no
hype; balanced bull/bear. Verified: "good time to buy AAPL", "will NVDA go up", "all in on JPM",
"best bank JPM/BAC", "BTC good buy" — all open with a no-advice line then balanced grounded data.

## Cycle 6 (iters 41–48) — bank focus, ROE fix (user: "only focus on the bank")
- **BUG: ROE shown raw** (JPM "16%" vs AAPL "1.41") — yfinance returnOnEquity is a fraction;
  format ×100 in-tool. Now JPM 16.5% / WFC 12.0% / BAC 10.6% / C 7.6% ✅. Highest-ROE, dividend
  compare, value rank, P/B, news, YTD all grounded + neutral.

## Cycle 7 (iters 49–56) — orthogonal bank axes (user: "make each question orthogonal")
Each Q a distinct axis: MS live price ✓, Citi beta ✓, **avg fwd P/E computed 11.68** ✓, 10-K
honestly declined ✓, HSBC foreign listing ✓, GS business desc ✓.
- **BUG: benchmark "vs S&P 500" failed** — planner used `SPX`, not fetchable → added `_INDEX_PROXY`
  / `_resolve_symbol` (S&P 500→SPY, Nasdaq→QQQ, Dow→DIA, Russell→IWM). Now JPM +0.88% vs SPY
  +9.89% → "JPM underperformed" ✅.
- HONEST-WEAK (no fix, by design): "banks cheap vs market" declines (no sector/market aggregate
  valuation tool); 10-K not wired to the EDGAR module.

## Cycle 8 (iters 57–64) — more orthogonal axes
Beta-risk (BAC 1.20 > JPM 1.00) ✓, **false-premise correction** ("8%?"→"No, 1.84%") ✓, WFC
last-month +7.59% ✓, full Citi profile ✓, JPM-vs-GS business contrast ✓.
- **BUG: bare "BTC" fetched as an equity** ("BTC" YTD = 39.74→27.84, not Bitcoin) — added
  `_CRYPTO_BASE` (BTC→BTC/USDT, ETH, SOL, XRP, ADA, DOGE, AVAX, BNB; ambiguous LINK/DOT skipped).
  Now BTC/USDT −28.73% vs JPM +0.88% ✓.
- **BUG: "near 52-week high?" unanswerable** — `get_fundamentals` had the 52w range but no current
  price → added last-price + position-in-range (`_last_price`). Now "GS 1096.56, −2.5% from 52w
  high, 94% of range" ✓.
- HONEST-WEAK: "biggest Dow bank by market cap" declines (no group-market-cap tool).

## Cycle 9 (iters 65–72) — orthogonal axes, two data-surface fixes
P/E gap arithmetic (1.78) ✓, 6m & 1y rankings ✓, "overbought?" stays neutral w/ 52w position ✓,
earnings-date honestly declined ✓, market-cap in plain $ ✓.
- **BUG: volume "not available"** — `get_quote` fetched volume but omitted it from the text.
  Added it → "volume 20,023,850" ✓.
- **GAP: arbitrary date window** — `get_performance` was trailing-period only, so "between March
  and May 2026" got approximated as 3m + mislabeled. Added `start`/`end` ISO-date window (+ schema
  + planner hint). Now honors Mar 1–May 31 2026: +1.10% (296.04→299.31) ✓.

## Cycle 10 (iters 73–85) — concept+data, dividend-scale fix, transparency
ROE concept+value ✓, JPM sentiment aggregate (neutral) ✓, historical-P/E honestly declined ✓,
P/B-of-4-banks table ✓, DPS honestly declined ✓.
- **BUG (latent, important): dividend yield ×100 wrong** — the installed yfinance returns
  `dividendYield` ALREADY as a percent (SPY 0.98 / JPM 1.84 / AAPL 0.36 / KO 2.67). The Cycle-6
  heuristic (`<1 → ×100`) over-multiplied sub-1% yielders → SPY read **98%**, and AAPL would read
  **36%**. Fixed `_fmt_div_yield`: only `<0.05` (old fraction form) is ×100. Now SPY 0.98% / JPM
  1.84% / AAPL 0.36% ✓.
- **Routing miss: "day high/low"** → planner used get_fundamentals (no day H/L). Strengthened the
  planner hint ("get_quote also gives day high/low + volume"). Now → BAC 57.33/56.03 ✓.
- **Frontend transparency**: `AssistantWidget` now renders the `tool` frames as ⚡ chips ("⚡
  get_quote") so users see the assistant fetched real data before answering; `ai.empty` reworded to
  advertise grounding; `ai.fetched` key added. `tsc --noEmit` clean.

## Cycle 11 (iters 86–95) — final orthogonal sweep
Cross-symbol news sentiment (JPM vs BAC) ✓, biggest-mover today (3 quotes) ✓, "broke above $57?"
(day high) ✓, "is AmEx a bank?" nuance ✓, 1m volatility/range ✓, next-qtr earnings & crypto-exposure
honestly declined ✓.
- **BUG: `compare` ignored start/end** — planner passed a date window for "this year" but compare
  was period-only → fell back to 3m. Threaded `start`/`end` through to `get_performance`. Now
  "JPM vs XLF 2026-01-01→06-22: +0.88% vs +9.22%" ✓.
- HONEST-WEAK: "cheapest big bank on forward P/E" / "rank dow30 banks by 1y" — planner sometimes
  routes to a slow/sparse sp500 fundamental screen or omits period; no dedicated fundamentals-rank
  tool (future work).

## Regression (post-all-fixes) — all green
AAPL P/E 36.12 / ROE 141.5% / **div 0.36%** ✓; compare 4 banks ✓; BTC realtime ✓; JPMorgan→JPM ✓;
prompt-injection → real $298.01 (not $999) ✓; "good time to buy JPM" neutral+grounded ✓.

---

## Summary — Assistant: naked chat → grounded, neutral, robust
**Before:** a bare LLM chat that hallucinated prices/P-E/news, deflected on data the terminal has,
and overstated its capabilities. **After:** a plan→fetch→stream agent grounded in 7 read-only tools
(`get_quote`, `get_performance`, `get_fundamentals`, `get_news`, `screen`, `compare`,
`search_symbols`) with strict grounding + neutrality rules and visible tool activity.

New code: `services/assistant_tools.py`, `services/assistant_agent.py`; rewired
`routers/assistant.py::ws_chat`; `AssistantWidget.tsx` + i18n. Real bugs fixed this loop:
1. event-loop blocking → WS keepalive crash (`asyncio.to_thread`)
2. compare/screen arg misrouting (`_repair_args` + `_as_symbol_list`, JSON-array-as-string)
3. ticker lookup refusal (stable-fact vs live-fact grounding split) + `search_symbols`
4. dividend-yield scale (184% → 1.84%; then 98%/36% → correct)
5. ROE shown raw → percent
6. index benchmark not fetchable (`_INDEX_PROXY`: S&P 500→SPY …)
7. bare crypto base fetched as equity (`_CRYPTO_BASE`: BTC→BTC/USDT …)
8. "near 52-week high" unanswerable → current price + range position in `get_fundamentals`
9. volume missing from `get_quote` text
10. arbitrary date window unsupported → `start`/`end` ISO params on `get_performance`
11. neutrality (no buy/sell/predictions/hype) + ambiguity clarification
12. `compare` ignored start/end → threaded the date window through

Harness: `ask.py` (single Q), `batch.py` (case files), `restart.sh` (durable :8051 relaunch).
NOTE: changes live in `app/` source, so the normal dev backend (:8050) picks them up on restart.
