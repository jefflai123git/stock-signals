#!/usr/bin/env python3
"""
Daily buy/sell signal scanner  (weekly + daily SKDJ, MACD(5,15,5), RSI)

SETUP (one time)
    pip install yfinance pandas numpy

RUN
    python signals.py                      # scan the whole watchlist
    python signals.py --symbols NVDA,AAPL  # quick test on a few tickers

It prints the report and saves it to ./signal_reports/signals_YYYY-MM-DD.txt
(+ a .csv with every indicator value).

SCHEDULE AT 8AM MALAYSIA TIME (computer clock must be on Malaysia time)
    Windows : Task Scheduler -> Create Basic Task -> Daily 08:00 ->
              Program: python   Arguments: C:\\path\\to\\signals.py
    Mac/Linux (crontab -e):
              0 8 * * *  /usr/bin/python3 /path/to/signals.py

RULES (all editable in the SETTINGS block below)
    BUY  = weekly SKDJ K > D
           AND weekly MACD histogram rising (this week > last week)
           AND weekly MACD line > 0 AND MACD line > signal line
           AND weekly RSI < 70
           AND daily SKDJ K > D
    SELL = (1) weekly RSI-MA > weekly RSI
               AND weekly MACD histogram falling
               AND daily SKDJ D > K
           OR
           (2) daily SKDJ reaches 75  (K line by default, see SELL2_LINE)

Notes
    * Only COMPLETED daily bars are used. The current week's bar is built from
      the completed daily bars of that week (in-progress weekly candle).
    * SKDJ (TongDaXin definition):
          RSV = EMA((C - LLV(L,N)) / (HHV(H,N) - LLV(L,N)) * 100, M)
          K   = EMA(RSV, M)        (fast line)
          D   = MA(K, M)           (slow line)
    * Data comes from Yahoo Finance via yfinance (unofficial, free). On Sunday
      and Monday mornings (Malaysia time) there is no new US session, so the
      report repeats Friday's data.
    * Technical-analysis output only; not investment advice.
"""
import argparse
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

# =============================== SETTINGS ===============================
SKDJ_N, SKDJ_M = 9, 3
MACD_FAST, MACD_SLOW, MACD_SIG = 5, 15, 5
RSI_LEN = 14
RSI_MA_LEN = 14          # length of the SMA drawn on the RSI ("RSI-based MA")
RSI_BUY_MAX = 70
SELL2_LEVEL = 75
SELL2_LINE = "K"         # "K" = fast line, "D" = slow line
HISTORY = "5y"           # enough weeks for RSI / MA warm-up
MIN_WEEKS = 60           # skip symbols with a shorter history
CHUNK = 25               # tickers per Yahoo request
OUTPUT_DIR = Path(__file__).resolve().parent / "signal_reports"

WATCHLIST = """
###Indices,SP:SPX,NASDAQ:QQQ,BITSTAMP:BTCUSD,TVC:GOLD,CMCMARKETS:SILVER,AMEX:IWM,CAPITALCOM:USOIL,FRED:SOFR,TVC:US10Y,TVC:US30Y,FX_IDC:USDMYR,CBOE:VIX,AMEX:SPMO,CBOE:BOXX,NASDAQ:UFO,NASDAQ:ICLN,AMEX:SOXL,###STOCKS,NASDAQ:AAOI,NASDAQ:LITE,NYSE:CIEN,NASDAQ:RKLB,NASDAQ:ASTS,NASDAQ:ONDS,NYSE:PL,NASDAQ:VSAT,NASDAQ:ECHO,NASDAQ:KTOS,NASDAQ:AXON,NYSE:RTX,NASDAQ:AVAV,NASDAQ:RCAT,NASDAQ:MU,NASDAQ:SNDK,NASDAQ:WDC,AMEX:FNGU,NASDAQ:NVDA,NASDAQ:AAPL,NASDAQ:AMZN,NASDAQ:GOOG,NASDAQ:META,NASDAQ:MSFT,NASDAQ:NFLX,NASDAQ:TSLA,NASDAQ:PLTR,NASDAQ:AVGO,NASDAQ:AMD,NASDAQ:APLD,NASDAQ:ARM,NASDAQ:ASML,NASDAQ:CRDO,NASDAQ:AMAT,NASDAQ:INTC,NASDAQ:NBIS,NASDAQ:QCOM,NYSE:TSM,NYSE:LLY,NYSE:NVO,NYSE:HIMS,NASDAQ:SOFI,NASDAQ:AFRM,NASDAQ:UPST,NASDAQ:COIN,NYSE:MP,NYSE:CCJ,AMEX:UUUU,NYSE:FCX,NASDAQ:CRWV,NASDAQ:IREN,NASDAQ:CEG,NASDAQ:NNE,NYSE:NRG,NYSE:SMR,NYSE:OKLO,NYSE:BE,NYSE:VST,NYSE:NKE,NASDAQ:LUNR,NASDAQ:POET,NYSE:NOK,NASDAQ:SIMO,NASDAQ:MRVL,NASDAQ:TSEM,NASDAQ:AXTI,CBOE:DRAM,NYSE:COHR,NASDAQ:FORM,NYSE:VSH,NYSE:GLW,NYSE:TE,NASDAQ:SPCX,NASDAQ:VICR
"""

