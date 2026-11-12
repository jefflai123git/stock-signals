#!/usr/bin/env python3
"""
Backtest of the signals.py BUY / SELL rules, 2026-01-01 -> latest completed day.

* Uses the exact indicator functions and settings from signals.py.
* Point-in-time: for every trading day T the weekly bars are rebuilt from the
  daily bars up to and including T (in-progress weekly candle), just like the
  live scanner sees them on the morning after T.  Nothing after T is used.
* Fills: signal on T's close -> trade at T+1's open.  Long only, one position
  per symbol, no costs, no slippage.  Open positions are marked at the last close.
* Entry: BUY rule true and the variant's exit rule false (main variant = the
  scanner's "BUY" state; CONFLICT days do not open a trade).
* Exit variants: "Sell-1 or Sell-2" (the actual rules), "Sell-1 only", "Sell-2 only".

Run:  python backtest.py            (writes backtest_results/)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import signals as S

START = pd.Timestamp("2026-01-01")
OUT = Path(__file__).resolve().parent / "backtest_results"

GROUPS = {
    "Index / ETF": ["^GSPC", "QQQ"],
    "Mega-7": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META", "TSLA"],
    "AI & Semis": ["AVGO", "AMD", "ARM", "ASML", "TSM", "MU", "INTC", "QCOM", "AMAT",
                   "MRVL", "CRDO", "SNDK", "WDC", "TSEM", "AXTI", "COHR", "FORM", "VSH",
                   "LITE", "AAOI", "CIEN", "POET", "SIMO", "VICR", "GLW",
                   "PLTR", "NBIS", "CRWV", "APLD", "IREN"],
}
LABEL = {"^GSPC": "SPX"}
VARIANTS = {"Sell-1 or Sell-2": ("s1", "s2"), "Sell-1 only": ("s1",), "Sell-2 only": ("s2",)}
FWD = (5, 10, 20)


# ----------------------------------------------------------------- signals
def daily_signal_table(raw, last_day):
    """One row per trading day >= START with buy / sell1 / sell2 flags."""
    d = raw.copy()
    idx = pd.to_datetime(d.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    d.index = idx.normalize()
    d = d[d.index <= last_day].sort_index()
    d = d[~d.index.duplicated(keep="last")]
    dk, dd = S.skdj(d)                    # causal: only uses past bars
    rows = []
    for T in d.index[d.index >= START]:
        w = S.to_weekly(d.loc[:T])
        r = {"date": T, "open": d.at[T, "Open"], "close": d.at[T, "Close"],
             "ok": False, "buy": False, "s1": False, "s2": False, "score": np.nan}
        if len(w) >= S.MIN_WEEKS:
            wk, wd = S.skdj(w)
            dif, dea, hist = S.macd(w["Close"])
            wr = S.rsi(w["Close"])
            wr_ma = wr.rolling(S.RSI_MA_LEN).mean()
            v = [wk.iloc[-1], wd.iloc[-1], dk.at[T], dd.at[T], dif.iloc[-1], dea.iloc[-1],
                 hist.iloc[-1], hist.iloc[-2], wr.iloc[-1], wr_ma.iloc[-1]]
            if not any(pd.isna(x) for x in v):
                w_K, w_D, d_K, d_D, m, sg, h, hp, rs, rsm = v
                checks = [w_K > w_D, h > hp, (m > 0 and m > sg), rs < S.RSI_BUY_MAX, d_K > d_D]
                s2v = d_K if S.SELL2_LINE.upper() == "K" else d_D
                r.update(ok=True, buy=all(checks), score=sum(checks),
                         s1=bool(rsm > rs and h < hp and d_D > d_K),
                         s2=bool(s2v >= S.SELL2_LEVEL))
        rows.append(r)
    return pd.DataFrame(rows).set_index("date")


# ----------------------------------------------------------------- trading
def simulate(t, exit_cols):
    exit_ = t[list(exit_cols)].any(axis=1).values
    entry = (t["buy"].values & ~exit_)
    o, c, dates = t["open"].values, t["close"].values, t.index
    n = len(t)
    trades, pos, ei = [], False, None
    daily = np.zeros(n)                   # strategy daily return (mark-to-market)
    inpos = np.zeros(n, dtype=bool)
    for i in range(n - 1):
        if not pos and entry[i]:
            pos, ei = True, i + 1
        elif pos and exit_[i]:
            trades.append((ei, i + 1, o[i + 1] / o[ei] - 1, False))
            pos = False
    if pos:
        trades.append((ei, n - 1, c[-1] / o[ei] - 1, True))
    for ei, xi, _, is_open in trades:
        # entry day: open -> close ; held days: close -> close ; exit day: prev close -> open
        daily[ei] += c[ei] / o[ei] - 1 if (is_open or xi > ei) else 0
        for j in range(ei + 1, xi):
            daily[j] += c[j] / c[j - 1] - 1
        if not is_open and xi > ei:
            daily[xi] += o[xi] / c[xi - 1] - 1
        elif is_open and xi > ei:
            daily[xi] += c[xi] / c[xi - 1] - 1
        inpos[ei:xi + (1 if is_open else 0)] = True
    tr = pd.DataFrame([{"entry_date": dates[a].date(), "entry_px": o[a],
                        "exit_date": dates[b].date(), "exit_px": (c[b] if op else o[b]),
                        "ret": r, "hold_days": b - a, "still_open": op}
                       for a, b, r, op in trades])
    return tr, pd.Series(daily, index=dates), inpos


def max_dd(daily_ret):
    eq = (1 + pd.Series(daily_ret)).cumprod()
    return float((eq / eq.cummax() - 1).min()) if len(eq) else np.nan


def sharpe(daily_ret):
    s = pd.Series(daily_ret)
    return float(s.mean() / s.std() * np.sqrt(252)) if s.std() > 0 else np.nan


def trade_stats(tr):
    if tr is None or tr.empty:
        return dict(trades=0, win_rate=np.nan, avg_ret=np.nan, median_ret=np.nan,
                    avg_win=np.nan, avg_loss=np.nan, profit_factor=np.nan,
                    avg_hold=np.nan, best=np.nan, worst=np.nan)
    r = tr["ret"]
    wins, losses = r[r > 0], r[r <= 0]
    return dict(trades=len(r), win_rate=len(wins) / len(r), avg_ret=r.mean(),
                median_ret=r.median(),
                avg_win=wins.mean() if len(wins) else np.nan,
                avg_loss=losses.mean() if len(losses) else np.nan,
                profit_factor=(wins.sum() / -losses.sum()) if losses.sum() < 0 else np.inf,
                avg_hold=tr["hold_days"].mean(), best=r.max(), worst=r.min())


def signal_events(t, sym):
    """Forward returns after the first day of each BUY / SELL episode."""
    ev = []
    sell = (t["s1"] | t["s2"]).values
    buy = (t["buy"].values & ~sell)
    o, c = t["open"].values, t["close"].values
    for kind, flag in (("BUY", buy), ("SELL", sell)):
        prev = np.r_[False, flag[:-1]]
        for i in np.where(flag & ~prev)[0]:
            if i + 1 >= len(t):
                continue
            row = {"symbol": sym, "kind": kind, "date": t.index[i].date()}
            for k in FWD:
                j = i + k
                row[f"fwd{k}"] = (c[j] / o[i + 1] - 1) if j < len(t) else np.nan
            ev.append(row)
    return ev


# ----------------------------------------------------------------- main
def pct(x, nd=1):
    return "n/a" if x is None or pd.isna(x) else (f"{x*100:+.{nd}f}%")


def main():
    tickers = [s for g in GROUPS.values() for s in g]
    frames = S.download_all(tickers)
    last_day = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(days=1)
    OUT.mkdir(exist_ok=True)

    per_sym, all_trades, events, daily_by = [], [], [], {}
    for g, syms in GROUPS.items():
        for s in syms:
            lab = LABEL.get(s, s)
            if s not in frames:
                per_sym.append({"group": g, "symbol": lab, "note": "no data"})
                continue
            t = daily_signal_table(frames[s], last_day)
            if t.empty:
                per_sym.append({"group": g, "symbol": lab, "note": "no 2026 bars"})
                continue
            first_ok = t.index[t["ok"]].min() if t["ok"].any() else None
            bh_daily = np.r_[t["close"].iloc[0] / t["open"].iloc[0] - 1,
                             t["close"].pct_change().values[1:]]
            bh = t["close"].iloc[-1] / t["open"].iloc[0] - 1
            events += signal_events(t, lab)
            daily_by.setdefault(("B&H", g), {})[lab] = pd.Series(bh_daily, index=t.index)
            for vname, cols in VARIANTS.items():
                tr, dret, inpos = simulate(t, cols)
                st = trade_stats(tr)
                st.update(group=g, symbol=lab, variant=vname,
                          total_ret=float((1 + dret).prod() - 1), bh_ret=float(bh),
                          max_dd=max_dd(dret.values), bh_max_dd=max_dd(bh_daily),
                          sharpe=sharpe(dret.values), bh_sharpe=sharpe(bh_daily),
                          exposure=float(inpos.mean()), first_signal_day=first_ok,
                          buy_days=int((t["buy"]).sum()), days=len(t),
                          first_bar=t.index[0].date(), last_bar=t.index[-1].date())
                per_sym.append(st)
                daily_by.setdefault((vname, g), {})[lab] = dret
                if not tr.empty:
                    tr.insert(0, "variant", vname); tr.insert(0, "symbol", lab); tr.insert(0, "group", g)
                    all_trades.append(tr)
            print(f"  {lab:6s} done ({len(t)} days, first signal day {first_ok})", flush=True)

    ps = pd.DataFrame(per_sym)
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    ev = pd.DataFrame(events)
    ps.to_csv(OUT / "per_symbol.csv", index=False)
    trades.to_csv(OUT / "trades.csv", index=False)
    ev.to_csv(OUT / "signal_events.csv", index=False)

    # ---------- group summaries
    lines = [f"# Backtest of signals.py rules, {START.date()} to {last_day.date()}", "",
             "Signal on day T close, trade at T+1 open; long only; no costs. "
             "Open positions marked at last close.", ""]
    summ = []
    for vname in VARIANTS:
        for g in list(GROUPS) + ["ALL"]:
            gl = list(GROUPS) if g == "ALL" else [g]
            sub = ps[(ps.get("variant") == vname) & ps["group"].isin(gl)]
            tsub = trades[(trades["variant"] == vname) & trades["group"].isin(gl)] if not trades.empty else None
            st = trade_stats(tsub)
            # equal-weight portfolio: 1/N sleeve per symbol, cash when flat
            sv = pd.DataFrame({k: v for gg in gl for k, v in daily_by.get((vname, gg), {}).items()}).fillna(0)
            bv = pd.DataFrame({k: v for gg in gl for k, v in daily_by.get(("B&H", gg), {}).items()}).fillna(0)
            pr, br = sv.mean(axis=1), bv.mean(axis=1)
            st.update(variant=vname, group=g, symbols=len(sub),
                      avg_sym_ret=sub["total_ret"].mean(), avg_bh_ret=sub["bh_ret"].mean(),
                      beat_bh=int((sub["total_ret"] > sub["bh_ret"]).sum()),
                      profitable_syms=int((sub["total_ret"] > 0).sum()),
                      port_ret=float((1 + pr).prod() - 1), port_dd=max_dd(pr.values), port_sharpe=sharpe(pr.values),
                      bh_port_ret=float((1 + br).prod() - 1), bh_port_dd=max_dd(br.values), bh_port_sharpe=sharpe(br.values),
                      exposure=sub["exposure"].mean())
            summ.append(st)
    sm = pd.DataFrame(summ)
    sm.to_csv(OUT / "group_summary.csv", index=False)

    for vname in VARIANTS:
        lines += [f"## Exit rule: {vname}", "",
                  "| Group | Syms | Trades | Win rate | Avg trade | Avg win | Avg loss | Profit factor | Avg hold (d) | EW port ret | EW port MaxDD | EW port Sharpe | EW B&H ret | EW B&H MaxDD | B&H Sharpe | Beat B&H | Exposure |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for _, r in sm[sm["variant"] == vname].iterrows():
            lines.append(f"| {r.group} | {r.symbols} | {r.trades} | {pct(r.win_rate,0)[1:] if pd.notna(r.win_rate) else 'n/a'} | "
                         f"{pct(r.avg_ret)} | {pct(r.avg_win)} | {pct(r.avg_loss)} | {r.profit_factor:.2f} | {r.avg_hold:.1f} | "
                         f"{pct(r.port_ret)} | {pct(r.port_dd)} | {r.port_sharpe:.2f} | {pct(r.bh_port_ret)} | {pct(r.bh_port_dd)} | "
                         f"{r.bh_port_sharpe:.2f} | {r.beat_bh}/{r.symbols} | {r.exposure*100:.0f}% |")
        lines.append("")

    if not ev.empty:
        lines += ["## Signal hit rates (first day of each episode; from next open)", "",
                  "| Signal | Group | Events | " + " | ".join(f"Hit {k}d" for k in FWD) + " | " + " | ".join(f"Avg {k}d" for k in FWD) + " |",
                  "|---|---|---|" + "---|" * (2 * len(FWD))]
        gmap = {LABEL.get(s, s): g for g, ss in GROUPS.items() for s in ss}
        ev["group"] = ev["symbol"].map(gmap)
        for kind in ("BUY", "SELL"):
            for g in list(GROUPS) + ["ALL"]:
                e = ev[(ev.kind == kind) & ((ev.group == g) | (g == "ALL"))]
                hits = []
                for k in FWD:
                    x = e[f"fwd{k}"].dropna()
                    h = (x > 0).mean() if kind == "BUY" else (x < 0).mean()
                    hits.append(f"{h*100:.0f}% (n={len(x)})" if len(x) else "n/a")
                avgs = [pct(e[f"fwd{k}"].mean()) for k in FWD]
                lines.append(f"| {kind} | {g} | {len(e)} | " + " | ".join(hits) + " | " + " | ".join(avgs) + " |")
        lines += ["", "BUY hit = price higher after N days; SELL hit = price lower after N days.", ""]

    lines += ["## Per symbol (exit: Sell-1 or Sell-2)", "",
              "| Group | Symbol | Trades | Win rate | Strategy ret | B&H ret | MaxDD | B&H MaxDD | Exposure | First signal day |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in ps[ps.get("variant") == "Sell-1 or Sell-2"].iterrows():
        wr = f"{r.win_rate*100:.0f}%" if pd.notna(r.win_rate) else "n/a"
        fs = r.first_signal_day.date() if pd.notna(r.first_signal_day) else "none"
        lines.append(f"| {r.group} | {r.symbol} | {int(r.trades)} | {wr} | {pct(r.total_ret)} | {pct(r.bh_ret)} | "
                     f"{pct(r.max_dd)} | {pct(r.bh_max_dd)} | {r.exposure*100:.0f}% | {fs} |")
    nodata = ps[ps.get("note").notna()] if "note" in ps else pd.DataFrame()
    if len(nodata):
        lines += ["", "No data: " + ", ".join(f"{r.symbol} ({r.note})" for _, r in nodata.iterrows())]
    (OUT / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
