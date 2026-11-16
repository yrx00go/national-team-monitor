#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""黄金共识：Kitco 周度调查（华尔街分析师 + Main Street 散户），经中金网转载。

═══ 为什么是这条路，而不是我一开始想的那条 ═══

最初打算抓中金网「名家点金」（catID 1712）—— 每天十几篇、45 个作者、
19 天 600 条，密度看起来完美（是东财黄金源的 12 倍）。

**但读了正文之后发现那批稿子不能用来做共识：**
  · 它们是**日内交易策略**，不是预判。正文是「3869-70 轻仓多，损3852，
    看3890-3905」+「3905-10 空单，止损3933」—— 同时给多和空两个方向，
    标题里落哪个纯看当天行情走到了哪。
  · 更硬的证据：许安丰 11-15 与 11-16 两篇正文**一字不差**，
    点位、止损、目标全同，只改了标题里的日期。
  · 标题里的「反弹」「下跌」大多是**描述已经发生的行情**，不是表态；
    真正的表态是「做空」「沽空」「低多」这类操作词。
  · 实测：用它做「当日 up 占比 vs 当日金价涨跌」，corr = +0.346，
    只有 14 个有效交易日。样本和精度都不足以画一条曲线。

所以放弃了「数标题方向」这条路。**换成了源头本来就有数字的东西。**

═══ 真正的数据源：Kitco 黄金周度调查 ═══

中金网「金市直播」（catID 1710）每周转载一篇 Kitco News 的
周度黄金调查，正文里直接写着：

  13位分析师中，2位即15%预计金价下周上涨，10位即62%认为金价还会继续下跌，
  另外3位即23%认为金价将横盘整理或无法判断方向。
  在线投票方面，共有160票参与，67名散户交易者即42%预计金价下周上涨，
  60人即38%预计下跌，另有33人即21%认为金价将维持盘整。

这是**机构（华尔街分析师）与散户（Main Street 在线投票）分开统计**的
一手比例，不是我从标题里估出来的。用户明确要「机构 + 散户两个都画」，
这份数据正好是两个都有，而且分子分母齐全、可复核。

═══ 诚实交代：频率是每周 1 条，不是我原来以为的每天 ═══

45 天的语料里只有 4 篇 Kitco 调查（10-04 / 10-17 / 11-09 / 11-15）。
这是**源头的事实**，不是抓取失败 —— Kitco 每周才做一次调查。
所以这条曲线只能是**周频台阶**，不能画成日频连续线。
画成日频就得插值，插值就是编造。这一步必须在图上写清楚。

═══ 解析口径 ═══

用「人数 + 括号百分比」结构定位，再用**紧跟在后面的方向词**判多空。
不用「段内第一个百分比」，因为会把「共有160票参与」的 160 当比例。

⚠ 三个百分比合计常是 101%（原文四舍五入），不是解析错：
   11-09 散户 68+15+18=101，因为 143/212=67.5→68、32/212=15.1→15、
   37/212=17.5→18。**原样保留，不强行凑 100**，另存人数供核对。

用法：
  python3 scripts/fetch_gold_survey.py            # 增量
  python3 scripts/fetch_gold_survey.py --full     # 全量重抓
  python3 scripts/fetch_gold_survey.py --pages 40 # 翻更多页
