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
chk('main() 里调用了 acquire', 'acquire(code, mk, have' in src_txt, True)
chk('★ 传了 first_ever（否则上市晚的标的会天天全量）',
    'first_ever=first_seen.get(code)' in src_txt, True)
chk('tx_codes 仍然被追踪', 'tx_codes.append' in src_txt, True)
# ⚠ 这条只查「上报文案」，不查别的：用了备用源必须上屏。
#   混源本身不丢人（口径已逐日验过一致），但读页面的人有权知道。
chk('★ 上报文案里有「走腾讯备用源」', '只走腾讯备用源' in src_txt, True)

print('\n## 规则 8：★★ 判「备用源有没有帮上」，看日期比较，不看新增根数')
#
#   ⚠⚠ 这条是第十轮 CI 用真实日志打出来的，不是我推出来的。
#
#   第一版写的是 `if n_add > 0: tx_codes else: tx_stale`（按新增根数判断）。
#   CI 上立刻炸出 7 只误报：
#
#       510050 增量 +0 根（旧 1906 根保留） → 末 2026-11-12  [东财]
#       510300 增量 +0 根（旧 1906 根保留） → 末 2026-11-12  [腾讯]
#       510300 东财失败 → 腾讯兜底（末日期没前进，没帮上）   ← 假警报
#
#   旧数据【本来就已经到 11-12】，腾讯也返回到 11-12，merge 自然新增 0 根。
#   **这是一条每天必然亮的假警报** —— 7.25 那个死法的第四次复发
#   （「一个天天亮的提示等于没装」）。
#
#   正确判据：比较两个末日期。
#     fresh 末 > have 末 → 带来新数据
#     fresh 末 = have 末 → 持平（旧数据本来就有这天，数据没损失）
#     fresh 末 < have 末 → 比手里的还旧，这个源本次无效 ← 只有这种才该报

have_upto = [['2020-01-02', 1.0], ['2026-11-11', 2.1], ['2026-11-12', 2.2]]

# 场景 A：腾讯也到 11-12（与旧数据持平）→ 不该报警
tx_same = [['2026-11-11', 2.1], ['2026-11-12', 2.2]]
f_a, s_a, _ = m.acquire('510050', 'sh', have_upto, fetch_fn=boom, tx_fn=lambda *a, **k: tx_same)
_m, n_a = m.merge_incremental(have_upto, f_a)
chk('持平：新增 0 根', n_a, 0)
chk('★ 但日期比较显示「不旧于旧数据」→ 不该报警', f_a[-1][0] < have_upto[-1][0], False)

# 场景 B：腾讯只到 11-11（比旧数据还旧）→ 这才是真没帮上
tx_older = [['2026-11-10', 2.0], ['2026-11-11', 2.1]]
f_b, s_b, _ = m.acquire('510050', 'sh', have_upto, fetch_fn=boom, tx_fn=lambda *a, **k: tx_older)
chk('★ 比旧数据还旧 → 该报警', f_b[-1][0] < have_upto[-1][0], True)

# 场景 C：腾讯带来新一天 → 帮上了
tx_newer = [['2026-11-12', 2.2], ['2026-11-13', 2.3]]
f_c, s_c, _ = m.acquire('510050', 'sh', have_upto, fetch_fn=boom, tx_fn=lambda *a, **k: tx_newer)
_m3, n_c = m.merge_incremental(have_upto, f_c)
chk('★ 带来新一天 → 新增 1 根', n_c, 1)
chk('★ 且不旧于旧数据', f_c[-1][0] < have_upto[-1][0], False)

src_txt2 = open(os.path.join(HERE, '..', 'scripts', 'fetch_price_bfq.py'), encoding='utf-8').read()
chk('★ 代码里有 tx_stale 分类', 'tx_stale' in src_txt2, True)
chk('★★ 判据是日期比较，不是 n_add', "fresh[-1][0] < have[-1][0]" in src_txt2, True)
chk('★★ 没有按 n_add 判断的残留', 'if n_add > 0:' not in src_txt2, True)
chk('★ 上报里有「兜底无效」的说法', '兜底无效' in src_txt2, True)

print('\n## 回归：全失败分支不能说「两个源都失败」而当没试过')
#
#   ⚠ 原来判的是 tx_codes，而全失败时 out 为空 → tx_codes 必空
#     → 那句提示永远不触发，是个死分支。改成看 bad 里有没有腾讯的痕迹。
chk('★ 全失败分支看 bad 不看 tx_codes', "any('腾讯' in r for _, r in bad)" in src_txt2, True)

print(f'\n{"=" * 50}\n通过 {ok_n} 项，失败 {fail_n} 项')
sys.exit(0 if fail_n == 0 else 1)
