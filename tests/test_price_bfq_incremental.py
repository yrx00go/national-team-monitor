#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""不复权价的增量合并（离线，不联网）。

对应 2026-11-12 晚的改动：fetch_price_bfq 从「每天全量 1906 根」改成「增量 ≈47 根」。
省下的是 10 只 × 1900 根的响应体，也是把自己 IP 打黑的主要来源。

⚠ 为什么不复权能直接追加、前复权不能：
   不复权 = 当日真实成交价。分红除权那天价格会掉一个分红额，
   但**那是真实发生的价格变化，不是数据错误** —— 历史值不会被改写。

   前复权 = 以今天为基准往回折算。**一次分红，整段历史全变**。
   所以前复权必须做重叠校验（对不上就全量重抓），见 test_price_incremental.py。
   两套价格、两种增量策略，不能混用。

跑法：python3 tests/test_price_bfq_incremental.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'scripts'))

import fetch_price_bfq as m

ok_n = fail_n = 0


def chk(label, got, want):
    global ok_n, fail_n
    if got == want:
        ok_n += 1
        print(f'  ✅ {label}: {got!r}')
    else:
        fail_n += 1
        print(f'  ❌ {label}: 得到 {got!r}，应为 {want!r}')


print('\n## need_full：什么时候必须全量')

chk('没有历史 → 全量', m.need_full([]), True)
chk('历史从 2020-01-02 起 → 增量', m.need_full([['2020-01-02', 1.0]]), False)
chk('历史从 2019-01-02 起（更早）→ 增量', m.need_full([['2019-01-02', 1.0]]), False)
chk('历史从 2020-06-01 起（太晚）→ 全量', m.need_full([['2020-06-01', 1.0]]), True)
chk('历史正好从 2020-01-03（晚一天）→ 全量', m.need_full([['2020-01-03', 1.0]]), True)

print('\n## ★★ need_full：起点晚 ≠ 数据缺（2026-11-12 第十轮 CI 的真 bug）')
#
#   ⚠ 上面那组断言全都是对的，但它们【掩盖】了一个更要紧的问题：
#     「起点晚于 2020-01-02 → 全量」这条判据，对**上市晚的标的**是死循环。
#
#     588000（科创50ETF）2020-11-16 上市   → 起点永远晚于 2020-01-02
#     159545（中证红利低波）2024-04-15 上市 → 起点永远晚于 2020-01-02
#
#     于是这两只**每天**被判成「需要全量」→ 每天重抓 ~1900 根 →
#     每天失败 → 体检栏天天写「2 只未获取」。
#     一条永远亮的琥珀色提示 = 没人再看 = 等于没装（7.25 的死法）。
#
#   判据要分成两种：
#     起点晚是因为**数据缺**（该补）→ 全量
#     起点晚是因为**它上市晚**（补不到）→ 增量
#   靠 first_ever（这只最早能拿到哪天）来区分。
chk('★ 588000 起点 2020-11-16，已知上市日 → 增量（不是全量）',
    m.need_full([['2020-11-16', 1.0]], first_ever='2020-11-16'), False)
chk('★ 159545 起点 2024-04-15，已知上市日 → 增量',
    m.need_full([['2024-04-15', 1.0]], first_ever='2024-04-15'), False)
chk('★ 起点比已知上市日还晚（说明真缺了）→ 全量',
    m.need_full([['2021-05-01', 1.0]], first_ever='2020-11-16'), True)
chk('★ 不知道上市日 + 起点晚 → 保守全量确认一次',
    m.need_full([['2020-11-16', 1.0]]), True)
chk('已知上市日但已覆盖 2020-01-02 → 仍是增量',
    m.need_full([['2019-01-02', 1.0]], first_ever='2019-01-02'), False)

# 用真实仓库数据验一遍：这 10 只判完之后，都不该再被判成「需全量」
import json
_fp = os.path.join(HERE, '..', 'data', 'listing_first.json')
if os.path.exists(_fp):
    _first = json.load(open(_fp, encoding='utf-8'))
    _px = json.load(open(os.path.join(HERE, '..', 'data', 'prices_bfq.json'), encoding='utf-8'))
    _still_full = [c for c, v in _px.items() if v and m.need_full(v, _first.get(c))]
    chk('★ 真实 10 只里仍在「需全量」的只数', _still_full, [])
