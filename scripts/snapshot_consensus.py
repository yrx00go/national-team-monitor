#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日共识快照 —— 「从今天开始记录」的存档层。

═══ 为什么这一层比图更重要 ═══

用户要的是「从今天开始记录市场共识」。这句话的难点不在画图，在于：
**今天以前的历史，几乎都不存在。**

所有能抓的源，回溯深度都有硬上限（实测）：
  · 东财搜索    ~30 天，且翻页无效（pageIndex 1/2/3 按相关性排序，
                1/2/3/5/10 返回的都是最近那 50 条）
  · 股吧深翻    ~3 天（第 100 页才到 11-13，热门吧更新太快）
  · 中金网列表  ~45 天（page 80 返回 0 条）

也就是说：今天不存，三个月后回头还是只有这 45 天。
**这一层存的是「以后才会有、现在不存就永远没有」的东西。**
所以它是本项目的长期资产，图只是它的一个视图。

═══ 存什么 ═══

每个标的每天存一条快照，字段全部是**原始计数**，不是评分：
  · 机构层：Kitco 周度调查（华尔街分析师 up/down/flat）
  · 散户层：Kitco 周度调查的 Main Street 在线投票
  · A股机构层：forecast.collect 的多空稿数（有日期，可复算）
  · A股散户层：retail.py 的股吧投票

═══ 两条硬规矩 ═══

① **同一天只留一条，新数据覆盖旧的。**
   共识是会修订的：早上抓到的 30 篇，晚上可能变成 45 篇。
   追加会变成同一天两条，画图时出现竖线。覆盖才符合「今天结束时的事实」。

② **不许用今天的数据回填历史。**
   快照只写「今天」。历史缺口就让它空着 —— 空着是诚实的，
   编一个差不多的数才是害人的。图上缺口就是要让人看见。

用法：
  python3 scripts/snapshot_consensus.py            # 写今天的快照
  python3 scripts/snapshot_consensus.py --show     # 只看不写
  python3 scripts/snapshot_consensus.py --backfill # 用已有语料复算历史（见下）
