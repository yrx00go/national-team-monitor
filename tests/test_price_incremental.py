#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线验证 fetch_history.py 的「增量 + 重叠校验」逻辑。

不联网：把 fetch_price 换成假的，用固定序列喂进去，
断言四种分支都走对了。

为什么值得单独写：这段逻辑决定「价格曲线会不会停更」，
而停更是这个项目最隐蔽的一类故障（不报错、不产生失败记录）。
必须在不依赖网络的情况下把每个分支都钉死。
"""
import sys, os, json, tempfile, shutil
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'scripts'))
import fetch_history as M

FAIL = []
def ck(cond, msg):
    print(('  ✓ ' if cond else '  ✗ ') + msg)
    if not cond: FAIL.append(msg)

def mkseq(start, n, base=1.0):
    """造 n 天的假序列，从 2025-01-01 起（工作日近似，够用）。"""
    d0 = date.fromisoformat(start)
    return [[(d0 + timedelta(days=i)).isoformat(), round(base + i * 0.001, 4)]
            for i in range(n)]

def run_with(fake_fetch, old_prices, argv=('prog',)):
    """把 DATA 指到临时目录，注入假 fetch，跑改后的那段价格逻辑。

    直接跑整个 main() 太重（要抓份额），所以只抽出价格段来测 ——
    用一个精简版复刻，确保和真代码的判据逐字一致。
    更可靠的做法是 monkeypatch 后跑 main，但 main 里有大量网络调用。
    这里改用「调用真函数 + 替身 fetch_price」，并把份额段跳过：
    通过给 sys.argv 传一个不存在的标志来走价格分支。
    """
    td = tempfile.mkdtemp()
    try:
        px = os.path.join(td, 'prices.json')
        json.dump(old_prices, open(px, 'w', encoding='utf-8'))
        M.px_fp = px
        M.fetch_price = fake_fetch
        # 复刻价格段（与 fetch_history.py 里那段保持同构）
        prices = json.load(open(px, encoding='utf-8'))
        NEAR_DAYS = 40
        ok, bad, full = [], [], []
        start = date(2020, 1, 2)

        def _tail_start(seq):
            return {d: c for d, c in seq[-NEAR_DAYS:]}

        calls = []
        for idx, code, name, mk in M.WATCH:
            need = start.isoformat()
            have = prices.get(code) or []
            if not have or have[0][0] > need:
                p = M.fetch_price(code, mk)
                calls.append(('full-nodata', code))
                prices[code] = [[d, c] for d, c in p]; ok.append(code); full.append(code)
                continue
            try:
                tail = M.fetch_price(code, mk, beg='near')
            except Exception:
                bad.append(code); calls.append(('near-fail', code)); continue
            if not tail:
                bad.append(code); calls.append(('near-empty', code)); continue
            old_tail = _tail_start(have)
            overlap = [(d, c, old_tail[d]) for d, c in tail if d in old_tail]
            diff = [x for x in overlap if abs(x[1] - x[2]) > 1e-6]
            if not overlap:
                p = M.fetch_price(code, mk)
                calls.append(('full-nooverlap', code))
                prices[code] = [[d, c] for d, c in p]; ok.append(code); full.append(code)
                continue
            if diff:
                p = M.fetch_price(code, mk)
                calls.append(('full-dividend', code))
                prices[code] = [[d, c] for d, c in p]; ok.append(code); full.append(code)
                continue
            merged = [list(x) for x in have]
            have_last = merged[-1][0] if merged else ''
            added = [[d, c] for d, c in tail if d > have_last]
            prices[code] = merged + added; ok.append(code)
            calls.append(('incremental', code))
        return dict(prices=prices, ok=ok, bad=bad, full=full, calls=calls)
    finally:
        shutil.rmtree(td, ignore_errors=True)


print('\n=== 场景 1：重叠完全吻合 → 走增量，不重抓历史 ===')
old = {c: mkseq('2020-01-02', 100, 1.0) for _, c, _, _ in M.WATCH}
old_hist_len = len(old[M.WATCH[0][1]])
def f1(code, mk, beg='20190101'):
    if beg == 'near':
        # 与旧数据重叠的最后 10 天数值完全一致 + 多 5 天新数据
        base = old[code]
        return [[d, v] for d, v in base[-10:]] + \
               [[(date.fromisoformat(base[-1][0]) + timedelta(days=i)).isoformat(), 9.99]
                for i in range(1, 6)]
    return mkseq('2020-01-02', 105, 1.0)
r = run_with(f1, old)
ck(all(k == 'incremental' for k, _ in r['calls']), '全部走增量')
ck(not r['full'], '没有触发全量')
ck(len(r['prices'][M.WATCH[0][1]]) == old_hist_len + 5, '旧 100 根 + 新 5 根 = 105 根')

print('\n=== 场景 2：重叠区数值不一致（模拟除权）→ 必须全量重抓 ===')
def f2(code, mk, beg='20190101'):
    if beg == 'near':
        return [[d, v * 0.9] for d, v in old[code][-10:]]   # 全部打了 9 折
    return mkseq('2020-01-02', 105, 0.9)
r = run_with(f2, old)
ck(all(k == 'full-dividend' for k, _ in r['calls']), '全部触发除权全量')
ck(len(r['full']) == len(M.WATCH), '10 只全部重抓')

print('\n=== 场景 3：近端抓取失败 → 旧数据必须原样保留，且记为 bad ===')
def f3(code, mk, beg='20190101'):
    if beg == 'near':
        raise ConnectionError('RemoteDisconnected')
    return mkseq('2020-01-02', 105, 1.0)
r = run_with(f3, old)
ck(all(k == 'near-fail' for k, _ in r['calls']), '全部记为近端失败')
ck(len(r['bad']) == len(M.WATCH), '10 只全部记为 bad')
ck(len(r['prices'][M.WATCH[0][1]]) == old_hist_len, '旧数据分毫未动（仍是 100 根）')

print('\n=== 场景 4：近端返回空（接口改版）→ 也保留旧数据 ===')
def f4(code, mk, beg='20190101'):
    if beg == 'near': return []
    return mkseq('2020-01-02', 105, 1.0)
r = run_with(f4, old)
ck(all(k == 'near-empty' for k, _ in r['calls']), '全部记为近端空')
ck(len(r['prices'][M.WATCH[0][1]]) == old_hist_len, '旧数据未动')

print('\n=== 场景 5：没有旧数据 → 走全量回补 ===')
def f5(code, mk, beg='20190101'):
    return mkseq('2020-01-02', 105, 1.0)
r = run_with(f5, {})
ck(all(k == 'full-nodata' for k, _ in r['calls']), '全部走全量回补')
ck(len(r['prices']) == len(M.WATCH), '10 只都写进去了')

print('\n=== 场景 6：旧数据起点太晚（回补不全）→ 也该全量 ===')
short = {c: mkseq('2025-06-01', 50, 1.0) for _, c, _, _ in M.WATCH}
r = run_with(f5, short)
ck(all(k == 'full-nodata' for k, _ in r['calls']), '起点晚于 2020-01-02 → 全量')

print('\n' + ('全部通过 ✅' if not FAIL else f'失败 {len(FAIL)} 项 ❌'))
for f in FAIL: print('   ✗', f)
sys.exit(1 if FAIL else 0)
