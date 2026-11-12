#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""体检栏诚实性 —— 测「它会不会说好听的假话」。

⚠ 这个测试文件存在的唯一理由，是 2026-11-12 晚抓到的一次谎报：

    CI 日志        price_qfq ⇒ partial, last=2026-11-12, got=1, want=10
    页面显示      「价格（前复权）  2026-11-12 · 当日  ·  1/10」

    真实情况      510050 一只到了 11-12，其余 9 只全停在 11-11。
                  也就是 9 只停更，而体检栏报「落后 0 天」。

它比「全失败」危险得多：全失败至少显示 ❌，这个显示的是好听的假话。
用户对这件事的要求原本就说得很清楚 ——「他得告诉我停了，当日没有获取到」。

根因是取 last 用了 max()：只要有一只前进了，整项 lag 就归零。
**一批数据的新鲜度由最慢的那部分决定，不是最快的。**
"""
import importlib.util
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import _fh   # noqa: E402

_ok = _fail = 0


def chk(label, got, want):
    global _ok, _fail
    if got == want:
        _ok += 1
        print(f'  ✅ {label}')
    else:
        _fail += 1
        print(f'  ❌ {label}\n       得到 {got!r}  期望 {want!r}')


def load(path):
    spec = importlib.util.spec_from_file_location('m', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


print('\n=== 场景 1：★ 9 只停在 11-11、1 只到 11-12（就是那天真实的样子）===')
# 这是本文件的核心用例。旧写法 max() 会返回 11-12 → lag=0 → 报「当日」。
series = [[['2026-11-10', 1.0], ['2026-11-11', 1.0]]] * 9 \
       + [[['2026-11-10', 1.0], ['2026-11-11', 1.0], ['2026-11-12', 1.0]]]
last, ahead, behind, total = _fh.majority_last(series)
chk('多数所在日期是 11-11（不是被一只拉到的 11-12）', last, '2026-11-11')
chk('领先的只有 1 只', ahead, 1)
chk('没有更旧的', behind, 0)
chk('总数 10', total, 10)

print('\n=== 场景 1b：★★ 5 只到 11-12、5 只停 11-11（我第一版修法在这里失效）===')
# 第一版写的是「从新到旧找，第一个覆盖 >= 半数的日期」——
# 平局时先碰到 11-12（5*2>=10 成立）→ 报 11-12 → lag 又是 0。
# **一半数据停更，体检栏却说「当日」。** 这是跑真实数据才发现的，不是想出来的。
half = [[['2026-11-11', 1.0], ['2026-11-12', 1.0]]] * 5 \
     + [[['2026-11-11', 1.0]]] * 5
last_h, ahead_h, behind_h, total_h = _fh.majority_last(half)
chk('★ 平局时不能报更晚的那天', last_h, '2026-11-11')
chk('★ 平局时如实报「5 只仍在更早日期」', behind_h, 0)
chk('★ 平局时领先只数是 5', ahead_h, 5)

print('\n=== 场景 2：全部同步到 11-12 → 不该出现「领先几只」的噪音 ===')
series = [[['2026-11-11', 1.0], ['2026-11-12', 1.0]]] * 10
last, ahead, behind, total = _fh.majority_last(series)
chk('多数日期 11-12', last, '2026-11-12')
chk('没有领先资产', ahead, 0)
chk('没有落后资产', behind, 0)

print('\n=== 场景 3：8 只到 11-12、2 只停在 11-11 → 必须报出那 2 只 ===')
series = [[['2026-11-12', 1.0]]] * 8 + [[['2026-11-11', 1.0]]] * 2
last, ahead, behind, total = _fh.majority_last(series)
chk('中位数落在 11-12', last, '2026-11-12')
chk('★ 如实报 2 只仍在更早日期', behind, 2)

print('\n=== 场景 4：个别标的停牌/上市晚，天生更旧 → 不能被它拖成陈旧 ===')
# 8 只到 11-12、2 只停在 10-20（停牌）。取 min 会让整项显示「落后 20 天」。
series = [[['2026-11-12', 1.0]]] * 8 + [[['2026-10-20', 1.0]]] * 2
last, ahead, behind, total = _fh.majority_last(series)
chk('中位数仍是 11-12（不被停牌的两只拖下去）', last, '2026-11-12')
chk('但如实报出那 2 只', behind, 2)

print('\n=== 场景 5：空数据 / 全空序列 → 不崩，返回 None ===')
chk('空列表', _fh.majority_last([]), (None, 0, 0, 0))
chk('全是空序列', _fh.majority_last([[], []]), (None, 0, 0, 0))

print('\n=== 场景 6：单只资产 → 就是它自己 ===')
chk('单只', _fh.majority_last([[['2026-11-12', 1.0]]])[0], '2026-11-12')

print('\n=== 场景 6b：中位数不会高估新鲜度（性质检查）===')
# 随机分布下，「报出的日期」必须满足「至少一半资产不比它更新」。
# 这是中位数的定义性质，也就是这条修复的核心保证。
import random
random.seed(7)
bad = 0
for _ in range(300):
    n = random.randint(1, 12)
    pool = ['2026-11-08', '2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12']
    ser = [[[random.choice(pool), 1.0]] for _ in range(n)]
    d, a, b, t = _fh.majority_last(ser)
    # 「不比 d 更新」的只数 = 总数 - 领先只数，必须 >= 一半
    if (t - a) * 2 < t:
        bad += 1
chk('★ 300 次随机分布都满足「至少一半不比报出的日期更新」', bad, 0)

print('\n=== 场景 7：★ 回归检查 —— 不许再用 max() 取 last ===')
# 只查代码行，剥掉注释。理由和 test_time_axis.py 一样：
# 注释里写的「原来用的是 max()」不该被算成复发，否则测试会奖励「把解释删掉」。
def code_lines(src):
    out = []
    for line in src.split('\n'):
        s = line.split('#')[0].rstrip()
        if s.strip():
            out.append(s)
    return out


for fn in ('fetch_history.py', 'fetch_price_bfq.py'):
    p = os.path.join(BASE, 'scripts', fn)
    body = '\n'.join(code_lines(open(p, encoding='utf-8').read()))
    # got_last / _last 的赋值行里不该再出现 max(
    bad = [l for l in body.split('\n')
           if ('got_last' in l or '_last' in l) and 'max(' in l and '=' in l
           and 'majority_last' not in l]
    chk(f'{fn} 不再用 max() 给 last 赋值', bad, [])
    chk(f'{fn} 用 majority_last', 'majority_last' in body, True)

print('\n=== 场景 8：compute_demo 不得用容器时区的 date.today() ===')
p = os.path.join(BASE, 'scripts', 'compute_demo.py')
body = '\n'.join(code_lines(open(p, encoding='utf-8').read()))
chk('compute_demo.py 不用 date.today()', 'date.today()' in body, False)
chk('compute_demo.py 用 _td.bj_today_str()', 'bj_today_str' in body, True)

print('\n' + '=' * 50)
print(f'通过 {_ok} 项，失败 {_fail} 项')
sys.exit(0 if _fail == 0 else 1)
