# stock-signals

A personal, fully automated **buy/sell signal scanner** for US stocks, ETFs, indices and a few macro series (about 90 symbols). Every morning it downloads fresh prices, calculates three technical indicators on both **weekly** and **daily** charts, applies a fixed set of buy and sell rules, and publishes a plain-text report.

> Technical-analysis output only. This is not investment advice, and the rules have not been back-tested.

## How it works

```
Claude scheduled task (08:30 Malaysia time) starts the workflow
        |
        v
GitHub Actions workflow (runs the scan, about 30 seconds)
        |
        v
signals.py  --> downloads daily prices (Yahoo Finance; SOFR from FRED)
            --> builds weekly bars from the daily data
            --> calculates SKDJ, MACD and RSI
            --> applies the buy / sell rules
            --> writes the report
        |
        v
latest.txt / latest.csv  (committed back to this repository)
        |
        v
The same Claude task waits for the run to finish, reads latest.txt,
compares it with the previous report, looks up the latest good and bad
news for every company on the BUY / NEAR-BUY / SELL lists, and sends
the daily summary.
A second Claude task sends a weekly summary every Saturday morning.
```

1. **Triggers:** `.github/workflows/daily-signals.yml` can be started two ways. (a) The daily Claude task starts it at 08:30 Malaysia time through the GitHub API (`workflow_dispatch`), which starts within seconds. (b) You can start it by hand from the **Actions** tab (*Run workflow*). Both start the scan immediately. The workflow has no schedule of its own, because GitHub's scheduler can start jobs hours late.
2. **Data:** daily prices for the last 5 years from Yahoo Finance (`yfinance`). Only *completed* daily bars are used. Weekly bars (weeks ending Friday) are built from the daily data, so the current week is an in-progress candle based on the latest completed day.
3. **Report:** the newest result is always `latest.txt` (human-readable) and `latest.csv` (every indicator value). Dated copies are kept in `signal_reports/`.

## The indicators

| Indicator | Setting | Used on |
|---|---|---|
| **SKDJ** (slow KDJ) | N = 9, M = 3 | weekly and daily |
| **MACD** | fast 5, slow 15, signal 5 | weekly |
| **RSI** | 14 periods, plus a 14-period moving average of the RSI ("RSI MA") | weekly |

SKDJ follows the common Chinese charting-software definition:

```
RSV = EMA( (Close - lowest Low of N) / (highest High of N - lowest Low of N) * 100 , M )
K   = EMA(RSV, M)      -> "fast line"
D   = MA(K, M)         -> "slow line"
```

## The rules

**BUY** - all five must be true:

1. Weekly SKDJ: K above D
2. Weekly MACD histogram rising (this week higher than last week)
3. Weekly MACD line above zero **and** above its signal line
4. Weekly RSI below 70
5. Daily SKDJ: K above D

**SELL** - either rule is enough:

1. **Sell-1:** weekly RSI MA above the weekly RSI, **and** weekly MACD histogram falling, **and** daily SKDJ D above K
2. **Sell-2:** daily SKDJ K reaches 75 or higher

## Reading the report

| Section | Meaning |
|---|---|
| **BUY** | All five buy conditions are met |
| **SELL** | At least one sell rule is met (the rule is shown under each symbol) |
| **CONFLICT** | A buy and a sell rule are both true; review manually |
| **NEAR-BUY** | Four of the five buy conditions are met; the missing one is shown |
| **NO USABLE DATA** | Symbol returned nothing, or has fewer than 60 weeks of history |

Each line shows the latest close, the data date, weekly and daily SKDJ K/D values, and the weekly RSI.

## Daily news tables

The scanner only produces technical signals. The daily Claude task adds the latest company news on top of it. There is no general market-news summary; the message goes straight to the signals.

- **Which symbols:** every individual company on the BUY, NEAR-BUY and SELL lists. ETFs, indices, commodities, currencies, crypto and bond yields (for example IWM, ICLN, UFO, GOLD, SILVER, BTCUSD, USOIL, BOXX, US10Y) have no company news and are listed without a table.
- **Layout:** directly below each company's name and signal line there is a two-column table, **good news on the left and bad news on the right**.

```
NASDAQ:AMZN   close 266.93 (as of 2026-11-09)

| Good news                          | Bad news                          |
|------------------------------------|-----------------------------------|
| Partnership extended (source, date)| Shares fell 5% in a week (source) |
```

- **Content rules:** each item is one short line with its date and source name. The task searches the web for news from roughly the past 7 to 14 days, newest first, and prefers company press releases and major news outlets. It writes *none found* for an empty side. When only a headline could be read, and not the article, the item says so. Undated items or items that look older than the window are left out. Where sources disagree, both versions are shown.
- **No advice:** the news is listed as found. The task adds no recommendations or opinions.

## Automation

