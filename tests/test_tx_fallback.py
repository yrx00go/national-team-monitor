#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯备用源兜底（离线，不联网）。

对应 2026-11-12 深夜的改动：东财 push2his 在 GitHub Actions 上经常整段 0/10，
净流入只能拿旧价凑。加腾讯 fqkline 兜底，专门保证「今天这一根」拿得到。

⚠⚠ 这个测试存在的理由，比它测的那些规则更重要：
   兜底分支的触发条件是「东财失败」。CI 上东财有时通、有时不通 ——
   **东财一通，兜底那段代码就永远不执行。**
   一段从未被执行过的错误处理，和没有错误处理是一回事，
   只是更让人安心（＝更危险）。所以这里用可注入的假函数，
   把每种失败组合在离线状态下都真跑一遍。

跑法：python3 tests/test_tx_fallback.py
"""
import os
import sys
import types

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


OLD_FULL = [['2020-01-02', 1.0], ['2026-11-10', 2.0], ['2026-11-11', 2.1]]
OLD_BAKYARD = [['2026-06-01', 1.0], ['2026-11-11', 2.1]]   # 起点太晚 → 需全量


def boom(*a, **kw):
    raise RuntimeError('RemoteDisconnected')


print('\n## 规则 1：★ 取 row[2]（收盘价），不是 row[1]（开盘价）')

# ⚠ 腾讯行格式 = [日期, 开, 收, 高, 低, 量]
#   凡是把开收价写反的实现，在真实数据上「大多数日子」看起来也像对的
#   （开收差千分之几），但净流入 = 份额差 × 当日价，每天都会错一点。
js = {'data': {'sh510050': {'day': [
    ['2026-11-10', '2.880', '2.891', '2.900', '2.870', '1000'],
    ['2026-11-11', '2.891', '2.882', '2.895', '2.875', '1200'],
]}}}
rows = m.parse_tx(js, 'sh510050')
chk('拿到 2 行', len(rows), 2)
chk('日期取 row[0]', rows[0][0], '2026-11-10')
chk('★ 收盘取 row[2] = 2.891（不是开盘 2.880）', rows[0][1], 2.891)
chk('★ 第二行收盘 2.882（不是开盘 2.891）', rows[1][1], 2.882)

# 直接断言「不是开盘价」，比断言具体数值更能防回归
chk('★ 不等于 row[1]', rows[0][1] != 2.880, True)
chk('★ 不等于 row[3]（最高价）', rows[0][1] != 2.900, True)
chk('★ 不等于 row[4]（最低价）', rows[0][1] != 2.870, True)

print('\n## 规则 1b：腾讯的两种 key 都要认')

js2 = {'data': {'sz159915': {'bfqkline': [['2026-11-12', '2.0', '2.05', '2.1', '1.9', '1']]}}}
chk('bfqkline 分支也能解析', m.parse_tx(js2, 'sz159915')[0][1], 2.05)
chk('两种 key 都没有 → 空', m.parse_tx({'data': {'sh510050': {}}}, 'sh510050'), [])
chk('data 缺失 → 空', m.parse_tx({}, 'sh510050'), [])
chk('短行（<3 列）被跳过', len(m.parse_tx(
    {'data': {'x': {'day': [['2026-11-12', '2.0'], ['2026-11-12', '2', '3']]}}}, 'x')), 1)

print('\n## 规则 2：★ 需要全量回补时，不走腾讯兜底')

#   腾讯硬上限 641 根，给不了 2020 年起的 ~1900 根。
#   硬用它补 → 得到「起点 2024-03」的残缺序列 → need_full 永远成立
#   → 每天重抓、每天得到同一段残缺数据，死循环且不自知。
called = {'tx': 0}


def fake_tx(*a, **kw):
    called['tx'] += 1
    return [['2026-11-12', 9.9]]

fresh, src, err = m.acquire('510050', 'sh', OLD_BAKYARD, fetch_fn=boom, tx_fn=fake_tx)
chk('★ 需全量时结果为空', fresh, None)
chk('★ 需全量时不调用腾讯', called['tx'], 0)
chk('★ 错误里写明「需全量」', '需全量' in (err or ''), True)

# 没历史（第一次跑）也是全量 —— 同样不能兜
called['tx'] = 0
fresh, src, err = m.acquire('510050', 'sh', [], fetch_fn=boom, tx_fn=fake_tx)
chk('无历史 → 也不兜底', (fresh, called['tx']), (None, 0))

print('\n## 规则 3：增量场景，东财失败 → 腾讯兜底成功')

called['tx'] = 0
tx_rows = [['2026-11-11', 2.1], ['2026-11-12', 2.15]]
fresh, src, err = m.acquire('510050', 'sh', OLD_FULL,
                            fetch_fn=boom, tx_fn=lambda *a, **k: tx_rows)
chk('拿到数据', fresh, tx_rows)
chk('★ 来源标成「腾讯」', src, '腾讯')
chk('无错误', err, None)

print('\n## 规则 4：兜底也失败 → 计 bad，不写空序列')

fresh, src, err = m.acquire('510050', 'sh', OLD_FULL, fetch_fn=boom, tx_fn=boom)
chk('★ 不返回空列表（空列表会被当成「今日无新增」）', fresh, None)
chk('来源为空', src, None)
chk('★ 错误里两个源都提到', ('东财' in err and '腾讯' in err), True)

print('\n## 规则 5：东财成功时不碰腾讯（不多打一次请求）')

called['tx'] = 0


def fake_ok(*a, **kw):
    return [['2026-11-12', 2.15]]


fresh, src, err = m.acquire('510050', 'sh', OLD_FULL, fetch_fn=fake_ok, tx_fn=fake_tx)
chk('来源是东财', src, '东财')
chk('★ 东财通了就不碰腾讯', called['tx'], 0)

print('\n## 规则 6：东财返回空列表 = 失败，要兜底')

called['tx'] = 0
fresh, src, err = m.acquire('510050', 'sh', OLD_FULL,
                            fetch_fn=lambda *a, **k: [],
                            tx_fn=lambda *a, **k: (called.__setitem__('tx', called['tx'] + 1)
                                                   or [['2026-11-12', 2.15]]))
chk('★ 空返回被当成失败并兜底', src, '腾讯')
chk('  腾讯确实被调用了', called['tx'], 1)

print('\n## 规则 7：全量场景下东财成功，照常用')

fresh, src, err = m.acquire('510050', 'sh', [], fetch_fn=fake_ok, tx_fn=boom)
chk('全量 + 东财通 → 东财', src, '东财')

print('\n## 回归：主干代码里不再有内联的兜底 try/except')

src_txt = open(os.path.join(HERE, '..', 'scripts', 'fetch_price_bfq.py'), encoding='utf-8').read()
chk('main() 里调用了 acquire', 'acquire(code, mk, have)' in src_txt, True)
chk('tx_codes 仍然被追踪', 'tx_codes.append' in src_txt, True)
# ⚠ 这条只查「上报文案」，不查别的：用了备用源必须上屏。
#   混源本身不丢人（口径已逐日验过一致），但读页面的人有权知道。
chk('★ 上报文案里有「走腾讯备用源」', '只走腾讯备用源' in src_txt, True)

print('\n## 规则 8：★★ 腾讯「接通」≠「给了今天的价」')
#
#   ⚠ 这是写这个测试时自己发现的漏洞，不是想出来的：
#     兜底成功、但腾讯返回的末日期还是旧那一天（源本身滞后），
#     于是 merge_incremental 新增 0 根。
#     这一只会被算成「成功」（不在 bad 里），note 里写着「走腾讯备用源」——
#     **读起来像「有备用所以没事」，实际是「换了个源，今天这一根照样没拿到」。**
#     比彻底失败更误导，因为它看起来是绿的。
#
#   规则：merge 新增 0 根 → 即使 src='腾讯' 也要计入 tx_stale，不能报 ok。
have_lag = [['2020-01-02', 1.0], ['2026-11-10', 2.0], ['2026-11-11', 2.1]]
tx_lag = [['2026-11-11', 2.1]]          # 接通了，但末日期就是 11-11

fresh, src, err = m.acquire('510050', 'sh', have_lag,
                            fetch_fn=boom, tx_fn=lambda *a, **k: tx_lag)
chk('acquire 认为「成功」', (src, err), ('腾讯', None))
_m, n_add = m.merge_incremental(have_lag, fresh)
chk('★ 但实际新增 0 根（没帮上）', n_add, 0)

# 对照：腾讯真给了 11-12 → 新增 1 根
tx_ok = [['2026-11-11', 2.1], ['2026-11-12', 2.15]]
fresh2, src2, _ = m.acquire('510050', 'sh', have_lag,
                            fetch_fn=boom, tx_fn=lambda *a, **k: tx_ok)
_m2, n_add2 = m.merge_incremental(have_lag, fresh2)
chk('★ 腾讯真给到当日 → 新增 1 根', n_add2, 1)

src_txt2 = open(os.path.join(HERE, '..', 'scripts', 'fetch_price_bfq.py'), encoding='utf-8').read()
chk('★ 代码里有 tx_stale 分类', 'tx_stale' in src_txt2, True)
chk('★ 没前进时不计入「走备用源」', 'if n_add > 0:' in src_txt2 and 'tx_codes.append' in src_txt2, True)
chk('★ 上报里有「末日期没前进」的说法', '末日期没前进' in src_txt2, True)

print('\n## 回归：全失败分支不能说「两个源都失败」而当没试过')
#
#   ⚠ 原来判的是 tx_codes，而全失败时 out 为空 → tx_codes 必空
#     → 那句提示永远不触发，是个死分支。改成看 bad 里有没有腾讯的痕迹。
chk('★ 全失败分支看 bad 不看 tx_codes', "any('腾讯' in r for _, r in bad)" in src_txt2, True)

print(f'\n{"=" * 50}\n通过 {ok_n} 项，失败 {fail_n} 项')
sys.exit(0 if fail_n == 0 else 1)
