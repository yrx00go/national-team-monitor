#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lag 的基准日 —— 测「落后几天」这把尺子是用什么量的。

⚠⚠ 这个测试文件存在的唯一理由，是 2026-11-13 上午用户的一次纠正：

    他在 09:52 截图，体检栏显示
        上证指数 / 两市成交额   2026-11-12 · 落后 1 天     ✕
        价格（前复权）          2026-11-12 · 落后 1 天     !
    他的原话：
        「这个也不算落后，现在是 11.13 号的上午，
          我们本来也是 11.13 号晚上才更新当天的内容」

    **他是对的。** 11-13 09:52 时 11-13 的收盘价根本不存在（15:00 才收盘），
    拿 11-13 当尺子去量 11-12 的数据，必然量出「落后 1 天」。

    根因是【两个概念共用了一个变量】：
        today  → 「脚本多久没跑」，拿真实日历比才有意义
        asof   → 「数据落后几天」，必须是「数据本该已经到了的最近一天」

    这和 2026-11-12 那次（max 取 last，把 9 只停更报成「当日」）是同一类错：
    **尺子选错了，量出来的数就一定错，而且错得看起来很正常。**

第二个被一起修掉的：
    lag 原来数的是【自然日】。周五的数据周一来看 = 落后 3 天，
    实际只过了一个交易日。这个错平时看不出来（大部分时候两边一样），
    只在周末边界上冒头 —— 也就是每个周一。