# TradingView symbol -> Yahoo Finance ticker (anything not listed uses the part
# after the colon, e.g. NASDAQ:NVDA -> NVDA). "FRED:" entries come from FRED.
OVERRIDES = {
    "SP:SPX": "^GSPC",
    "BITSTAMP:BTCUSD": "BTC-USD",
    "TVC:GOLD": "GC=F",
    "CMCMARKETS:SILVER": "SI=F",
    "CAPITALCOM:USOIL": "CL=F",
    "TVC:US10Y": "^TNX",
    "TVC:US30Y": "^TYX",
    "FX_IDC:USDMYR": "MYR=X",
    "CBOE:VIX": "^VIX",
    "FRED:SOFR": "FRED:SOFR",
}
# =========================================================================


# ------------------------------ indicators ------------------------------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def skdj(df, n=SKDJ_N, m=SKDJ_M):
    low = df["Low"].rolling(n).min()
    high = df["High"].rolling(n).max()
    rng = (high - low).replace(0, np.nan)
    raw = ((df["Close"] - low) / rng * 100).where(rng.notna(), 50.0)
    raw = raw.where(low.notna())
    rsv = ema(raw.dropna(), m).reindex(df.index)
    k = ema(rsv.dropna(), m).reindex(df.index)
    d = k.rolling(m).mean()
    return k, d


def macd(close, fast=MACD_FAST, slow=MACD_SLOW, sig=MACD_SIG):
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, sig)
    return dif, dea, dif - dea


def rsi(close, n=RSI_LEN):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


# ------------------------------ data helpers ----------------------------
def completed_bars(df):
    """Keep only finished daily bars (drop anything dated today, UTC)."""
    df = df.copy()
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    return df[df.index < today].sort_index()


def to_weekly(daily):
    return (
        daily.resample("W-FRI")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
        .dropna(subset=["Close"])
    )


def parse_watchlist(text):
    items, section = [], "Watchlist"
    for raw in text.replace("\n", ",").split(","):
        tok = raw.strip()
        if not tok:
            continue
        if tok.startswith("###"):
            section = tok.lstrip("#").strip() or section
            continue
        items.append((section, tok))
    return items


def to_source_ticker(tv):
    return OVERRIDES.get(tv, tv.split(":")[-1])


def fetch_fred(series):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
    df = pd.read_csv(url, parse_dates=[0], index_col=0, na_values=".")
    s = df.iloc[:, 0].dropna()
    s = s[s.index >= pd.Timestamp.today() - pd.DateOffset(years=5)]
    # FRED has a single value per day: use it as O/H/L/C
    return pd.DataFrame({"Open": s, "High": s, "Low": s, "Close": s})


def download_all(tickers):
    import yfinance as yf

    frames = {}
    yahoo = [t for t in tickers if not t.startswith("FRED:")]
    for i in range(0, len(yahoo), CHUNK):
        chunk = yahoo[i:i + CHUNK]
        try:
            raw = yf.download(chunk, period=HISTORY, interval="1d",
                              group_by="ticker", auto_adjust=True,
                              threads=True, progress=False)
        except Exception as exc:  # network / Yahoo hiccup
            print(f"  download error for {chunk[:3]}...: {exc}")
            continue
        if not isinstance(raw.columns, pd.MultiIndex):
            raw.columns = pd.MultiIndex.from_product([chunk[:1], raw.columns])
        for tk in chunk:
            try:
                df = raw[tk][["Open", "High", "Low", "Close"]].dropna(subset=["Close"])
            except KeyError:
                continue
            if len(df):
                frames[tk] = df
    for tk in tickers:
        if tk.startswith("FRED:"):
            try:
                frames[tk] = fetch_fred(tk.split(":", 1)[1])
            except Exception as exc:
                print(f"  FRED error for {tk}: {exc}")
    return frames


