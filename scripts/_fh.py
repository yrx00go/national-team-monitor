#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据源健康上报 —— 让「没抓到」这件事能传到页面上。

为什么需要这个文件
────────────────
2026-11-12 这一天里，同一个项目连续炸出五处「静默失败」：

  ① 抓取全失败时把 227 KB 有效数据覆盖成 0 KB（日志只有一行「0 KB」）
  ② CI 每天把离线版换成 CDN 版，伪装成「更新数据」
  ③ 价格混进当日未收盘的半日价（价格到 11-12、份额只到 11-11）
  ④ fetch_inst.py 没有 sys.exit，退出码恒为 0，失败传不到报警
  ⑤ prices.json（画曲线用的前复权价）根本不在日常更新链里，会永久停更

这五处没有一个会「报错」，页面看起来全都正常。共同点不是别的，就是：
  **坏了，但不吭声。**

所以修法不能是「再补一个 if」，而是给整个抓取层装一个统一的仪表盘：
每次抓完，不管成功失败，都往 fetch_status.json 里记一笔「我这次拿到了什么」。
失败也要记 —— **那正是要报出来的东西**。

设计上的三个取舍
────────────────
1. **不抛异常、不影响退出码。** 上报本身失败（磁盘满、JSON 坏）绝不能把
   一个本来成功的抓取拖成失败。这个函数永远是 best-effort。
2. **保留 14 天。** 只留最后一次的话，看不出「连续三天没抓到」和「今天偶尔没抓到」
   的区别 —— 而这两件事的判断完全不同。
3. **按 key 覆盖，不追加。** 同一次运行里 upsert；跨运行按日期保留最近 14 条。

字段含义
────────
  key     数据源标识（shares / price_bfq / index / margin / dividend / inst / price_qfq）
  name    给人看的名字
  got     这次实际拿到的条数
  want    预期条数（拿不全时 pages 上会显示 12/20 而不是笼统的「失败」）
  last    这份数据最新到哪一天（ISO 日期）—— 这是判断「停更」的核心字段
  state   ok / partial / fail / stale
  note    一句话原因，直接上屏，所以要写人话不是写堆栈
  at      这次上报的时间
