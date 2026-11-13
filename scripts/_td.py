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


def expect_asof():
    """判断「数据该到哪天」的基准日，返回 (日期, 说明)。

    ⚠⚠ 2026-11-13 加，起因是体检栏的一次误报：

      用户在 11-13 上午 09:52 截图，看到
        「上证指数 / 两市成交额   2026-11-12 · 落后 1 天」
        「价格（前复权）          2026-11-12 · 落后 1 天」
      他的原话：**「这个也不算落后，现在是 11.13 号的上午，
      我们本来也是 11.13 号晚上才更新当天的内容」**

      他是对的。lag 原来算的是 `今天 - last`，而「今天」用的是
      **日历上的今天**，不是**「数据本该已经到了的最近一天」**。
      11-13 09:56 时 11-13 的收盘价根本还不存在（15:00 才收盘），
      拿 11-13 当尺子去量 11-12 的数据，必然量出「落后 1 天」。

      **这就是 _fh.py 里反复写的那个「狼来了」死法，只是换了个入口复发：
      把「还没到更新时间」误报成「落后」。**

    正确基准：往前找最近一个「此时此刻它的数据本该已经定稿」的交易日。
      · 今天是交易日、且已过 15:10 收盘定稿 → 基准 = 今天
      · 今天是交易日、但还没收盘          → 基准 = 上一个交易日
      · 今天是周末/节假日（非交易日）      → 基准 = 上一个交易日

    为什么基准是「上一个交易日」而不是「上一个日历日」：
      周五晚的数据，周六上午看它，`今天 - last` = 1 天，
      但周五的数据本来就该是周五的，周六没有行情 —— 用日历年算会
      把每个周六、每个周日、每个节假日都报成落后。

    ⚠ 这个函数只管「该到哪天」。至于各数据源自己天生滞后（份额 T+1、
      两融 T+1）那是另一层，由 _fh.LAG_OK 处理，别混进来。
    """
    t = bj_today()
    if today_allowable()[0]:
        # 今天已收盘定稿 → 今天的数据该有了
        return t.isoformat(), '今天已收盘'

    # 今天还没定稿 / 非交易日 → 基准退到上一个交易日
    cal = trading_days()
    if not cal:
        # 没有日历可推（首次部署）→ 退到昨天，别抛异常把体检拖挂
        return (t - timedelta(days=1)).isoformat(), '无交易日历，退到昨天'

    prev = [d for d in cal if d < t.isoformat()]
    if not prev:
        return (t - timedelta(days=1)).isoformat(), '日历里没有更早的交易日'
    return prev[-1], ('今天未收盘' if t.weekday() < 5 else '非交易日')


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


def trading_gap(d_from, d_to):
    """d_from 与 d_to 之间隔了几个交易日（不含 d_from，含 d_to）。

    ⚠⚠ 2026-11-13 加。原来 _fh 算「落后几天」用的是自然日差，
    于是每个周一都会把「周五的数据」报成「落后 3 天」——
    而周五到周一之间只过了 1 个交易日：

        11-06（周五）→ 11-09（周一）  自然日 3 天   交易日 1 天
        11-11（周二）→ 11-12（周三）  自然日 1 天   交易日 1 天
        11-10（周一）→ 11-13（周四）  自然日 3 天   交易日 2 天（跨 11-11、11-12）

    「落后 3 天」听起来像「漏了三个交易日」，实际只漏一个 ——
    这又是一个「数字对、含义错」。**体检栏报的天数必须是交易日。**

    返回 0 表示没有落后（数据就是该到的那天）。
    """
    if not d_from or not d_to or d_to <= d_from:
        return 0
    cal = trading_days()
    if cal is None:
        # 没有日历可推 → 退到自然日差。宁可粗糙，也别因为算不出而不报。
        try:
            return (datetime.strptime(d_to[:10], '%Y-%m-%d')
                    - datetime.strptime(d_from[:10], '%Y-%m-%d')).days
        except Exception:
            return 0

    d_from, d_to = d_from[:10], d_to[:10]
    last_known = cal[-1] if cal else None

    # ── 第 1 段：日历覆盖到的部分，用日历精确数 ──────────────────────
    s = set(cal)
    if d_to > last_known:
        # ⚠ d_to 超出日历末位，说明这段日历还不知道 —— 交给第 2 段
        s.add(last_known)
    n = sum(1 for d in s if d_from < d <= d_to)

    # ── 第 2 段：日历之后的部分，按工作日估 ───────────────────────────
    #
    # ⚠⚠⚠ 这一段是必须的，而且是这次差点写漏的一个死循环：
    #
    #   日历是从 prices_bfq.json / prices.json 的日期集合反推的
    #   （见 trading_days 的说明：让数据自己说，不用维护休市表）。
    #   但这也意味着 —— **数据停更时，日历跟着一起停。**
    #
    #   于是会出现这种情况：
    #     抓取全挂了一个月 → 价格文件停在 11-12 → 日历末位也是 11-12
    #     → 拿 12-10 当基准日去数交易日 → 日历里没有 11-12 之后的日子
    #     → 数出 0 天 → **落后一个月，体检栏报「已是最新」。**
    #
    #   这比误报「落后 1 天」严重得多：那是多报，这是瞒报。
    #   而且它恰好只在「真出大事」时触发，平时完全看不出来。
    #
    #   所以日历覆盖不到的地方，退一步按【工作日】估（周一到周五）。
    #   误差来源只有一个：这段里的法定节假日会被多算。
    #   但这段本来就是「已经不正常了」的区间 —— 多算几天比报 0 天好得多。
    if d_to > last_known:
        _d = datetime.strptime(last_known, '%Y-%m-%d').date()
        _end = datetime.strptime(d_to, '%Y-%m-%d').date()
        _start = max(_d, datetime.strptime(d_from, '%Y-%m-%d').date())
        _cur = _start + timedelta(days=1)
        while _cur <= _end:
            if _cur.weekday() < 5:      # 周一至周五
                n += 1
            _cur += timedelta(days=1)
    return n