# ------------------------------ evaluation ------------------------------
def evaluate(daily_raw):
    d = completed_bars(daily_raw)
    if d.empty:
        return {"status": "no completed bars"}
    w = to_weekly(d)
    if len(w) < MIN_WEEKS:
        return {"status": f"insufficient history ({len(w)} weeks)"}

    dk, dd = skdj(d)
    wk, wd = skdj(w)
    dif, dea, hist = macd(w["Close"])
    wr = rsi(w["Close"])
    wr_ma = wr.rolling(RSI_MA_LEN).mean()

    v = {
        "asof": d.index[-1].date().isoformat(),
        "close": float(d["Close"].iloc[-1]),
        "w_K": wk.iloc[-1], "w_D": wd.iloc[-1],
        "d_K": dk.iloc[-1], "d_D": dd.iloc[-1],
        "macd": dif.iloc[-1], "signal": dea.iloc[-1],
        "hist": hist.iloc[-1], "hist_prev": hist.iloc[-2],
        "w_rsi": wr.iloc[-1], "w_rsi_ma": wr_ma.iloc[-1],
    }
    if any(pd.isna(x) for k_, x in v.items() if k_ not in ("asof", "close")):
        return {"status": "insufficient data for indicators"}

    buy_checks = {
        "weekly SKDJ K>D": v["w_K"] > v["w_D"],
        "weekly MACD hist rising": v["hist"] > v["hist_prev"],
        "weekly MACD>0 and >signal": v["macd"] > 0 and v["macd"] > v["signal"],
        f"weekly RSI<{RSI_BUY_MAX}": v["w_rsi"] < RSI_BUY_MAX,
        "daily SKDJ K>D": v["d_K"] > v["d_D"],
    }
    sell1 = (v["w_rsi_ma"] > v["w_rsi"]) and (v["hist"] < v["hist_prev"]) and (v["d_D"] > v["d_K"])
    sell2_val = v["d_K"] if SELL2_LINE.upper() == "K" else v["d_D"]
    sell2 = sell2_val >= SELL2_LEVEL

    buy = all(buy_checks.values())
    missing = [k_ for k_, ok in buy_checks.items() if not ok]
    sells = [n for n, f in (("Sell-1 (weekly RSI/MACD + daily D>K)", sell1),
                            (f"Sell-2 (daily {SELL2_LINE} >= {SELL2_LEVEL})", sell2)) if f]

    if buy and sells:
        signal = "CONFLICT"
    elif buy:
        signal = "BUY"
    elif sells:
        signal = "SELL"
    else:
        signal = "-"
    v.update(status="ok", signal=signal, buy_missing="; ".join(missing),
             sell_reason="; ".join(sells), buy_score=len(buy_checks) - len(missing))
    return v


# ------------------------------ reporting -------------------------------
def fmt_line(r):
    stale = ""
    try:
        age = (pd.Timestamp.today().normalize() - pd.Timestamp(r["asof"])).days
        if age > 5:
            stale = f"  [STALE data {r['asof']}]"
    except Exception:
        pass
    return (f"  {r['symbol']:<22} close {r['close']:>10.2f} (as of {r['asof']})"
            f"  wK/D {r['w_K']:.0f}/{r['w_D']:.0f}  dK/D {r['d_K']:.0f}/{r['d_D']:.0f}"
            f"  wRSI {r['w_rsi']:.0f}{stale}")


def build_report(rows, now):
    ok = [r for r in rows if r.get("status") == "ok"]
    bad = [r for r in rows if r.get("status") != "ok"]
    out = [f"SIGNAL REPORT  {now:%Y-%m-%d %H:%M} (local time)",
           f"Scanned {len(rows)} symbols: {len(ok)} evaluated, {len(bad)} without usable data",
           "=" * 78]

    def block(title, items, extra=None):
        out.append(f"\n{title} ({len(items)})")
        if not items:
            out.append("  none")
        for r in items:
            out.append(fmt_line(r))
            if extra and r.get(extra):
                out.append(f"      -> {r[extra]}")

    block("BUY - all 5 conditions met", [r for r in ok if r["signal"] == "BUY"])
    block("SELL", [r for r in ok if r["signal"] == "SELL"], "sell_reason")
    block("CONFLICT - buy conditions AND a sell rule both true (review manually)",
          [r for r in ok if r["signal"] == "CONFLICT"], "sell_reason")
    near = sorted([r for r in ok if r["signal"] == "-" and r["buy_score"] == 4],
                  key=lambda r: r["symbol"])
    block("NEAR-BUY - 4 of 5 buy conditions met", near, "buy_missing")
    if bad:
        out.append(f"\nNO USABLE DATA ({len(bad)})")
        for r in bad:
            out.append(f"  {r['symbol']:<22} {r.get('status')}")
    out.append("\nTechnical-analysis output only; not investment advice.")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="comma separated Yahoo tickers for a quick test")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    if args.symbols:
        items = [("Test", s.strip()) for s in args.symbols.split(",") if s.strip()]
    else:
        items = parse_watchlist(WATCHLIST)

    mapping = {tv: to_source_ticker(tv) for _, tv in items}
    print(f"Downloading {len(mapping)} symbols ...")
    frames = download_all(sorted(set(mapping.values())))

    rows = []
    for section, tv in items:
        tk = mapping[tv]
        res = evaluate(frames[tk]) if tk in frames else {"status": "no data returned"}
        res.update(symbol=tv, section=section)
        rows.append(res)

    now = dt.datetime.now()
    report = build_report(rows, now)
    print(report)

    if not args.no_save:
        OUTPUT_DIR.mkdir(exist_ok=True)
        stem = OUTPUT_DIR / f"signals_{now:%Y-%m-%d}"
        stem.with_suffix(".txt").write_text(report, encoding="utf-8")
        pd.DataFrame(rows).to_csv(stem.with_suffix(".csv"), index=False)
        print(f"\nSaved to {stem}.txt / .csv")


if __name__ == "__main__":
    main()