"""
import json, os
from collections import Counter
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FP = os.path.join(BASE, 'data', 'fetch_status.json')

KEEP_DAYS = 14

# 各数据源的「正常延迟」。判断 stale 时用：
# 一个 T 日能拿到 T-1 的数据源，落后 1 天是正常的；落后 5 天才叫停更。
LAG_OK = {
    'shares':   1,   # 上交所 T 日晚发布，T+1 早上可见
    'price_bfq': 0,  # 收盘后即可得（接口盘中也会给，我们自己剔除当日）
    'price_qfq': 0,
    'index':    0,
    'margin':   1,   # 两融 T+1 晚间公布
    'dividend': 0,
    'inst':     180, # 半年报/年报，滞后两个月，别拿日频标准要求它
}

# 源站本身多久没出新数据，才算「它停了」。
# 为什么单独一个数：数据日期落后，有两种完全不同的原因 ——
#   ① 脚本没跑（我们的问题）→ 该报
#   ② 源站本来就没新数据（春节休市 9 天、周末）→ 报了就是狼来了
# 区分两者的关键是「脚本今天有没有报到」：脚本在跑、且它说「今天无新数据」，
# 那是权威答案，不是停更。所以 stale 的主判据是下面的 REPORT_STALE_DAYS。
# 而 SRC_STALE_DAYS 兜的是另一种：脚本天天在跑，但源站连着十几天不放新数据。
# A 股最长休市是春节（约 9 天），所以取 10 —— 超过 10 天没新数据，基本不可能是假期。
SRC_STALE_DAYS = 10
REPORT_STALE_SLACK = 2   # 脚本连续几天没报到这一项，才算这一项没人管


def majority_last(series):
    """一批序列里，这一批【真正】停在哪一天。返回 (日期, 更晚只数, 更早只数, 总数)。

    ⚠⚠ 2026-11-12 晚新增，起因是一个真实的谎报：

    那天 CI 的日志是
        price_qfq  ⇒  'partial', last=2026-11-12, got=1, want=10
    页面上却显示
        「价格（前复权）  2026-11-12 · 当日  ·  1/10」

    看着像「今天更新了，只是慢了半拍」。实际是 **510050 一只到了 11-12，
    其余 9 只全停在 11-11** —— 也就是 9 只停更，而体检栏报「落后 0 天」。

    原因在取 last 的写法：
        _last = max(v[-1][0] for v in prices.values() if v)
    `max()` 取的是【最快的那一只】。只要有一只前进了，整项的 lag 就归零，
    **其余九只停更多久都看不见**。

    一批数据的新鲜度由【最慢的那部分】决定，不是最快的。

    ⚠⚠⚠ 第一版修法也错了，是跑真实数据才发现的：
    第一版写的是「从新到旧找，第一个覆盖 >= 半数资产的日期」。
    那天恰好的分布是 5 只到 11-12、5 只停在 11-11 —— **平局**。
    从新往旧扫，先碰到 11-12（5 只，5*2 >= 10 成立）→ 报 11-12 → lag 又是 0。
    **一半的数据停更，而体检栏还是说「当日」。等于没修好。**

    现在改成取【中位数】（lower median）：把所有末日期排序，取中间那个。
    这样得到的日期天然【不会高估新鲜度】——
    因为中位数意味着「至少一半的资产不比它更新」。

    为什么不取 min（最旧的）：
    个别标的可能因为停牌、上市晚而天生更旧，取 min 会让整项永远显示陈旧，
    变成另一头的老狼来了（例如 8 只到 11-12、2 只停牌在 10-20，
    min 会报「落后 20 天」）。中位数不被少数极端值拖走。

    返回的「更早只数」= 比这个中位日期还旧的资产数，
    也就是【真正的停更只数】，必须写进 note 让人看见。
    """
    tails = [v[-1][0] for v in series if v]
    if not tails:
        return None, 0, 0, 0
    tails.sort()
    total = len(tails)
    d = tails[(total - 1) // 2]          # lower median：不会偏向更新的一天
    ahead = sum(1 for x in tails if x > d)
    behind = sum(1 for x in tails if x < d)
    return d, ahead, behind, total


def _load():
    if not os.path.exists(FP):
        return {}
    try:
        return json.load(open(FP, encoding='utf-8')) or {}
    except Exception:
        # 旧文件坏了就当空的，不要因为仪表盘本身坏掉而让抓取失败
        return {}


def report(key, name, state, last=None, got=None, want=None, note=''):
    """记一笔。永远不抛异常。"""
    try:
        db = _load()
        hist = db.get(key) or []
        rec = {
            'at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'state': state,          # ok / partial / fail / stale
            'got': got,
            'want': want,
            'last': last,
            'note': note,
            'name': name,
        }
        # 同一天只保留最后一条（一天可能跑好几次），跨天累积
        today = rec['at'][:10]
        hist = [h for h in hist if (h.get('at') or '')[:10] != today]
        hist.append(rec)
        hist.sort(key=lambda h: h.get('at') or '')

        # 只留最近 KEEP_DAYS 天
        cut = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%d')
        hist = [h for h in hist if (h.get('at') or '')[:10] >= cut]

        db[key] = hist
        os.makedirs(os.path.dirname(FP), exist_ok=True)
        tmp = FP + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(db, f, ensure_ascii=False, indent=1)
        os.replace(tmp, FP)
    except Exception as e:
        # 仪表盘坏了不能连累抓取。一句话报警，然后继续。
        print(f'  ⚠ 状态上报失败（不影响本次抓取）：{type(e).__name__}: {e}')


def summarize(today=None):
    """把 fetch_status.json 压成 compute_demo 要内联进页面的结构。

    返回 [{key, name, state, last, lag, got, want, note, days_bad, days_silent}]，
    按严重程度排序。

    ⚠⚠ 2026-11-12 当天改过一次，原因是第一版会自己骗自己：

    第一版是 `lag = today - last`，而 compute_demo 传进来的 today 是
    `days[-1]`（数据自己的最新日期）。**拿被测对象当尺子，永远量不出它短了。**
    实测：今天 11-12、数据停在 11-11 → lag=0 显示「当日」；
    哪怕数据停在 11-05，基准也变成 11-05 → lag 还是 0。
    也就是说这一栏永远不会出现 stale，**它装饰得很像体检，其实什么也没查。**

    现在改成三个独立判据，各查各的：

      ① state 本身是 fail/partial  → 照实显示（抓取当场就知道）
      ② days_silent = 今天 - 最后一次上报日期  → 脚本根本没跑
         这是 ⑤ 号 bug（prices.json 不在更新链里）的唯一可行判据：
         它不报错、不产生失败记录，只能从「这项多久没被上报过」看出来。
      ③ source_lag = 今天 - last 超过 SRC_STALE_DAYS → 源站真停了
         如果脚本今天报到了、且它自己说「今日无新数据」（非交易日/休市），
         那是权威答案，不算停更。周末和春节休市不能天天报警 ——
         一份天天误报的仪表盘，等于没有仪表盘。

    days_bad = 连续几天不是 ok（从最近往前数），区分「今天偶尔没抓到」和
    「已经三天没更新了」。这两件事的判断完全不同，混在一起就等于没报。
    """
    today = today or datetime.now().strftime('%Y-%m-%d')
    try:
        d_today = datetime.strptime(today[:10], '%Y-%m-%d')
    except Exception:
        d_today = datetime.now()

    db = _load()
    out = []
    for key, hist in db.items():
        if not hist:
            continue
        cur = hist[-1]
        ok_lag = LAG_OK.get(key, 1)

        # 连续非 ok 天数（从最近往前数）
        bad = 0
        for h in reversed(hist):
            if h.get('state') == 'ok':
                break
            bad += 1

        # ① 数据本身落后几天
        lag = None
        if cur.get('last'):
            try:
                d0 = datetime.strptime(cur['last'][:10], '%Y-%m-%d')
                lag = (d_today - d0).days
            except Exception:
                lag = None

        # ② 这一项多久没被上报过（判「脚本没跑」）
        days_silent = None
        if cur.get('at'):
            try:
                d_at = datetime.strptime(cur['at'][:10], '%Y-%m-%d')
                days_silent = (d_today - d_at).days
            except Exception:
                days_silent = None

        state = cur.get('state') or 'ok'
        note = cur.get('note') or ''

        # ①② 降级判定。注意顺序：先看抓取当场的结果，再看「没人管」。
        if state == 'ok':
            if days_silent is not None and days_silent > REPORT_STALE_SLACK:
                # 脚本连着好几天没碰过这一项 —— 这才是 ⑤ 号 bug 的形态：
                # 它不会失败，只会一天天没人管。必须报出来。
                state = 'stale'
                note = (note + f'；已 {days_silent} 天没有抓取记录').strip('；')
            elif lag is not None and lag > SRC_STALE_DAYS:
                # 脚本在跑、但它拿到的最新数据是十几天前的 —— 源站真停了
                state = 'stale'
                note = (note + f'；源站已 {lag} 天无新数据').strip('；')
            # ⚠ 这里刻意【不】用 LAG_OK 判 stale：
            #   LAG_OK 表示「这个源天然滞后几天」（如两融 T+1），
            #   数据停在 T-1 是完全正常的，拿它判 stale 会在每个交易日误报。
            #   LAG_OK 现在的用途是给下面 _derive_health 反推时参考。

        out.append({
            'key': key,
            'name': cur.get('name') or key,
            'state': state,
            'last': cur.get('last'),
            'lag': lag,
            'got': cur.get('got'),
            'want': cur.get('want'),
            'note': note,
            'at': cur.get('at'),
            'days_bad': bad,
            'days_silent': days_silent,
            'lag_ok': ok_lag,
        })

    order = {'fail': 0, 'stale': 1, 'partial': 2, 'ok': 3}
    out.sort(key=lambda r: (order.get(r['state'], 9), -(r['lag'] or 0)))
    return out


if __name__ == '__main__':
    # 直接跑一次看当前状态，方便本机排查。
    # ⚠ 打印文案和页面保持一致：ok 的不说「落后几天」。
    #   份额/两融天生 T+1，天天写「落后 1 天」会让人以为坏了，
    #   于是真坏的时候也没人看 —— 本机排查工具同样要经得起这一条。
    rows = summarize()
    if not rows:
        print('还没有任何上报记录（data/fetch_status.json 不存在或为空）')
    for r in rows:
        flag = {'ok': '✅', 'partial': '⚠ ', 'fail': '❌', 'stale': '🕒'}.get(r['state'], '?')
        if r['state'] == 'ok':
            # ⚠ 必须写 `is not None`，不能写 `if r['lag']` ——
            #   lag=0（今天刚更新）在 Python 里是假值，会掉进「无日期」分支。
            #   踩过：股息率明明到 2026-11-12、lag=0，却打印「无日期」。
            lag = (f"最近交易日 {r['last']}" if r['lag'] is not None and r['lag'] > 0
                   else f"当日 {r['last']}" if r['last'] else '无日期')
        elif r['days_silent'] is not None and r['days_silent'] > REPORT_STALE_SLACK:
            lag = f"已 {r['days_silent']} 天未抓取（数据停在 {r['last'] or '—'}）"
        else:
            lag = f"落后 {r['lag']} 天" if r['lag'] is not None else '无日期'
        print(f"{flag} {r['name']:<12} {lag:<18} "
              f"{r['got'] if r['got'] is not None else '—'}/{r['want'] or '—'}  {r['note']}")
