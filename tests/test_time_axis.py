#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""时间口径与交易日历的回归测试（离线，不联网）。

覆盖 2026-11-12 晚发现的三个 bug —— 这三个都是「不报错、看着正常、
但结论一直是错的」，只有断言能钉住：

  bug A  「当日」被无条件丢弃 → 页面永远「落后 1 天」，永远修不好
  bug B  按日历天算缺口 → 每天白打 716 个周末的请求 → 把自己打进限流
  bug C  date.today() 是容器时区（CI 上为 UTC）→ 跨日时「今天」会错一天

跑法：python3 tests/test_time_axis.py
"""
import os
import sys
import json
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'scripts'))

import _td

BJ = timezone(timedelta(hours=8))
ok_n = 0
fail_n = 0


def chk(label, got, want):
    global ok_n, fail_n
    if got == want:
        ok_n += 1
        print(f'  ✅ {label}: {got!r}')
    else:
        fail_n += 1
        print(f'  ❌ {label}: 得到 {got!r}，应为 {want!r}')


def freeze(monkeypatch_dt):
    """把 _td.bj_now 换成固定时刻，便于断言。"""
    _td.bj_now = lambda: monkeypatch_dt


print('\n## bug A：当日该不该采纳，看的是「定稿没有」而不是「是不是今天」')

# 正常工作日 18:38（CI 的真实时刻）—— 必须采纳
freeze(datetime(2026, 11, 12, 18, 38, tzinfo=BJ))
allowed, why = _td.today_allowable()
chk('工作日 18:38 能否采纳当日', allowed, True)
chk('  原因', why, '已收盘')
chk('  当日记录 accept_day(今天)', _td.accept_day('2026-11-12'), True)

# 盘中 11:00 —— 必须拒绝（这是原来那条规则真正要防的情况）
freeze(datetime(2026, 11, 12, 11, 0, tzinfo=BJ))
allowed, why = _td.today_allowable()
chk('工作日 11:00 能否采纳当日', allowed, False)
chk('  原因含「未收盘」', '未收盘' in why, True)
chk('  当日记录 accept_day(今天)', _td.accept_day('2026-11-12'), False)

# 收盘瞬间 15:00 —— 仍然拒绝（留 10 分钟给定稿）
freeze(datetime(2026, 11, 12, 15, 0, tzinfo=BJ))
chk('15:00 整能否采纳（未过 15:10 缓冲）', _td.today_allowable()[0], False)

# 15:11 —— 采纳
freeze(datetime(2026, 11, 12, 15, 11, tzinfo=BJ))
chk('15:11 能否采纳', _td.today_allowable()[0], True)

# 昨天以前 —— 任何时候都采纳（终态数据）
freeze(datetime(2026, 11, 12, 9, 0, tzinfo=BJ))
chk('盘中 9:00 采纳昨天', _td.accept_day('2026-11-11'), True)
chk('盘中 9:00 拒绝今天', _td.accept_day('2026-11-12'), False)
chk('未来日期一律拒绝', _td.accept_day('2026-11-13'), False)

# 周末 —— 拒绝当日
freeze(datetime(2026, 11, 14, 18, 0, tzinfo=BJ))   # 周六
chk('周六 18:00 采纳当日', _td.accept_day('2026-11-14'), False)
chk('  原因', _td.today_allowable()[1], '周末')


print('\n## bug C：日期必须是北京时间，不是容器时区')

# UTC 2026-11-12 20:30 = 北京 2026-11-13 04:30
# 这就是「跨日错一天」的现场：UTC 还是 12 号，北京已经 13 号了
freeze(datetime(2026, 11, 12, 20, 30, tzinfo=timezone.utc))
chk('UTC 20:30 的北京日期', _td.bj_today_str(), '2026-11-13')
chk('  UTC date.today() 会得到（错的）', datetime(2026, 11, 12).date().isoformat(), '2026-11-12')
chk('  两者确实不同 → 这个 bug 真实存在',
    _td.bj_today_str() != datetime(2026, 11, 12).date().isoformat(), True)


print('\n## bug B：缺口要按交易日算，不能按日历天')

# 造一个只有交易日的假价格文件：2026-11-02 ~ 2026-11-06（跳过周末）
tmp = tempfile.mkdtemp()
fake = {'510050': [[d, 1.0] for d in
                   ['2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06']]}
json.dump(fake, open(os.path.join(tmp, 'prices_bfq.json'), 'w'), ensure_ascii=False)

_orig_data = _td.DATA
_td.DATA = tmp
cal = _td.trading_days(start='2026-11-01')
chk('反推出的交易日', cal, ['2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06'])

miss = _td.missing_trading_days(['2026-11-02', '2026-11-03'], start='2026-11-01')
chk('真正的缺口（只剩 3 天）', miss, ['2026-11-04', '2026-11-05', '2026-11-06'])

# 用日历天算会得到多少？这是那个 846 的来源
cal_naive = 6      # 11-01 ~ 11-06 共 6 个日历天
chk('日历天口径会算成', cal_naive - 2, 4)
chk('交易日口径只有', len(miss), 3)
chk('差值 = 白打的请求数', (cal_naive - 2) - len(miss), 1)   # 周末 11-01

# 全都有 → 缺口 0（这正是生产环境的真实情况：1661 = 1661）
miss0 = _td.missing_trading_days(
    ['2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06'],
    start='2026-11-01')
chk('数据齐全时缺口', miss0, [])

# 价格文件读不到 → 返回 None，让调用方降级，而不是退回全区间日历天
_td.DATA = os.path.join(tmp, 'nope')
chk('无价格文件时 trading_days', _td.trading_days(), None)
chk('无价格文件时 missing', _td.missing_trading_days(['2026-11-02'], '2026-11-01'), None)
_td.DATA = _orig_data


print('\n## 回归：三个抓取脚本必须都走 _td，不许自己写 date.today()')


def code_lines(src):
    """只留代码行，去掉注释和字符串 —— 否则注释里写的「原来这里是 x >= today」
    会被当成「还在用旧写法」，测试反而奖励把解释删掉。"""
    out = []
    for line in src.split('\n'):
        s = line.split('#')[0].rstrip()
        if s.strip():
            out.append(s)
    return out


scripts = ['fetch_history.py', 'fetch_price_bfq.py', 'fetch_index.py']
for fn in scripts:
    src = open(os.path.join(HERE, '..', 'scripts', fn), encoding='utf-8').read()
    body = '\n'.join(code_lines(src))
    chk(f'{fn} 导入 _td', 'import _td' in body, True)
    # 旧写法（真代码里出现才算复发）：
    #   if p[0] >= today: continue      /   x[0] < TODAY
    bad = ('>= today' in body) or ('< TODAY' in body) or ('date.today()' in body)
    chk(f'{fn} 不用「无条件丢弃当日 / 容器时区」写法', bad, False)
    chk(f'{fn} 用 accept_day 判定', 'accept_day' in body, True)

print(f'\n{"="*50}\n通过 {ok_n} 项，失败 {fail_n} 项')
sys.exit(0 if fail_n == 0 else 1)