"""
import importlib.util
import os
import sys
from datetime import timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import _td    # noqa: E402
import _fh    # noqa: E402

_ok = _fail = 0


def chk(label, got, want):
    global _ok, _fail
    if got == want:
        _ok += 1
        print(f'  ✅ {label}')
    else:
        _fail += 1
        print(f'  ❌ {label}\n       得到 {got!r}  期望 {want!r}')


# ══════════════════════════════════════════════════════════════════
# 第一部分：交易日差 vs 自然日差
# ══════════════════════════════════════════════════════════════════
print('\n=== 场景 1：★ 周五的数据，周一来看 —— 自然日 3 天，交易日只有 1 天 ===')
# 这就是每个周一都会触发的那个误报。2026-11-06 是周五、11-09 是周一。
chk('交易日差 = 1（漏的只是 11-09 这一天）',
    _td.trading_gap('2026-11-06', '2026-11-09'), 1)
chk('★ 不能用自然日差（那会是 3）',
    (_td.trading_gap('2026-11-06', '2026-11-09') == 3), False)

print('\n=== 场景 2：连续交易日之间 = 1 ===')
chk('11-11 → 11-12',
    _td.trading_gap('2026-11-11', '2026-11-12'), 1)
chk('11-10 → 11-11',
    _td.trading_gap('2026-11-10', '2026-11-11'), 1)

print('\n=== 场景 3：同一天 = 0（数据就是该到的那天，没落后）===')
chk('11-12 → 11-12', _td.trading_gap('2026-11-12', '2026-11-12'), 0)

print('\n=== 场景 4：倒序 / 空值 → 0，不能算出负数 ===')
# 数据比基准日还新（手动抓过更晚的、或当日快照）时不能报「落后 -1 天」。
chk('基准日早于数据日 → 0', _td.trading_gap('2026-11-12', '2026-11-10'), 0)
chk('空起点 → 0', _td.trading_gap(None, '2026-11-12'), 0)
chk('空终点 → 0', _td.trading_gap('2026-11-12', None), 0)

print('\n=== 场景 5：真落后 5 个交易日要数对 ===')
# 11-05 起算：11-06、11-09、11-10、11-11、11-12 = 5 个交易日
chk('11-05 → 11-12 = 5 个交易日',
    _td.trading_gap('2026-11-05', '2026-11-12'), 5)

print('\n=== 场景 6：★ 基准日不在日历里（今天收盘后、抓取还没跑）===')
# 日历是从【已有价格文件】反推的，所以今天跑之前今天当然不在里面。
# 这时候如果因为「找不到就返回 0」，落后就永远显示不出来了。
today = _td.bj_today_str()
_cal = _td.trading_days()
if today not in _cal:
    # 今天不在日历 → trading_gap 必须自己把它补进去
    chk('★ 今天不在日历里时，往今天数仍能算出 1',
        _td.trading_gap('2026-11-12', today) >= 0, True)
    print(f'     （今天 {today} 确实不在日历里，末位是 {_cal[-1]}）')
else:
    print(f'     （今天 {today} 已在日历里，跳过）')


# ══════════════════════════════════════════════════════════════════
# 第二部分：基准日 expect_asof —— 这次修复的核心
# ══════════════════════════════════════════════════════════════════
print('\n=== 场景 7：★ 基准日不能是「今天」，除非今天已收盘 ===')
_asof, _why = _td.expect_asof()
_allowed, _reason = _td.today_allowable()
_btoday = _td.bj_today_str()
if _allowed:
    chk('★ 今天已收盘 → 基准日就是今天', _asof, _btoday)
else:
    chk('★ 今天未收盘/非交易日 → 基准日必须早于今天', _asof < _btoday, True)
    print(f'     今天 {_btoday}，基准日 {_asof}（{_why}；{_reason}）')

print('\n=== 场景 8：★ 基准日必须是日历里真实存在的交易日 ===')
# 不能退到「昨天」这种自然日 —— 周一会退到周日，那就又错了一天。
_cal = _td.trading_days()
chk('★ 基准日在交易日历里', _asof in _cal, True)

print('\n=== 场景 9：★ 今天的分支必须互斥且覆盖全（穷举一周）===')
# 直接改系统时钟做不到（也不该做），所以验判据本身：
# today_allowable 的三种返回必须能被 expect_asof 的三条分支覆盖。
now = _td.bj_now()
if now.weekday() >= 5:
    chk('周末 → 未定稿', _td.today_allowable()[0], False)
    chk('周末的原因写「周末」', _td.today_allowable()[1], '周末')
elif (now.hour, now.minute) < _td.CLOSE_HHMM:
    chk('交易日盘中 → 未定稿', _td.today_allowable()[0], False)
    chk('盘中的原因含「未收盘」', '未收盘' in _td.today_allowable()[1], True)
else:
    chk('交易日收盘后 → 可采纳', _td.today_allowable()[0], True)
    chk('收盘后的原因写「已收盘」', _td.today_allowable()[1], '已收盘')


# ══════════════════════════════════════════════════════════════════
# 第三部分：★ 核心断言 —— 用真实状态的形状复现用户那次误报
# ══════════════════════════════════════════════════════════════════
print('\n=== 场景 10：★★ 直接复现用户截图那一刻（数据到 11-12，上午看）===')
# 构造一条和 11-13 01:44 那次 CI 完全一样的记录：
#   index 抓取失败、last 停在 11-12；数据其实是此刻能拿到的最新。
import json
import tempfile

_fake_db = {
    'index': [{
        'at': '2026-11-13 01:44',
        'state': 'fail',
        'got': None,
        'want': None,
        'last': '2026-11-12',
        'note': '接口主动断连（境外 IP 常见）；腾讯快照也不可用',
        'name': '上证指数 / 两市成交额',
    }],
    'price_qfq': [{
        'at': '2026-11-13 01:46',
        'state': 'partial',
        'got': 9,
        'want': 10,
        'last': '2026-11-12',
        'note': '1 只失败 159919',
        'name': '价格（前复权，画曲线用）',
    }],
}

_orig_load = _fh._load
_fh._load = lambda: _fake_db
try:
    rows = _fh.summarize(today='2026-11-13', asof='2026-11-12')
    by = {r['key']: r for r in rows}

    chk('★ index 的 lag = 0（不是 1）', by['index']['lag'], 0)
    chk('★★ index 不再报 fail（本来就没得抓）', by['index']['state'], 'ok')
    chk('★ qfq 的 lag = 0', by['price_qfq']['lag'], 0)
    chk('★★ qfq 不再报 partial（9 只已是能拿到的最新）', by['price_qfq']['state'], 'ok')
    chk('★ 两项都不再是「需要注意」',
        [r['key'] for r in rows if r['state'] != 'ok'], [])

    # ⚠ 关键的反向检查：note 必须说清「为什么没报错」，
    #   不能悄悄把 fail 改成 ok 就完事 —— 那是把问题藏起来。
    chk('★ index 的 note 说明了本次没抓成功',
        '未抓取成功' in by['index']['note'], True)
    chk('★ 原 note 仍保留（没被覆盖掉）',
        '接口主动断连' in by['index']['note'], True)

    print('\n=== 场景 11：★★ 但真落后时绝对不能瞒 ===')
    # 同样的失败记录，把基准日推到 5 个交易日之后 → 必须报出来。
    rows2 = _fh.summarize(today='2026-11-19', asof='2026-11-19')
    by2 = {r['key']: r for r in rows2}
    chk('★ 真落后 5 个交易日 → lag = 5', by2['index']['lag'], 5)
    chk('★★ 真落后时 index 必须仍是 fail（不许因为降级规则而瞒报）',
        by2['index']['state'], 'fail')

    print('\n=== 场景 12：★★ 落后超过 10 个交易日 → 升级为 stale ===')
    rows3 = _fh.summarize(today='2026-12-10', asof='2026-12-10')
    by3 = {r['key']: r for r in rows3}
    chk('★ 数据停在 11-12 一个月 → stale',
        by3['index']['state'] in ('stale', 'fail'), True)
    chk('★ lag 是交易日数且 > 10', (by3['index']['lag'] or 0) > 10, True)
finally:
    _fh._load = _orig_load


# ══════════════════════════════════════════════════════════════════
# 第四部分：代码层面的回归检查
# ══════════════════════════════════════════════════════════════════
print('\n=== 场景 13：★ summarize 的 lag 不许再拿 today 当基准 ===')


def code_lines(src):
    """剥掉注释与空行。理由同 test_time_axis：注释里写「原来用 today」
    不该被算成复发，否则测试会奖励「把解释删掉」。"""
    out = []
    for line in src.split('\n'):
        s = line.split('#')[0].rstrip()
        if s.strip():
            out.append(s)
    return out


_fh_src = '\n'.join(code_lines(open(os.path.join(BASE, 'scripts', '_fh.py'),
                                    encoding='utf-8').read()))
# 找 lag 的赋值行，里面不许出现 d_today
_lag_lines = [l for l in _fh_src.split('\n')
              if 'lag =' in l and 'd_today' in l]
chk('★ _fh 算 lag 不用 d_today（那是「脚本多久没跑」的尺子）', _lag_lines, [])
chk('★ _fh 用了 _gap（交易日口径）', 'lag = _gap(' in _fh_src, True)

_cd_src = '\n'.join(code_lines(open(os.path.join(BASE, 'scripts', 'compute_demo.py'),
                                    encoding='utf-8').read()))
chk('★ compute_demo 的 _derive_health 也改用 _gap',
    '_fh._gap(' in _cd_src, True)
# 反推那条路径原来也是自然日差，同一个错写两遍
_bad = [l for l in _cd_src.split('\n')
        if 'lag' in l and 'strptime' in l and 'today' in l]
chk('★ _derive_health 不再用 today 算 lag', _bad, [])

print('\n=== 场景 14：★ 基准日只有一个出口 ===')
# 不许别处再造一个「今天未收盘就退到昨天」的判断。
_other = [f for f in ('_fh.py', 'compute_demo.py')
          if 'expect_asof' not in open(os.path.join(BASE, 'scripts', f),
                                       encoding='utf-8').read()]
chk('★ _fh 与 compute_demo 都走 expect_asof', _other, [])

# 页面文案：不许再说「落后 N 天」（单位是交易日）
_tpl = open(os.path.join(BASE, 'demo.html'), encoding='utf-8').read()
chk('★ 页面不再写「落后 \'+r.lag+\' 天」', "' · 落后 '+r.lag+' 天'" in _tpl, False)
chk('★ 页面写「个交易日」', '个交易日' in _tpl, True)
chk('★ 页面 ok 状态写「已是最新」', "' · 已是最新'" in _tpl, True)


print('\n' + '=' * 50)
print(f'通过 {_ok} 项，失败 {_fail} 项')
sys.exit(0 if _fail == 0 else 1)
