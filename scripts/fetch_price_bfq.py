#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补抓【不复权】日线，用于计算净流入。

为什么必须用不复权价：
    净流入 = 份额差 × 当日真实成交价。ETF 分红除权那天，真实成交价会掉一个分红额，
    这时「份额不变、价格下跌」不代表亏损，而是分红落到了持有人账上。
    用前复权价算净流入是正确方向（消除了分红断层），
    但前复权价在历史区间上会被系统性压低（实测 2024-06-03：上证50 −5.4%、
    红利 −8.8%、中证红利 −10.9%），导致早期净流入被低估。
    正确做法：净流入一律用不复权（当日真实价），画价格曲线一律用前复权（趋势连续）。

输出：data/prices_bfq.json  {code: [[date, close], ...]}
"""
import json, os, sys, time, urllib.request
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')

# 抓取起始日。真正决定页面区间的是 compute_demo.py 的 START，
# 这里留出余量早抓几天，免得 START 前移后要重抓。
BEG = '20190101'

CODES = {
    '510050': 'sh', '510300': 'sh', '510500': 'sh', '512100': 'sh',
    '588000': 'sh', '510880': 'sh', '515080': 'sh',
    '159915': 'sz', '159545': 'sz', '159919': 'sz',
}


def _get(url, referer='https://gu.qq.com/', tries=4):
    """带退避重试的 GET。

    ⚠ 2026-11-12 在 GitHub Actions 上实测出来的问题：
       10 只标的里 4 只（588000 / 510880 / 515080 / 159919）报
       `RemoteDisconnected: Remote end closed connection without response`，
       而上交所份额、两融余额同一台 runner 上全都成功。
       说明不是「美国 IP 被整体封」，而是东财 push2his 对【境外 IP + 高频连打】
       做了间歇性掐断 —— 典型瞬时错误，等一下再问就通了。

       原来只请求一次，撞上就整只标的作废，于是 compute_demo 因为
       缺 510880 直接 IndexError（两个 bug 串成一条链）。

       重试间隔 0.8s / 2.0s / 4.0s，四次都失败才算真拿不到。
       对交易所正常情况没有副作用：成功时只请求一次，一秒都不多等。
    """
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                'Referer': referer,
            })
            return urllib.request.urlopen(req, timeout=30).read()
        except Exception as e:                     # 网络类异常一律重试
            last = e
            if i < tries - 1:
                time.sleep((0.8, 2.0, 4.0)[min(i, 2)])
    raise last


def fetch(code, market, bars=900):
    """⚠ 这里曾经用腾讯接口，但腾讯硬上限 640 根（实测 2500 根直接返回空），
    只能回溯到 2024-03 左右，无法支撑 2020 年起的区间。
    改用东方财富 push2his，可回溯到上市首日，且【前复权序列与腾讯逐日吻合】
    （实测 510880 重叠 751 天，中位差 0.0000%，最大差 0.177%），换源不会改变已有结论。

    fqt=0 = 不复权（这里要的）；fqt=1 = 前复权。
    """
    secid = ('1.' if market == 'sh' else '0.') + code
    url = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?'
           f'secid={secid}&fields1=f1,f2&fields2=f51,f53&klt=101&fqt=0'
           f'&beg={BEG}&end={date.today().strftime("%Y%m%d")}')
    js = json.loads(_get(url, 'https://quote.eastmoney.com/').decode('utf-8', 'ignore'))
    kl = (js.get('data') or {}).get('klines') or []
    return [(s.split(',')[0], float(s.split(',')[1])) for s in kl]


def _save(fp, obj):
    """原子写盘：先写 .tmp 再 os.replace。

    直接 open(fp,'w') 写，如果中途异常（磁盘满、进程被杀、JSON 序列化炸），
    会留下一个【半截文件】。下游 compute_demo.py 读到坏 JSON 直接崩，
    而它是可选步骤 —— 崩了页面就沿用旧数据，表面一切正常。
    临时文件 + replace 是原子操作，要么旧的完整、要么新的完整，没有中间态。
    """
    tmp = fp + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, fp)


def main():
    fp = os.path.join(DATA, 'prices_bfq.json')

    # ⚠⚠ 2026-11-12 第二次 CI（run 43182246241）炸出来的最严重 bug，记录在这里：
    #   那一次东财全站掐断，10 只标的全部 RemoteDisconnected，
    #   而这几行代码【无条件】json.dump(out, ...)，于是：
    #       写入 data/prices_bfq.json  (0 KB)  成功 0/10
    #   把仓库里原本 227 KB 的有效数据【覆盖成了空文件】，再由 CI 的 git commit
    #   提交回仓库 → 线上页面「组合单日净流入 +0.00 亿 / 近5日 +0.0 亿」全部归零。
    #
    #   比崩溃更糟：崩溃至少能看见，写空是静默的，日志里只有一行 "0 KB"，
    #   页面长得跟正常一样，只是数字全变成 0。
    #   上一轮我加「判空兜底」正是为了不让 compute_demo.py 崩 —— 结果把崩溃
    #   换成了静默写空，等于把响警报拆了却没修火。
    #
    #   现在的三条规矩：
    #     ① 全失败（一只都没抓到）→ 一个字都不写，直接 return 2，旧文件原样保留
    #     ② 部分成功 → 【合并】进旧数据，不能整体替换。否则某只标的瞬时故障，
    #        它那一段历史就被清空了，净流入从该日起全变 0
    #     ③ 抓到的写入 + 旧的里没抓到的保留，两边都以「日期」为准去重，取新的
    old = {}
    if os.path.exists(fp):
        try:
            old = json.load(open(fp, encoding='utf-8')) or {}
        except Exception as e:
            print(f'  ⚠ 旧文件读不出来（{type(e).__name__}: {e}），按空处理')
            old = {}

    out, bad = {}, []
    for code, mk in CODES.items():
        try:
            arr = fetch(code, mk)
            if not arr:
                bad.append((code, '空返回'))
                continue
            out[code] = arr
            print(f'  {code}  {len(arr):>4} 根  {arr[0][0]} → {arr[-1][0]}  末价 {arr[-1][1]:.3f}')
        except Exception as e:
            bad.append((code, f'{type(e).__name__}: {e}'))
        time.sleep(0.4)

    if bad:
        print(f'\n失败 {len(bad)} 只：')
        for c, r in bad:
            print(f'  {c}  {r}')

    # ── ① 全失败：不写盘 ───────────────────────────────────────────────
    if not out:
        kept = sum(len(v) for v in old.values())
        print(f'\n✗ 一只都没抓到，不写盘。旧文件保留 {len(old)} 只 / {kept} 根 '
              f'({os.path.getsize(fp)/1024:.0f} KB)' if os.path.exists(fp) else
              '\n✗ 一只都没抓到，且本地无旧文件，本次不产出 prices_bfq.json')
        return 2

    # ── ② 合并：抓到的覆盖旧的，没抓到的沿用旧数据 ─────────────────────
    merged, filled = {}, []
    for code in CODES:
        if code in out:
            merged[code] = out[code]
        elif code in old and old[code]:
            merged[code] = old[code]
            filled.append(code)

    _save(fp, merged)

    print(f'\n写入 {fp}  ({os.path.getsize(fp)/1024:.0f} KB)  '
          f'抓到 {len(out)}/{len(CODES)}')
    if filled:
        # 不是错误，但要显式说出来：这几只的末价是旧的，
        # 净流入会按「旧价 × 新份额」算 —— 误差只在该标的当日涨跌里，比归零好得多。
        print(f'  沿用旧数据 {len(filled)} 只：{" ".join(filled)}')

    # ③ 部分成功也返回 1：让 Actions 标红，人能看到「今天没抓全」。
    #    但数据是对的（合并后的），页面能正常用。
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
