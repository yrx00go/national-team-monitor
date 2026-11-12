#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交易日历与北京时钟 —— 全站唯一的时间口径。

⚠ 为什么单独抽一个模块（2026-11-12 晚）：

   之前每个抓取脚本各自写 `date.today()` 和 `if p[0] >= today: continue`，
   看起来统一，实际埋了三个互相独立的错：

   ① **`date.today()` 在 GitHub Actions 上是 UTC 日期**
      CI 在北京时间 18:38 跑，UTC 还是同一天，看不出问题；
      但只要有一步挪到 UTC 20:00 之后（= 北京次日 04:00），
      `date.today()` 会比北京日期早一天，于是「今天」被当成「昨天」。

   ② **无条件丢弃当日，导致「落后 1 天」是结构性永久状态**
      A 股 15:00 收盘。CI 在北京 18:38 跑，收盘价早已定稿，
      代码仍然写 `p[0] >= today: continue` 把它扔掉。
      结果：**每一次运行都只拿到 T-1，页面永远显示「落后 1 天」，永远修不好。**
      一条天天琥珀的提示栏 = 没人再看它 = 等于没装。
      这就是我上午刚写进 _fh.py 的「狼来了」死法，只是换了个地方复发。

   ③ **按日历天回补，把 716 个周末也当成「缺数据」去打请求**
      shares.json 已有 1661 天，正好等于真实交易日数；
      但 `todo` 是按日历天算的，得到 846 天「待补」，其中 716 是周末、
      其余是春节/国庆。**每天白白多打 846 次请求，
      而且这 846 次恰好把我自己的 IP 打进限流，
      紧接着的价格段就 0/10 —— 一个 bug 造出另一个 bug。**

   所以：时间口径、交易日历、当日可否采纳，三件事集中在这里，别处不许自己判断。
"""
import json
import os
from datetime import date, datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')

BJ = timezone(timedelta(hours=8))

# A 股 15:00 收盘。留 10 分钟给尾盘集合竞价与交易所定稿，
# 15:10 之前一律视当日为「未定稿」，宁可不写也不写半日价。
CLOSE_HHMM = (15, 10)


def bj_now():
    """北京时间的「现在」。"""
    return datetime.now(BJ)


def bj_today():
    """北京时间的「今天」（date 对象）。

    ⚠ 不要用 date.today() —— 那是容器的时区（CI 上是 UTC）。

    ⚠ 这里必须显式 astimezone(BJ)，不能只写 bj_now().date()。
       `datetime.now(BJ)` 返回的确实是 BJ 时间，`.date()` 也就对；
       但只要有人把 bj_now 换成别的（测试、注入、将来改成读系统时钟），
       带 UTC 时区的 datetime 调 `.date()` 就会安静地返回 UTC 日期 ——
       **又是一个「不报错但错一天」**。多写一次转换，堵死这条路。
    """
    return bj_now().astimezone(BJ).date()


def bj_today_str():
    return bj_today().isoformat()


def today_allowable():
    """今天这一根 K 线现在能不能要？

    返回 (能不能, 原因)。不能时原因直接上屏，不要写堆栈。
    """
    now = bj_now()
    if now.weekday() >= 5:
        return False, '周末'
    if (now.hour, now.minute) < CLOSE_HHMM:
        return False, f'未收盘（北京 {now:%H:%M}，15:10 后才有定稿价）'
    return True, '已收盘'


def accept_day(d):
    """某一天的记录是否可以用？

    ⚠ 这里的判断取代了原来三处 `if p[0] >= today: continue`。

    原来那条规则的本意是对的（防盘中半日价），但它把「今天已收盘」和
    「今天正在盘中」当成同一件事，于是每天收盘后跑，仍然把当天扔掉。
    正确的判据不是「是不是今天」，而是「今天这一根定稿了没有」——
    昨天以前一定定稿；今天要看时钟。
    """
    t = bj_today_str()
    if d < t:
        return True
    if d > t:
        return False          # 未来日期，接口偶尔会给脏数据
    return today_allowable()[0]


def trading_days(start=None, lookback_files=('prices_bfq.json', 'prices.json')):
    """真实交易日列表，从本地价格文件的日期集合反推。

    ⚠ 为什么不自己算「工作日 − 节假日」：
       需要维护一张每年更新的休市表，漏一次就少一天，而且永远不知道漏了。
       价格文件里出现的日期**就是**交易所真实开市的日子，自带节假日，
       不用维护、不会过期。这是「让数据自己说」的又一个用法。

    返回 sorted list[str]；如果价格文件都读不到，返回 None（调用方要自己降级）。
    """
    cal = set()
    for fn in lookback_files:
        fp = os.path.join(DATA, fn)
        if not os.path.exists(fp):
            continue
        try:
            obj = json.load(open(fp, encoding='utf-8'))
        except Exception:
            continue
        for v in obj.values():
            if isinstance(v, list):
                cal |= {x[0] for x in v if x}
    if not cal:
        return None
    if start:
        cal = {d for d in cal if d >= start}
    return sorted(cal)


def missing_trading_days(have_keys, start):
    """已有的日期集合 vs 真实交易日，差出真正缺的那几天。

    ⚠ 用日历天算会得到 846 天（716 个周末），实际缺 0 天。
       这个差别就是「每天白打 846 次请求」的来源。
    """
    cal = trading_days(start=start)
    if cal is None:
        return None
    have = set(have_keys)
    return [d for d in cal if d not in have]