else:
    print('  （跳过真实数据检查：data/listing_first.json 不存在）')

print('\n## merge_incremental：只追加，永不覆盖')

have = [['2026-11-09', 1.00], ['2026-11-10', 1.01], ['2026-11-11', 1.02]]

# 场景 1：新抓的含 11-11（重叠）+ 11-12（新）
fresh = [['2026-11-10', 9.99],    # 故意给一个不同的值，验证它【不会】覆盖旧值
         ['2026-11-11', 9.99],
         ['2026-11-12', 1.03]]
merged, n = m.merge_incremental(have, fresh)
chk('新增根数（只算 11-12）', n, 1)
chk('合并后长度', len(merged), 4)
chk('末日期', merged[-1][0], '2026-11-12')
chk('11-10 的旧值没被覆盖', merged[1][1], 1.01)
chk('11-11 的旧值没被覆盖', merged[2][1], 1.02)
chk('11-12 用的是新值', merged[3][1], 1.03)

# 场景 2：新抓的完全落在旧范围内（今天没新数据）→ 一根都不加
merged2, n2 = m.merge_incremental(have, [['2026-11-11', 9.99]])
chk('无新日期时新增 0 根', n2, 0)
chk('  长度不变', len(merged2), 3)
chk('  旧末值仍是 1.02', merged2[-1][1], 1.02)

# 场景 3：旧数据为空（不该走到这，但要保证不炸）
merged3, n3 = m.merge_incremental([], [['2026-11-12', 1.5]])
chk('空历史时全部收下', (len(merged3), n3), (1, 1))

# 场景 4：连续多天新增
fresh4 = [['2026-11-12', 1.03], ['2026-11-13', 1.04], ['2026-11-14', 1.05]]
merged4, n4 = m.merge_incremental(have, fresh4)
chk('连加 3 天', n4, 3)
chk('  末日期', merged4[-1][0], '2026-11-14')
chk('  原 3 根 + 新 3 根', len(merged4), 6)

# 场景 5：★ 关键 —— 盘中半日价会不会污染旧数据
#   假设时钟判断被绕过、抓到一根当日盘中价，且它的日期正好等于旧末日期
#   （比如补跑历史时）。merge 必须拒绝覆盖。
have5 = [['2026-11-11', 2.882]]       # 已定稿收盘价
bad_intraday = [['2026-11-11', 2.75]]  # 同一天，但是盘中价
merged5, n5 = m.merge_incremental(have5, bad_intraday)
chk('同日盘中价不覆盖定稿价', merged5[0][1], 2.882)
chk('  也不被当成新增', n5, 0)

print('\n## 回归：fetch 的起点参数必须真的生效')

src = open(os.path.join(HERE, '..', 'scripts', 'fetch_price_bfq.py'), encoding='utf-8').read()
# ⚠ 这里只认 `def fetch(` 那一行，不做全文搜索。
#   原因：第一版对全文做 `bars=900 in src`，结果**docstring 里写的
#   「原来签名是 bars=900」也被算成「还在用 bars」** →
#   于是测试奖励「把解释删掉」，方向完全反了。
#
#   剥注释也不够：那个 `bars=900` 在 docstring 里，而 `split('#')` 只去 `#` 注释。
#   **检查「代码里还有什么」时，注释和字符串都不该算数。**
#   这类测试本身写错，比没有测试更糟 —— 它会让你为了通过而删掉有用的解释。
#   （同一个坑今天踩了两次，另一次在 test_time_axis.py。）
sig = [l for l in src.split('\n') if l.lstrip().startswith('def fetch(')]
chk('找到 def fetch 签名', len(sig), 1)
chk('签名用 beg 不用 bars', 'beg' in sig[0] and 'bars' not in sig[0], True)
chk('URL 里用了 beg', 'beg or BEG' in src, True)

print(f'\n{"=" * 50}\n通过 {ok_n} 项，失败 {fail_n} 项')
sys.exit(0 if fail_n == 0 else 1)