"""
import json
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
OUT = os.path.join(DATA, 'gold_survey.json')

sys.path.insert(0, os.path.join(BASE, 'scripts'))
import _net       # noqa: E402

UA = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36'),
    'Referer': 'http://gold.cnfol.com/',
}

# 中金网 JSONP 文章列表。catID=1710 金市直播（Kitco 周度调查发在这里）
API = ('http://app.cnfol.com/dataapi/index.php/GetArticle/lists'
       '?catID={cat}&limit=20&page={p}&offset=23&jsonCallback=cb')
CAT = 1710

# ⚠ 中金网限流很严：间隔 <5s 连打会稳定返回 HTTP 500，
#   等 20~30s 能恢复（是限流不是封禁）。5s 是实测安全线。
GAP = (5.0, 6.5)
MAX_PAGES = 25          # 每页 20 条，25 页 ≈ 500 条 ≈ 45 天

# 标题里要出现这些词，才值得去抓正文。
# ⚠ 这是**筛正文**不是**筛结论**：宁可多抓几篇正文（成本 1 次请求），
#   也不能因为标题没写「Kitco」就漏掉周度调查 —— 标题常只写
#   「金市前瞻：下周恐不平静！华尔街62%分析师看空黄金」。
CAND = re.compile(r'金市前瞻|Kitco|周度调查|周评|调查|'
                  r'分析师.{0,6}散户|散户.{0,6}分析师|'
                  r'\d+\s*%[^。]{0,10}(?:分析师|散户)')

# 方向锚点。顺序有讲究：先判横盘类，
# 否则「认为金价将横盘整理或无法判断方向」会被「方向」二字带偏。
#
# ⚠ 「方向不明」「方向不明或将继续横盘」是 10-19 那条的实际写法，
#   原来没有，导致 50% 的那一档丢失（机构只剩 33/17，
#   合计 50%，看起来像「数据不全」，其实是解析漏了一档）。
ANCHORS = [
    ('flat', [r'横盘', r'盘整', r'震荡', r'中性', r'持平',
              r'方向不明', r'方向不清', r'不明朗', r'不明确',
              r'无法判断方向', r'难以判断方向', r'暂时难以判断', r'难以判断']),
    ('up',   [r'上涨', r'看涨', r'走高', r'上行']),
    ('down', [r'下跌', r'看跌', r'走低', r'下行']),
]

# 「85人(47%)」「10位即62%」「67名散户交易者即42%」
#
# ⚠⚠ 括号要**吃掉但不强制** —— 这是两次都写错的地方：
#   ① 写成 `[（(]?`（可选、不吃掉）：`37人(20%)` 匹配停在「20%」，
#      后面还剩个「)」，它占掉 14 字窗口第一格，把
#      「预计金价将在未来一周维持震荡」挤成「…维持震」，
#      「震荡」被截断 → flat 那一档凭空消失，且不报错。
#   ② 改成 `[（(]`（必选）：所有「10位即62%」这种无括号写法全部匹配失败，
#      整条数据变成全 None —— 比①更糟，因为它**一条都不剩**。
#
#   正确写法是 `[（(]?` 放在**捕获组之外、数值之后**一起可选：
#   用 `(?:\s*[（(]\s*)?` 在数字前允许一个左括号（吃掉它），
#   同时允许完全不存在。这样两种写法都能命中，且 tail 不再从括号开始。
NUM_PAT = re.compile(r'(\d+)\s*[人名位票][^0-9%％（）()]{0,8}?'
                     r'\s*(?:[（(]\s*)?(\d+)\s*[%％]\s*(?:[)）])?')

# ⚠⚠ 切块只能按「。」，**不能按「；」「，」**。
#   实测反例（10-19）：
#     「…本周共有12位分析师参与Kitco News黄金调查，其中6位、占比50%的人认为
#       下周金价方向不明或将继续横盘；4位、占比33%的人预计金价上涨；
#       仅2位、占比17%的人看跌。」
#   这条调查用**分号**把三个方向连成一句流水，按「；」切会切成三块、
#   每块只剩一个百分比，整条调查就漏掉了（第一版就是这么漏的）。
#   按「。」切才是一块三个数。
#
#   ⚠ 但块内可能混进介绍性废话（「Kitco News最新周度调查显示，华尔街对金价
#     短线前景几乎势均力敌，而主街投资者…明显转多。」），它没有百分比，
#     会被「≥2 个百分比」这条门槛自然滤掉 —— 不需要额外处理。
BLOCK = re.compile(r'[^。]+。?')

# 主语词。⚠ 不含「投资者」：中文转载里「主街投资者」「投资者情绪」常出现在
# 铺垫句里，带上它会把铺垫句误认成散户调查块。
SUBJ_ANALYST = re.compile(r'分析师')
SUBJ_RETAIL = re.compile(r'散户|在线投票|投票|Main\s*Street')


def _pdate(s):
    """'2026年11月16日 08:22' → '2026-11-16'"""
    m = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', s or '')
    if not m:
        return ''
    return '%s-%02d-%02d' % (m.group(1), int(m.group(2)), int(m.group(3)))


def _list_page(cat, page):
    """取列表页一页，返回 list。"""
    url = API.format(cat=cat, p=page)
    _net.throttle(url, gap=GAP)
    req = urllib.request.Request(url, headers=UA)
    raw = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'ignore')
    m = re.search(r'^\s*\w+\s*\((.*)\)\s*;?\s*$', raw, re.S)
    body = m.group(1) if m else raw
    js = json.loads(body)
    # ⚠ 接口直接返回**裸数组** cb([...])，
    #   不是 {flag:'1001', list:[...]} —— 首页 JS 里那个 res['list']
    #   是另一套已废弃的 CMS 接口，照抄会 AttributeError。
    return js if isinstance(js, list) else (js.get('list') or [])


def _body(url):
    """取正文纯文本。"""
    _net.throttle(url, gap=(4.5, 6.0))
    raw = urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=30).read()
    # ⚠ 页面声明 utf-8 但夹着个别坏字节，用 ignore 兜住。
    #   试过 gb18030/gbk/big5，全在更前面就报错 —— 就是 utf-8 带脏字节。
    h = raw.decode('utf-8', 'ignore')
    m = re.search(r'id="[^"]*content[^"]*"[^>]*>(.*?)</div>', h, re.S)
    b = m.group(1) if m else h
    t = re.sub(r'<br\s*/?>|</p>', '\n', b)
    t = re.sub(r'<script.*?</script>|<style.*?</style>', '', t, flags=re.S)
    t = re.sub(r'<[^>]+>', '', t)
    t = re.sub(r'&nbsp;|\u3000|&amp;', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def _parse_side(seg):
    """解析一段（分析师段或散户段）→ (pct_dict, people_dict)。"""
    out = {'up': None, 'down': None, 'flat': None}
    ppl = {'up': None, 'down': None, 'flat': None}
    for m in NUM_PAT.finditer(seg):
        people, pct = int(m.group(1)), int(m.group(2))
        tail = seg[m.end():m.end() + 14]
        for d, pats in ANCHORS:
            if any(re.search(p, tail) for p in pats):
                if out[d] is None:
                    out[d] = pct
                    ppl[d] = people
                break
    return out, ppl


def _parse_survey(text):
    """从正文里抽出机构/散户两套比例。

    返回 {'analyst': {...}, 'retail': {...}} 或 None（这篇没有调查数据）。

    ⚠ 不用固定措辞去切段，而是**逐句找**：
      一句话里同时有「分析师」主语 + ≥2 个百分比 → 分析师调查；
      有「散户/投票」主语 + ≥2 个百分比 → 散户调查。
      理由：四篇转载的句式各不相同（「13位分析师中」「本周共有12位分析师参与
      Kitco News黄金调查，其中…」），按文案切会漏。
    """
    ans = {'analyst': None, 'retail': None}

    for sent in BLOCK.findall(text):
        pcts = NUM_PAT.findall(sent)
        if len(pcts) < 2:
            continue
        is_a = bool(SUBJ_ANALYST.search(sent))
        is_r = bool(SUBJ_RETAIL.search(sent))
        if not is_a and not is_r:
            continue
        pct, ppl = _parse_side(sent)
        if sum(1 for v in pct.values() if v is not None) < 2:
            continue
        # 一块里两个主语都有时（「分析师…散户…」同段），分别解析两半：
        # 以第一个散户主语出现的位置为界切开，前半算分析师、后半算散户。
        if is_a and is_r:
            mr0 = SUBJ_RETAIL.search(sent)
            parts = [(sent[:mr0.start()], 'analyst'),
                     (sent[mr0.start():], 'retail')]
        else:
            parts = [(sent, 'analyst' if is_a else 'retail')]

        for chunk, key in parts:
            if ans[key] is not None:
                continue
            pct, ppl = _parse_side(chunk)
            if sum(1 for v in pct.values() if v is not None) < 2:
                continue
            nm = re.search(r'(\d+)\s*位分析师', chunk) if key == 'analyst' else \
                re.search(r'(\d+)\s*(?:票|名散户|名投资者|张)', chunk)
            ans[key] = {**pct, 'people': ppl,
                        'n': int(nm.group(1)) if nm else None,
                        'raw': chunk.strip()[:240]}

    if not ans['analyst'] and not ans['retail']:
        return None
    return ans


def _lean(pct):
    """由比例算一个 -100..+100 的偏向值，供画曲线。

    ⚠ 分母只算「有方向的」（up+down），不含 flat。
      理由：flat 是「看不清」，把它算进分母会把「分歧」和「无观点」混成一件事。
      但 flat 原值仍然存在输出里，页面要能看见。
    """
    if not pct:
        return None
    u, d = pct.get('up'), pct.get('down')
    if u is None or d is None or (u + d) == 0:
        return None
    return round((u - d) / (u + d) * 100, 1)


def load_old():
    try:
        with open(OUT, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    full = '--full' in sys.argv
    pages = MAX_PAGES
    if '--pages' in sys.argv:
        try:
            pages = int(sys.argv[sys.argv.index('--pages') + 1])
        except (IndexError, ValueError):
            pass

    old = {} if full else load_old()
    prev = {r['date']: r for r in (old.get('rows') or [])}
    print(f'已有 {len(prev)} 条历史调查')

    # ① 先翻列表，收集候选 URL
    cands = {}
    for p in range(1, pages + 1):
        try:
            arr = _list_page(CAT, p)
        except Exception as e:
            print(f'  列表 page {p} ✗ {type(e).__name__}')
            continue
        if not arr:
            print(f'  列表 page {p} 空 → 到底了')
            break
        for a in arr:
            t = (a.get('Title') or '').replace('\u3000', ' ').strip()
            if not CAND.search(t):
                continue
            d = _pdate(a.get('CreatedTime'))
            if d and d not in cands:
                cands[d] = {
                    'url': a.get('Url') or '',
                    'title': t,
                    'date': d,
                    'time': a.get('CreatedTime') or '',
                    'src': a.get('Source') or '',
                    'id': str(a.get('ContId') or ''),
                }
    print(f'候选（标题像调查）{len(cands)} 篇，其中 {len(prev)} 天已有')

    # ② 逐篇抓正文、解析。已有且非全量的跳过，省请求。
    rows, fails = {}, []
    for d in sorted(cands):
        c = cands[d]
        if not full and d in prev and prev[d].get('analyst'):
            rows[d] = prev[d]
            continue
        if not c['url']:
            continue
        try:
            text = _body(c['url'])
        except Exception as e:
            fails.append(f'{d}（{type(e).__name__}）')
            print(f'  ✗ {d} 正文 {type(e).__name__}')
            # 旧的留着，别因为这次失败把历史弄丢
            if d in prev:
                rows[d] = prev[d]
            continue
        got = _parse_survey(text)
        if not got:
            # 没解析出数字的（如 10-17 那篇确实转载了前一周的旧数）
            # **不写进结果**，也不当失败 —— 它本来就没有数据。
            print(f'  · {d} 无调查数据（标题像但正文没有）')
            if d in prev:
                rows[d] = prev[d]
            continue
        a, r = got['analyst'], got['retail']
        row = {
            'date': d,
            'url': c['url'],
            'title': c['title'],
            'src': c['src'],
            'analyst': a,
            'retail': r,
            'lean_a': _lean(a) if a else None,
            'lean_r': _lean(r) if r else None,
        }
        rows[d] = row
        print(f'  ✓ {d}  机构 '
              f'{a["up"] if a else "-"}/{a["down"] if a else "-"}/'
              f'{a["flat"] if a else "-"} '
              f'lean={row["lean_a"]}　散户 '
              f'{r["up"] if r else "-"}/{r["down"] if r else "-"}/'
              f'{r["flat"] if r else "-"} '
              f'lean={row["lean_r"]}')

    # ③ 落盘。按日期正序，前端好画
    out_rows = [rows[k] for k in sorted(rows)]
    days = [r['date'] for r in out_rows]
    payload = {
        'at': time.strftime('%Y-%m-%d %H:%M'),
        'source': '中金网「金市直播」转载 Kitco News 黄金周度调查',
        'cat': CAT,
        'freq': 'weekly',
        'errors': fails,
        'n': len(out_rows),
        'from': days[0] if days else None,
        'to': days[-1] if days else None,
        'rows': out_rows,
        'note': ('周频数据：Kitco 每周做一次调查，不是每天。'
                 '画到日频曲线上必须显示为台阶，不许插值。'),
    }
    os.makedirs(DATA, exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
    print(f'\n写入 {OUT}（{os.path.getsize(OUT)/1024:.0f} KB）')
    print(f'{len(out_rows)} 条周度调查，{payload["from"]} ~ {payload["to"]}')
    if fails:
        print(f'⚠ 失败：{", ".join(fails)}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
