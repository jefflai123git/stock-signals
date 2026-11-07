# stock-signals

A personal, fully automated **buy/sell signal scanner** for US stocks, ETFs, indices and a few macro series (about 90 symbols). Every morning it downloads fresh prices, calculates three technical indicators on both **weekly** and **daily** charts, applies a fixed set of buy and sell rules, and publishes a plain-text report.

> Technical-analysis output only. This is not investment advice, and the rules have not been back-tested.

## How it works

```
GitHub Actions (daily, 08:30 Malaysia time)
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
A scheduled Claude task reads latest.txt each morning and sends a summary
```

1. **Schedule:** `.github/workflows/daily-signals.yml` runs at 00:30 UTC (08:30 Malaysia time) every day. It can also be started by hand from the **Actions** tab (*Run workflow*).
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

## Files

| File | Purpose |
|---|---|
| `signals.py` | The scanner: settings, watchlist, indicators, rules, report |
| `.github/workflows/daily-signals.yml` | Daily schedule on GitHub Actions |
| `latest.txt`, `latest.csv` | Newest report and all indicator values |
| `signal_reports/` | Dated copies of every report |

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

- **Run time matters.** The scan must run after 00:00 UTC (08:00 Malaysia time) so the previous US session counts as a completed bar. The workflow is set for 08:30 Malaysia time.
- **Weekends and Mondays:** there is no new US session, so Sunday and Monday morning reports repeat Friday's data.
- **Sell-2 fires often.** In a strong uptrend, daily K stays above 75 for long stretches, so strong stocks can show SELL. Treat it as an overbought warning.
- **Data source:** Yahoo Finance via `yfinance` is free but unofficial; occasional gaps or outages can happen. SOFR comes from FRED as a single daily value, so its SKDJ is approximate.
- **GitHub schedules can run late** by several minutes during busy periods.
- **Short-history symbols** (recent listings) are skipped until they have 60 weeks of data.
- **Assumptions:** "SKDJ reaches 75" uses the K line, and the RSI MA is a 14-period simple moving average. Both can be changed in the settings above.
