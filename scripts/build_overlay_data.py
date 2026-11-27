#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成站点「历史叠加」页所需的静态数据 data/overlay.json。

口径：
  · 图一 纳指：FRED NASDAQCOM；实线 2022-10-12 起补到最新可用日，
    虚线 1994-10-03 起（匹配窗 1994-10-03..1998-05-13，之后为历史后续，平移 +28 年）。
  · 图二 A 股：上证指数 sh000001（AkShare/新浪）作万得全 A 的公开代理；
    事件日 1999-05-19 / 2024-09-24，按交易日对齐。

用法: python3 scripts/build_overlay_data.py
"""
from __future__ import annotations

import io
import json
import os
import requests
import pandas as pd
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data", "overlay.json")
FETCH = "2026-11-25"


def zdist(a: np.ndarray, b: np.ndarray) -> float:
    za, zb = (a - a.mean()) / a.std(), (b - b.mean()) / b.std()
    return float(np.linalg.norm(za - zb) / np.sqrt(len(a)))


def nasdaq() -> dict:
    url = ("https://fred.stlouisfed.org/graph/fredgraph.csv"
           "?id=NASDAQCOM&cosd=1994-01-01&coed=2026-11-24")
    df = pd.read_csv(io.StringIO(requests.get(url, timeout=30).text))
    df["observation_date"] = pd.to_datetime(df["observation_date"])
    s = df.dropna().set_index("observation_date")["NASDAQCOM"]

    blue = s.loc["2022-10-12":"2026-11-24"]
    match = s.loc["1994-10-03":"1998-05-13"]
    hist = s.loc["1994-10-03":"2001-10-19"]
    n = len(match)
    lb = np.log(blue.iloc[:n].to_numpy() / blue.iloc[0])
    lo = np.log(match.to_numpy() / match.iloc[0])

    bo = blue.iloc[n:].to_numpy()
    oo = hist.iloc[n:n + len(bo)].to_numpy()
    lob, loo = np.log(bo / bo[0]), np.log(oo / oo[0])
    div = np.abs(lob - loo)
    k = int(np.argmax(div))

    return {
        "idx": list(range(len(hist))),
        "blue": [round(float(x / blue.iloc[0]), 4) for x in blue.to_numpy()]
                + [None] * (len(hist) - len(blue)),
        "orange": [round(float(x / hist.iloc[0]), 4) for x in hist.to_numpy()],
        "bottom": [d.strftime("%Y-%m-%d") for d in hist.index],
        "top": [f"{d.year + 28}-{d.month:02d}-{d.day:02d}" for d in hist.index],
        "split": len(blue) - 1,
        "metrics": {
            "blueEnd": blue.index[-1].strftime("%Y-%m-%d"),
            "lastFred": s.index[-1].strftime("%Y-%m-%d"),
            "n": n,
            "corr": round(float(np.corrcoef(lb, lo)[0, 1]), 4),
            "zdist": round(zdist(lb, lo), 4),
            "oosN": len(bo),
            "oosCorr": round(float(np.corrcoef(lob, loo)[0, 1]), 4),
            "oosZdist": round(zdist(lob, loo), 4),
            "blueDd": round(float(bo[-1] / bo[0] - 1) * 100, 2),
            "orangeDd": round(float(oo[-1] / oo[0] - 1) * 100, 2),
            "blueRatio": round(float(bo[-1] / blue.iloc[0]), 4),
            "orangeRatio": round(float(oo[-1] / hist.iloc[0]), 4),
            "maxDivDate": blue.index[n + k].strftime("%Y-%m-%d"),
            "maxDivOrangeDate": hist.index[n + k].strftime("%Y-%m-%d"),
            "maxDiv": round(float(div[k]), 3),
        },
        "oos": {
            "i": list(range(len(bo))),
            "blue": np.round((bo / bo[0] - 1) * 100, 2).tolist(),
            "orange": np.round((oo / oo[0] - 1) * 100, 2).tolist(),
            "blueDate": [d.strftime("%Y-%m-%d") for d in blue.index[n:]],
            "orangeDate": [d.strftime("%Y-%m-%d") for d in hist.index[n:n + len(bo)]],
        },
    }


def ashare() -> dict:
    import akshare as ak
    df = ak.index_zh_daily_sina(symbol="sh000001")
    df["date"] = pd.to_datetime(df["date"])
    s = df.set_index("date").sort_index()["close"].astype(float)

    def window(event: str, before=280, after=1040, end=None):
        ss = s if end is None else s.loc[:end]
        pos = ss.index.get_loc(pd.Timestamp(event))
        lo, hi = max(0, pos - before), min(len(ss), pos + after + 1)
        w = ss.iloc[lo:hi]
        rel = list(range(lo - pos, hi - pos))
        return rel, np.asarray((w / ss.loc[event] - 1) * 100)

    t99, r99 = window("1999-05-19")
    t24, r24 = window("2024-09-24", end="2026-11-24")
    n = min(len(r99), len(r24))
    a, b = r99[:n], r24[:n]
    ret_corr = float(np.corrcoef(np.diff(a), np.diff(b))[0, 1])

    def at(t):
        return round(float(r99[t - t99[0]]), 1), round(float(r24[t - t24[0]]), 1)

    return {
        "t99": t99, "r99": np.round(r99, 2).tolist(),
        "t24": t24, "r24": np.round(r24, 2).tolist(),
        "metrics": {
            "n": n,
            "corr": round(float(np.corrcoef(a, b)[0, 1]), 4),
            "zdist": round(zdist(a, b), 4),
            "mae": round(float(np.mean(np.abs(a - b))), 2),
            "retCorr": round(ret_corr, 4),
            "peak99": round(float(r99.max()), 1),
            "peak99T": int(t99[int(r99.argmax())]),
            "peak24": round(float(r24.max()), 1),
            "peak24T": int(t24[int(r24.argmax())]),
            "at200": at(200), "at400": at(400), "at523": at(523),
            "lastDate": s.index[-1].strftime("%Y-%m-%d"),
            "lastClose": round(float(s.iloc[-1]), 2),
        },
    }


def main():
    data = {"fetchDate": FETCH, "nasdaq": nasdaq(), "ashare": ashare()}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {OUT} ({os.path.getsize(OUT)/1024:.0f} KB)")
    print("nasdaq:", data["nasdaq"]["metrics"])
    print("ashare:", data["ashare"]["metrics"])


if __name__ == "__main__":
    main()