"""
import json
import os
import sys
import time
from datetime import date, datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
OUT = os.path.join(DATA, 'consensus_history.json')

sys.path.insert(0, os.path.join(BASE, 'scripts'))


def _load(name):
    try:
        with open(os.path.join(DATA, name), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _today():
    return date.today().isoformat()


# ─────────────────────── 四路数据 ───────────────────────

def gold_survey_point(survey, day):
    """黄金：Kitco 周度调查。周频，只在调查发布那天记，其余天留 None。

    ⚠ 不做「用上周的数字填本周」—— 那就是插值。
      周一看到的调查，说的是「下周」，它的有效期是发布到下一个调查之间，
      但把它复制到中间每一天，会让曲线看起来像每天都有独立的调查，
      而实际是同一份数据被拉伸。宁可画成台阶上的孤点。
    """
    if not survey:
        return None
    for r in survey.get('rows') or []:
        if r.get('date') == day:
            a, rt = r.get('analyst'), r.get('retail')
            return {
                'analyst': {k: a.get(k) for k in ('up', 'down', 'flat')} if a else None,
                'retail': {k: rt.get(k) for k in ('up', 'down', 'flat')} if rt else None,
                'lean_analyst': r.get('lean_a'),
                'lean_retail': r.get('lean_r'),
                'url': r.get('url'),
            }
    return None


def a_share_institution_point(sentiment, day):
    """A股机构：fetch_sentiment 产出的多空稿数。

    ⚠ 只取 a_share（不含 gold）—— 黄金那栏有更硬的 Kitco 数据，
      两套口径混在一起会让人以为「黄金机构 = 中金网分析师 + Kitco」，
      其实完全不是一个东西。

    ⚠ 日期字段是 **window_to**，不是 date。而且它是「窗口右端」，
      不等于今天的日期：窗口 7 天，今天抓的话 window_to 就是今天；
      但如果是周末/节假日抓的，window_to 是最近一个交易日。
      所以判据用「window_to == day」，对不上就不记 ——
      否则会把 11-14 的结论贴上 11-16 的标签。
    """
    if not sentiment:
        return None
    fc = (sentiment.get('forecast') or {}).get('a_share') or {}
    if not fc:
        return None
    src_day = (fc.get('window_to') or '')[:10]
    if src_day != day:
        return None
    return {
        'up': fc.get('a_up'),
        'down': fc.get('a_down'),
        'flat': fc.get('a_flat'),
        'n_dir': fc.get('a_dir'),
        'n_kept': fc.get('n_kept'),
        'window_days': fc.get('window_days'),
        'window_from': fc.get('window_from'),
    }


def a_share_retail_point(sentiment, day):
    """A股散户：股吧「我看多/我看空」投票。

    ⚠ 日期字段是 **retail.date**（抓取日），不是 window_to。
      散户层的窗口是 30 天，window_to 也是今天，但 date 更直接。
    """
    if not sentiment:
        return None
    rt = sentiment.get('retail') or {}
    if not rt:
        return None
    if (rt.get('date') or '')[:10] != day:
        return None
    return {
        'up': rt.get('n_up'),
        'down': rt.get('n_down'),
        'n_people': rt.get('n_people'),
        'lean': rt.get('lean'),
        'window_days': rt.get('window_days'),
    }


def build_point(day):
    survey = _load('gold_survey.json')
    sentiment = _load('sentiment.json')
    return {
        'date': day,
        'gold_survey': gold_survey_point(survey, day),
        'a_inst': a_share_institution_point(sentiment, day),
        'a_retail': a_share_retail_point(sentiment, day),
        'at': time.strftime('%Y-%m-%d %H:%M'),
    }


# ─────────────────────── 存档 ───────────────────────

def load_hist():
    try:
        with open(OUT, encoding='utf-8') as f:
            h = json.load(f)
            if isinstance(h, dict) and 'rows' in h:
                return h
    except Exception:
        pass
    return {'rows': [], 'note': ''}


def main():
    show = '--show' in sys.argv
    backfill = '--backfill' in sys.argv
    day = _today()

    hist = load_hist()
    rows = {r['date']: r for r in hist.get('rows') or []}

    new = []

    if backfill:
        # 用**已经抓到的语料**复算历史。这不是编造 ——
        # Kitco 调查本身带发布日期，每一条都是那一天真实存在过的数字。
        # 但只补「数据源里有明确日期」的那些，没有就不补。
        survey = _load('gold_survey.json') or {}
        for r in survey.get('rows') or []:
            d = r.get('date')
            if not d:
                continue
            pt = build_point(d)
            if any(pt.get(k) for k in ('gold_survey', 'a_inst', 'a_retail')):
                rows[d] = pt
                new.append(d)
        print(f'复算：从 gold_survey.json 补进 {len(new)} 天（全部带原始发布日期）')

    pt = build_point(day)
    if show:
        print(json.dumps(pt, ensure_ascii=False, indent=2))
        return 0

    # ── 补「按调查自己发布日期」的黄金点（2026-11-23 加）──────────
    #
    # ⚠⚠ 这是一个会永久丢数据的洞，11-23 才发现：
    #   gold_survey_point 按「日期严格相等」匹配，所以一条 Kitco 调查
    #   只有恰好在它发布日期当天跑快照脚本才能入库。
    #   而 Kitco 是**周末发布**（11-22 是周日），CI 的 cron 只在
    #   工作日 18:00 / 21:30 / 23:30 和周二至周六 08:10 跑 ——
    #   **周日那天根本没有任何一次运行**，于是 11-22 的调查永远进不了存档。
    #   表现：gold_survey.json 里明明有这条，页面上却看不到，
    #   「黄金 4 个调查日」一直不变。用户说「市场共识最近没更新了」，就是这个。
    #
    # 修法不是「把周日的数搬到今天」（那是插值/伪造日期），
    # 而是**按调查自己带的那一天补记录** —— 那个数字本来就属于那天。
    # 只在还没有该日期记录时补，不覆盖已有快照。
    gold_filled = []
    if not backfill:
        survey_now = _load('gold_survey.json') or {}
        for r in survey_now.get('rows') or []:
            d = r.get('date')
            if not d or d in rows:
                continue                      # 已有这天的快照 → 不动
            gpt = gold_survey_point(survey_now, d)
            if not gpt:
                continue
            # 只补黄金这一层，A股两层留 None —— 那天我们确实没有 A股 快照，
            # 拿今天的 A股 数据填进去就是编造。
            rows[d] = {
                'date': d,
                'gold_survey': gpt,
                'a_inst': None,
                'a_retail': None,
                'at': time.strftime('%Y-%m-%d %H:%M'),
                'note': '按调查发布日期补录（当天未运行快照脚本）',
            }
            gold_filled.append(d)
        if gold_filled:
            print(f'补录 {len(gold_filled)} 条黄金调查（按发布日期）：'
                  f'{", ".join(sorted(gold_filled))}')

    has_any = any(pt.get(k) for k in ('gold_survey', 'a_inst', 'a_retail'))
    if not has_any and not backfill:
        # ⚠ 今天什么都没抓到，**不写空壳**。
        #   写空壳会让「今天有快照」这件事本身变成假的，
        #   以后看统计会以为记录了 90 天，其实有 30 天是空行。
        #
        # ⚠ 注意这里【不能】因为「刚刚补录了黄金」就放行：
        #   补录的那些已经是独立的行了，今天该不该写，只看今天有没有数据。
        #   11-23 踩过一次 —— 条件写成 `and not gold_filled` 之后，
        #   当天补了 11-22 的黄金，就把 11-23 的空壳一起写进去了。
        print(f'{day} 三路数据都没有 → 不写快照（不写空壳）'
              + (f'；但已按发布日期补录 {len(gold_filled)} 条' if gold_filled else ''))
        if not gold_filled:
            return 0
        # 有补录 → 只存补录的行，今天的空壳仍然不写
    else:
        prev = rows.get(day)
        rows[day] = pt
        if prev:
            print(f'{day} 覆盖旧快照（原来 at={prev.get("at")}）')
        else:
            print(f'{day} 新增快照')

    out_rows = [rows[k] for k in sorted(rows)]
    payload = {
        'at': time.strftime('%Y-%m-%d %H:%M'),
        'n': len(out_rows),
        'from': out_rows[0]['date'] if out_rows else None,
        'to': out_rows[-1]['date'] if out_rows else None,
        'note': ('每日快照。同一天覆盖、跨天追加。'
                 '缺口不补 —— 没有那天数据的就留空，不插值。'),
        'rows': out_rows,
    }
    os.makedirs(DATA, exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))

    fill = {k: sum(1 for r in out_rows if r.get(k)) for k in
            ('gold_survey', 'a_inst', 'a_retail')}
    print(f'写入 {OUT}：{len(out_rows)} 天 '
          f'{payload["from"]} ~ {payload["to"]}')
    print(f'  覆盖：黄金调查 {fill["gold_survey"]} 天 · '
          f'A股机构 {fill["a_inst"]} 天 · A股散户 {fill["a_retail"]} 天')
    return 0


if __name__ == '__main__':
    sys.exit(main())
