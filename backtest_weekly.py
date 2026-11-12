#!/usr/bin/env python3
"""
Backtest: long while weekly SKDJ K > D, flat while K <= D.  2026-01-01 -> latest day.

Same symbols, data, indicator code (signals.py) and fills as backtest.py:
signal on day T's close -> trade at T+1's open, long only, no costs.

Two ways of checking the weekly condition:
  A. "Daily check": every day, using the in-progress weekly candle (what the
     daily scanner shows each morning).
  B. "Friday check": only on the last trading day of each week (completed
     weekly candle), trade at the next week's first open.
Writes backtest_results/weekly_skdj/.
"""
import numpy as np
import pandas as pd

import signals as S
import backtest as B

OUT = B.OUT / "weekly_skdj"


def weekly_kd_table(raw, last_day):
    d = raw.copy()
    idx = pd.to_datetime(d.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    d.index = idx.normalize()
    d = d[d.index <= last_day].sort_index()
    d = d[~d.index.duplicated(keep="last")]
    rows = []
    for T in d.index[d.index >= B.START]:
        w = S.to_weekly(d.loc[:T])
        k, dd = S.skdj(w)
        ok = len(w) >= S.MIN_WEEKS and pd.notna(k.iloc[-1]) and pd.notna(dd.iloc[-1])
        rows.append({"date": T, "open": d.at[T, "Open"], "close": d.at[T, "Close"],
                     "ok": bool(ok), "kd": bool(ok and k.iloc[-1] > dd.iloc[-1])})
    t = pd.DataFrame(rows).set_index("date")
    wk = t.index.to_period("W-FRI")
    t["week_end"] = np.r_[wk[:-1] != wk[1:], True]
    return t


def run_variant(t, mode):
    x = t.copy()
    gate = x["week_end"] if mode == "Friday check" else True
    x["buy"] = x["kd"] & gate
    x["x"] = (x["ok"] & ~x["kd"]) & gate
    return B.simulate(x, ("x",))


def main():
    tickers = [s for g in B.GROUPS.values() for s in g]
    frames = S.download_all(tickers)
    last_day = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(days=1)
    OUT.mkdir(parents=True, exist_ok=True)
    modes = ("Daily check", "Friday check")
    per, trades, daily_by, events = [], [], {}, []
    for g, syms in B.GROUPS.items():
        for s in syms:
            lab = B.LABEL.get(s, s)
            if s not in frames:
                per.append({"group": g, "symbol": lab, "note": "no data"}); continue
            t = weekly_kd_table(frames[s], last_day)
            bh_daily = np.r_[t["close"].iloc[0] / t["open"].iloc[0] - 1, t["close"].pct_change().values[1:]]
            daily_by.setdefault(("B&H", g), {})[lab] = pd.Series(bh_daily, index=t.index)
            for m in modes:
                tr, dret, inpos = run_variant(t, m)
                st = B.trade_stats(tr)
                st.update(group=g, symbol=lab, mode=m, total_ret=float((1 + dret).prod() - 1),
                          bh_ret=float(t["close"].iloc[-1] / t["open"].iloc[0] - 1),
                          max_dd=B.max_dd(dret.values), bh_max_dd=B.max_dd(bh_daily),
                          sharpe=B.sharpe(dret.values), exposure=float(inpos.mean()))
                per.append(st)
                daily_by.setdefault((m, g), {})[lab] = dret
                if not tr.empty:
                    tr.insert(0, "mode", m); tr.insert(0, "symbol", lab); tr.insert(0, "group", g)
                    trades.append(tr)
                # forward returns after each entry signal (first day of K>D episode)
                sig = (t["kd"] & (t["week_end"] if m == "Friday check" else True)).values
                prev = np.r_[False, (t["kd"].values if m == "Daily check" else np.zeros(len(t), bool))[:-1]]
                if m == "Friday check":   # a new episode = K>D at this week end but not at the previous week end
                    we = np.where(t["week_end"].values)[0]
                    starts = [i for n, i in enumerate(we) if t["kd"].values[i] and (n == 0 or not t["kd"].values[we[n - 1]])]
                else:
                    starts = list(np.where(sig & ~prev)[0])
                o, c = t["open"].values, t["close"].values
                for i in starts:
                    if i + 1 >= len(t):
                        continue
                    r = {"group": g, "symbol": lab, "mode": m, "date": t.index[i].date()}
                    for k in B.FWD:
                        r[f"fwd{k}"] = c[i + k] / o[i + 1] - 1 if i + k < len(t) else np.nan
                    events.append(r)
            print(f"  {lab} done", flush=True)

    ps = pd.DataFrame(per); tr = pd.concat(trades, ignore_index=True); ev = pd.DataFrame(events)
    ps.to_csv(OUT / "per_symbol.csv", index=False); tr.to_csv(OUT / "trades.csv", index=False)
    ev.to_csv(OUT / "signal_events.csv", index=False)

    pct = B.pct
    L = [f"# Weekly SKDJ K>D backtest, {B.START.date()} to {last_day.date()}", "",
         "Long while weekly K > D, flat otherwise. Signal on day T close, trade at T+1 open; no costs.", ""]
    rows = []
    for m in modes:
        L += [f"## {m}", "",
              "| Group | Syms | Trades | Win rate | Avg trade | Median | Avg win | Avg loss | Profit factor | Avg hold (d) | EW port ret | EW MaxDD | EW Sharpe | EW B&H ret | B&H MaxDD | B&H Sharpe | Beat B&H | Exposure |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for g in list(B.GROUPS) + ["ALL"]:
            gl = list(B.GROUPS) if g == "ALL" else [g]
            sub = ps[(ps.get("mode") == m) & ps["group"].isin(gl)]
            st = B.trade_stats(tr[(tr["mode"] == m) & tr["group"].isin(gl)])
            sv = pd.DataFrame({k: v for gg in gl for k, v in daily_by.get((m, gg), {}).items()}).fillna(0).mean(axis=1)
            bv = pd.DataFrame({k: v for gg in gl for k, v in daily_by.get(("B&H", gg), {}).items()}).fillna(0).mean(axis=1)
            st.update(mode=m, group=g, syms=len(sub), port=float((1 + sv).prod() - 1), dd=B.max_dd(sv.values),
                      sh=B.sharpe(sv.values), bh=float((1 + bv).prod() - 1), bdd=B.max_dd(bv.values),
                      bsh=B.sharpe(bv.values), beat=int((sub["total_ret"] > sub["bh_ret"]).sum()),
                      expo=sub["exposure"].mean())
            rows.append(st)
            L.append(f"| {g} | {len(sub)} | {st['trades']} | {st['win_rate']*100:.0f}% | {pct(st['avg_ret'])} | {pct(st['median_ret'])} | "
                     f"{pct(st['avg_win'])} | {pct(st['avg_loss'])} | {st['profit_factor']:.2f} | {st['avg_hold']:.1f} | "
                     f"{pct(st['port'])} | {pct(st['dd'])} | {st['sh']:.2f} | {pct(st['bh'])} | {pct(st['bdd'])} | {st['bsh']:.2f} | "
                     f"{st['beat']}/{len(sub)} | {st['expo']*100:.0f}% |")
        L.append("")
    pd.DataFrame(rows).to_csv(OUT / "group_summary.csv", index=False)

    L += ["## Entry-signal hit rates (price higher N trading days after the K>D turn, from next open)", "",
          "| Mode | Group | Events | " + " | ".join(f"Hit {k}d" for k in B.FWD) + " | " + " | ".join(f"Avg {k}d" for k in B.FWD) + " |",
          "|---|---|---|" + "---|" * (2 * len(B.FWD))]
    for m in modes:
        for g in list(B.GROUPS) + ["ALL"]:
            e = ev[(ev["mode"] == m) & ((ev["group"] == g) | (g == "ALL"))]
            hits = [f"{(e[f'fwd{k}'].dropna() > 0).mean()*100:.0f}% (n={e[f'fwd{k}'].notna().sum()})" for k in B.FWD]
            L.append(f"| {m} | {g} | {len(e)} | " + " | ".join(hits) + " | " + " | ".join(pct(e[f'fwd{k}'].mean()) for k in B.FWD) + " |")
    L += ["", "## Per symbol", "",
          "| Group | Symbol | Daily: trades | Daily: win | Daily: ret | Daily: MaxDD | Friday: trades | Friday: win | Friday: ret | Friday: MaxDD | B&H ret | B&H MaxDD |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for (g, s), grp in ps[ps.get("mode").notna()].groupby(["group", "symbol"], sort=False):
        a = grp[grp["mode"] == "Daily check"].iloc[0]; b = grp[grp["mode"] == "Friday check"].iloc[0]
        wr = lambda x: f"{x*100:.0f}%" if pd.notna(x) else "n/a"
        L.append(f"| {g} | {s} | {int(a.trades)} | {wr(a.win_rate)} | {pct(a.total_ret)} | {pct(a.max_dd)} | "
                 f"{int(b.trades)} | {wr(b.win_rate)} | {pct(b.total_ret)} | {pct(b.max_dd)} | {pct(a.bh_ret)} | {pct(a.bh_max_dd)} |")
    (OUT / "summary.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