| What | When (Malaysia time) | What it does |
|---|---|---|
| **Daily Claude task** ("Daily stock signals from GitHub") | every day, 08:30 | Starts the workflow, waits for it to finish (up to 10 minutes), reads `latest.txt`, compares it with the previous dated report and sends: changes, full BUY and NEAR-BUY lists, SELL count by rule, symbols without data and the data date. For every company on the BUY, NEAR-BUY and SELL lists it also adds a good-news / bad-news table (see *Daily news tables*). It marks the report STALE and says why if the run failed or did not finish. |
| **Weekly Claude task** ("Weekly stock signal summary") | every Saturday, about 09:00 | Read-only. Compares the newest report (Friday's US close) with the report from a week earlier and lists new and dropped BUY, SELL (by rule) and NEAR-BUY symbols, plus symbols that entered and left a list during the week. |

The workflow has no schedule of its own: GitHub's scheduler can start jobs hours late, which is why the Claude task starts it. If the Claude task cannot start a run, its message says the report is STALE and gives the cause; you can then start the workflow by hand from the **Actions** tab.

## Files

| File | Purpose |
|---|---|
| `signals.py` | The scanner: settings, watchlist, indicators, rules, report |
| `.github/workflows/daily-signals.yml` | GitHub Actions workflow that runs the scanner when it is started |
| `latest.txt`, `latest.csv` | Newest report and all indicator values |
| `signal_reports/` | Dated copies of every report |

## Setting it up step by step (teaching steps)

1. **Create a GitHub repository** (public or private) and add `signals.py` and `.github/workflows/daily-signals.yml`.
2. **Check the workflow permission:** the workflow needs `permissions: contents: write` so it can save the report back into the repository (already in the file).
3. **Run it once by hand:** open the **Actions** tab, choose *Daily signal report*, click *Run workflow*. After about a minute `latest.txt` appears in the repository and a dated copy in `signal_reports/`.
4. **Open `latest.txt`** and read the sections (BUY, SELL, CONFLICT, NEAR-BUY, NO USABLE DATA); see *Reading the report* above.
5. **Automate the start:** create a Claude scheduled task at 08:30 Malaysia time that starts the workflow through the GitHub connection, waits, then reads and summarises `latest.txt`. The workflow deliberately has no `schedule:` trigger, because GitHub's cron can run hours late.
6. **Add the news tables:** in the same task, after reading `latest.txt`, tell Claude to search the web for the latest news on each company in the BUY, NEAR-BUY and SELL lists and to show it as a two-column table (good news left, bad news right) directly under the company's name. Skip ETFs, indices, commodities, crypto and yields. Ask for a date and source on every item and for *none found* when there is nothing. Do not ask for a market-news summary.
7. **Keep the timing rule in mind:** the scan must run after 00:00 UTC (08:00 Malaysia time), otherwise the latest US session is treated as unfinished and the data is a day old. The Claude task starts it at 08:30; if you start it by hand, do so after 08:00 Malaysia time.
8. **Add the weekly summary:** a second Claude scheduled task, Saturdays about 09:00 Malaysia time, compares this week's report with last week's.
9. **Change the watchlist or rules** by editing the settings at the top of `signals.py` (see *Customising*), commit, and start a manual run to test.
10. **Check each morning:** the daily message should show a report dated today. If it says STALE, open the **Actions** tab to see whether the run failed, is queued or did not start. Treat the news tables as leads: open the source before relying on any item.

## Customising

All settings are at the top of `signals.py`:

- **Watchlist:** edit `WATCHLIST` (TradingView-style names such as `NASDAQ:NVDA`). Names that differ on Yahoo Finance are mapped in `OVERRIDES` (for example `SP:SPX` becomes `^GSPC`, `TVC:GOLD` becomes `GC=F`).
- **Indicator settings:** `SKDJ_N`, `SKDJ_M`, `MACD_FAST`, `MACD_SLOW`, `MACD_SIG`, `RSI_LEN`, `RSI_MA_LEN`.
- **Rule thresholds:** `RSI_BUY_MAX` (70), `SELL2_LEVEL` (75) and `SELL2_LINE` (`"K"` for the fast line or `"D"` for the slow line).

Edit the file, commit it, and the next run uses the new settings.

## Running it yourself

```
pip install yfinance pandas numpy
python signals.py                       # whole watchlist
python signals.py --symbols NVDA,AAPL   # quick test
```

## Things to know

- **Run time matters.** The scan must run after 00:00 UTC (08:00 Malaysia time) so the previous US session counts as a completed bar. A run started earlier shows data that is a day old. The Claude task starts it at 08:30 Malaysia time.
- **Weekends and Mondays:** there is no new US session, so Sunday and Monday morning reports repeat Friday's data.
- **Sell-2 fires often.** In a strong uptrend, daily K stays above 75 for long stretches, so strong stocks can show SELL. Treat it as an overbought warning.
- **Data source:** Yahoo Finance via `yfinance` is free but unofficial; occasional gaps or outages can happen. SOFR comes from FRED as a single daily value, so its SKDJ is approximate.
- **GitHub schedules can run late**, sometimes by hours (the first scheduled run here started about 6 hours late), so the workflow has no schedule of its own. Runs started through the API start within seconds, which is why the Claude task triggers the workflow.
- **Same-day re-runs:** starting the workflow again on the same day overwrites that day's dated report with fresh data and commits it.
- **News tables are only as good as the search.** The search tool can return headlines without article text, dates can be missing, and some items may refer to an earlier quarter. The tables say when this is the case. The news comes from the daily Claude task, not from `signals.py`, so it is not saved in `latest.txt` or `signal_reports/`.
- **Short-history symbols** (recent listings) are skipped until they have 60 weeks of data.
- **Assumptions:** "SKDJ reaches 75" uses the K line, and the RSI MA is a 14-period simple moving average. Both can be changed in the settings above.
