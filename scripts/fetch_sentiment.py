#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场共识抓取 —— 「别人怎么看」这一层。

为什么单独一个脚本
──────────────────
现有全站都是「事实层」：份额、价格、净流入、两融。这些是**已经发生的事**。
OO 2026-11-13 要的是另一样东西：

  > 我还是会希望爬到网上对A股/恒生科技/黄金/美股的普遍判断

**判断不是事实。** 事实可以校验（份额涨了就是涨了），判断只能统计：
同一天有多少人在说同一句话、热度有多高、处在历史什么位置。
所以这一层的数据结构和事实层完全不同：不产生交易信号，只产生「共识读数」。

⚠ 四个必须写在前面的口径问题
──────────────────────────
① **热度绝对值没有意义，分位才有意义。**
   同花顺 `rate` 今天 66 万，明天 40 万，你没法判断「40 万算高还是低」。
   所以本脚本每天把热度存档，用**过去 N 天的经验分布**算分位。
   存不满 20 天之前，分位显示「样本不足」而不是编一个数出来。

② **「共识」不等于「正确」，也不等于「多数人赚钱」。**
   页面文案必须写成「今天大家在说 X」，不能写成「应该买 X」。
   这一条是本脚本所有 note 字段的写作纪律。

③ **多家媒体同一天发同一句 = 那天的共识。不是算法共识，是编辑共识。**
   标题里高频出现的词组反映的是**媒体选题的重合度**，它滞后于价格、
   领先于散户认知。它是个好东西，但它叫什么必须叫准。

④ **抖音这条路已经实测关闭，不要在这里偷偷重开。**
   2026-11-13 实测：抖音热榜免登录可抓 49 条，连抓 3 次共 147 条，
   **财经相关命中 0 条**；抖音搜索需登录（status_code 2483）；视频详情 403。
   所以「抖音财经博主的判断」这件事，只能退化成
   「关于他们的新闻」（靠东财搜索），不能假装抓到了他们的原话。

数据源（全部 2026-11-13 实测可用，详见 数据源实测清单.md）
────────────────────────────────────────────────────
  · 蛋卷基金  index_eva      → 63 个指数的 PE/PB 历史分位 + 股息率
  · 同花顺     hot_list      → 100 只热股（带 AI 归因）+ 行业/概念榜（带 ETF 代码）
  · 东财搜索   search/jsonp   → 财经媒体标题 + 摘要 + 日期 + 来源
  · 头条       pc/feed        → 财经博主原话标题（source 就是博主名）
  · 头条       hot-board      → 全民关注度（HotValue）
  · 东财股吧   list,zssh000300 → 散户原话

输出：data/sentiment.json
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fh          # 数据源健康上报
import _net         # 请求节流 + 账单

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
OUT = os.path.join(DATA, 'sentiment.json')

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

# 关注的标的 → 搜索关键词。顺序即页面顺序。
TOPICS = [
    ('a_share',     'A股',     ['A股', '沪指', '创业板指']),
    ('gold',        '黄金',    ['黄金', '金价']),
]

# 蛋卷里要盯的指数（名称必须与接口返回的 name 完全一致）
WATCH_INDEX = [
    '沪深300', '上证50', '中证500', '中证1000', '创业板', '科创50',
    '恒生科技', '恒生指数', '中证红利', '红利低波', '上证红利',
    '中证银行', '证券公司', '中证白酒', '中证医疗', '纳指100', '标普500',
]

# 热度分位需要的最小样本天数。不满就不给分位 —— 宁可说「样本不足」，
# 也不要用 3 天的数据算出一个看起来很确定的百分位。
MIN_SAMPLE_DAYS = 20
# 历史保留天数（用于算分位）
KEEP_DAYS = 120


# ────────────────────────────── 网络 ──────────────────────────────

def _get(url, referer=None, retries=3, timeout=20, accept='*/*'):
    """带节流和退避重试的 GET。

    ⚠ 返回 (状态码, 文本)。失败也返回文本形式的错误，不抛异常 ——
      本脚本有 6 个独立数据源，任何一个挂掉都不能拖垮其他的。
    """
    h = {'User-Agent': UA, 'Accept': accept,
         'Accept-Language': 'zh-CN,zh;q=0.9', 'Accept-Encoding': 'gzip, deflate'}
    if referer:
        h['Referer'] = referer
    last = None
    for i in range(retries):
        _net.throttle(url)
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                _net.mark(url)
                if r.headers.get('Content-Encoding') == 'gzip':
                    import gzip as _gz
                    raw = _gz.decompress(raw)
                return r.status, raw.decode('utf-8', 'ignore')
        except Exception as e:
            _net.mark(url)
            last = e
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
    return 'ERR', str(last)


def _json(text):
    """宽容地解析 JSON。失败返回 None，不抛。"""
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    # 东财 jsonp: cb({...})
    m = re.match(r'^\s*[A-Za-z_$][\w$]*\((.*)\)\s*;?\s*$', text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None


def _clean(s):
    """剥 HTML 标签和实体。标题里的 <em> 高亮必须去掉，否则上屏全是尖括号。"""
    s = re.sub(r'<[^>]+>', '', str(s or ''))
    s = (s.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<')
          .replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'"))
    return re.sub(r'\s+', ' ', s).strip()


# ──────────────────────── 0. 当天大盘：用事实锚住判断 ────────────────────────

def fetch_index_chg():
    """取创业板指最近一个交易日的涨跌幅（%）。

    ⚠ 为什么这一层必须去抓「事实」，而不是从舆论里推：
      2026-11-13 实测那天，媒体标题里 33/56 篇写的是
      「创业板指跌3%」「沪指跌0.56%」这类**指数级**涨跌 ——
      主题词表一个都命中不了（词表按板块组织，指数名被当纯主语排除）。
      于是「媒体主题方向」这一票只看见板块，得出「偏强」，
      而当天创业板指实际跌 3.32%。
      **一个说偏强、而当天指数跌 3% 的判断，逻辑再自洽也是误导。**
      所以这里直接从行情接口拿真实涨跌，作为一票把它摆到台面上。

    ⚠ 为什么用腾讯不用东财：2026-11-13 实测 push2his 的 kline 接口
      三次全部 `Remote end closed connection without response`（当天
      东财对境外 IP 间歇性掐断，fetch_index.py 头部也记了同一现象）。
      腾讯 qt.gtimg.cn 一次成功。这一层只需要「今天涨了还是跌了」，
      不需要历史序列，腾讯快照完全够用 —— 也就没必要去啃一个挂掉的接口。

    返回 (涨跌幅 float 或 None, 日期 str 或 None)
    """
    # 创业板指 399006（深市，代码前缀 sz）；读数在 "~" 分隔的第 32 位（涨跌幅%），
    # 第 30 位是涨跌额、第 13 位附近是时间戳。下标按腾讯的固定顺序取，
    # 不靠 find 找位置 —— 字段顺序是协议的一部分，稳定。
    st, txt = _get('https://qt.gtimg.cn/q=sz399006', referer='https://gu.qq.com/')
    if st == 'ERR' or '="' not in txt:
        return None, None
    try:
        body = txt.split('="', 1)[1].rstrip('";\n ')
        f = body.split('~')
        chg = float(f[32])
        ts = f[30]                      # 形如 20261113161406
        d = f'{ts[0:4]}-{ts[4:6]}-{ts[6:8]}'
        return round(chg, 2), d
    except Exception:
        return None, None


# ──────────────────────── 1. 蛋卷：分位基准 ────────────────────────

def fetch_valuation():
    """指数估值分位。这是全站唯一能回答「现在贵不贵」的源。

    ⚠ 蛋卷的 bond_yeild 字段恒为 0.05，是占位值不是 10 年国债收益率。
      不要拿它当无风险利率用 —— 那是编数据。
    """
    url = 'https://danjuanfunds.com/djapi/index_eva/dj'
    st, txt = _get(url, referer='https://danjuanfunds.com/')
    j = _json(txt)
    if not j or not (j.get('data') or {}).get('items'):
        _fh.report('valuations', '指数估值分位（蛋卷）', 'fail',
                   note=f'接口返回异常 HTTP {st}')
        return []

    items = j['data']['items']
    out = []
    for it in items:
        name = it.get('name') or ''
        if name not in WATCH_INDEX:
            continue
        pe = it.get('pe')
        out.append({
            'name': name,
            'code': it.get('index_code'),
            'pe': round(pe, 2) if isinstance(pe, (int, float)) and pe > 0 else None,
            'pe_pct': _pct(it.get('pe_percentile')),
            'pb': it.get('pb') if it.get('pb') else None,
            'pb_pct': _pct(it.get('pb_percentile')),
            'div_yield': _pct(it.get('yeild')),      # 股息率，0.0301 → 3.01
            'roe': _pct(it.get('roe')),
        })

    # 按 PE 分位升序：便宜的在前。没有分位的排最后。
    out.sort(key=lambda x: (x['pe_pct'] is None, x['pe_pct'] if x['pe_pct'] is not None else 999))

    got = len(out)
    want = len(WATCH_INDEX)
    d = (j['data'].get('items') or [{}])[0].get('date') or ''
    _fh.report('valuations', '指数估值分位（蛋卷）',
               'ok' if got >= want - 2 else ('partial' if got else 'fail'),
               last=None, got=got, want=want,
               note=f'估值日期 {d}，取到 {got}/{want} 个指数' if got else '一个指数都没匹配上')
    return out


def _pct(v):
    """0.654 → 65.4（百分位）。不是数字就返回 None。

    ⚠ 千万别在这里「补 0」或「猜一个」。分位缺失只说明这个指数没有历史序列，
      补出来的数字会直接变成买卖依据。
    """
    if isinstance(v, (int, float)):
        return round(float(v) * 100, 1)
    return None


# ─────────────────── 2. 同花顺：A股人气 + ETF 映射 ───────────────────

def fetch_hot_stocks():
    """热股榜 100 只 + AI 归因。"""
    url = ('https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock'
           '?stock_type=a&type=hour&list_type=normal')
    st, txt = _get(url)
    j = _json(txt)
    lst = ((j or {}).get('data') or {}).get('stock_list') or []
    if not lst:
        _fh.report('hot_stock', 'A股热股榜（同花顺）', 'fail', note=f'HTTP {st}，0 条')
        return []

    out = []
    for it in lst:
        tag = it.get('tag') or {}
        if not isinstance(tag, dict):
            tag = {}
        analyse = _clean(it.get('analyse'))
        out.append({
            'rank': it.get('order'),
            'name': it.get('name'),
            'code': it.get('code'),
            'chg': _round(it.get('rise_and_fall'), 2),
            'heat': _round(it.get('rate'), 0),
            'tags': tag.get('concept_tag') or [],
            'mark': tag.get('popularity_tag') or '',
            # 只有约 25% 的票有归因，有才放，别给空字符串占位
            'why': analyse if len(analyse) > 20 else '',
        })
    top_heat = out[0]['heat'] if out else 0
    with_why = sum(1 for x in out if x['why'])
    _fh.report('hot_stock', 'A股热股榜（同花顺）', 'ok',
               got=len(out), want=100,
               note=f'榜首 {out[0]["name"]}（热度 {top_heat:.0f}），{with_why} 只有 AI 归因')
    return out


def fetch_hot_plates():
    """行业/概念板块热榜。每条带 ETF 代码 —— 这是把「板块在热」翻译成「可买代码」的唯一接口。"""
    result = {}
    total_got = 0
    for ty, label in (('industry', '行业'), ('concept', '概念')):
        url = ('https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/plate'
               f'?type={ty}')
        st, txt = _get(url)
        j = _json(txt)
        pl = ((j or {}).get('data') or {}).get('plate_list') or []
        rows = []
        for it in pl:
            rows.append({
                'name': it.get('name'),
                'chg': _round(it.get('rise_and_fall'), 2),
                'heat': _round(it.get('rate'), 0),
                'etf': it.get('etf_name') or '',
                'etf_code': it.get('etf_product_id') or '',
            })
        result[ty] = rows
        total_got += len(rows)

    _fh.report('hot_plate', '板块热榜（同花顺）',
               'ok' if total_got >= 30 else ('partial' if total_got else 'fail'),
               got=total_got, want=40,
               note=f'行业 {len(result.get("industry") or [])} + 概念 {len(result.get("concept") or [])}'
                    + (f'，HTTP {st}' if not total_got else ''))
    return result


def _round(v, n):
    try:
        return round(float(v), n)
    except Exception:
        return None


# ─────────────────── 3. 东财搜索：媒体共识 ───────────────────

def fetch_news(keyword, page_size=20):
    """东财全站搜索。返回标题/摘要/来源/日期 + hitsTotal（话题热度）。"""
    param = json.dumps({
        'uid': '', 'keyword': keyword, 'type': ['cmsArticleWebOld'],
        'client': 'web', 'clientType': 'web', 'clientVersion': 'curr',
        'param': {'cmsArticleWebOld': {
            'searchScope': 'default', 'sort': 'default',
            'pageIndex': 1, 'pageSize': page_size,
        }},
    }, ensure_ascii=False)
    url = ('https://search-api-web.eastmoney.com/search/jsonp?cb=cb&param='
           + urllib.parse.quote(param))
    st, txt = _get(url, referer='https://so.eastmoney.com/')
    j = _json(txt)
    if not j:
        return None, []

    arts = ((j.get('result') or {}).get('cmsArticleWebOld')) or []
    out = []
    for a in arts:
        out.append({
            'title': _clean(a.get('title')),
            'digest': _clean(a.get('content'))[:120],
            'src': a.get('mediaName') or '',
            'date': a.get('date') or '',
            'url': a.get('url') or '',
        })
    return j.get('hitsTotal'), out


# ─────────────────── 4. 头条：博主原话 ───────────────────

def fetch_toutiao_feed(pages=2):
    """头条股票频道。source 字段就是博主/媒体名 —— 这是唯一能拿到「博主说了什么」的地方。

    ⚠ 实测只翻到约 20 条唯一标题就卡住（max_behot_time 递减但内容重复）。
      所以默认只取 2 页，不为了凑数量空转请求。
    """
    seen, out = set(), []
    bt = 0
    for i in range(pages):
        url = ('https://www.toutiao.com/api/pc/feed/?category=stock'
               f'&utm_source=toutiao&max_behot_time={bt}&tab=all')
        st, txt = _get(url, referer='https://www.toutiao.com/')
        j = _json(txt)
        ds = (j or {}).get('data') or []
        if not ds:
            break
        for d in ds:
            title = _clean(d.get('title'))
            if not title or title in seen:
                continue
            seen.add(title)
            out.append({
                'title': title,
                'digest': _clean(d.get('abstract'))[:100],
                'src': _clean(d.get('source')),
                'comments': d.get('comments_count'),
            })
        nbt = ds[0].get('behot_time') if ds else 0
        if not nbt or nbt == bt:
            break
        bt = nbt
    return out


# ─────────────────── 5. 头条热榜：全民关注度 ───────────────────

FIN_KW = ['股', '基金', '金价', '黄金', 'A股', '沪指', '创业板', '涨停', '跌停',
          '债', '央行', '降息', '加息', '美联储', '楼市', '房价', '经济', '通胀',
          '关税', '汇率', '美元', '人民币']


def fetch_toutiao_hot():
    """头条热榜 50 条，筛出财经条目。

    ⚠ HotValue 是平台内部的量纲，**不能跨平台比较**，只能同一平台跨时间比。
      所以这里只存原值 + 财经条目的相对位置，不做任何「热度占比」的推算。
    """
    url = 'https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc'
    st, txt = _get(url, referer='https://www.toutiao.com/')
    j = _json(txt)
    dl = (j or {}).get('data') or []
    if not dl:
        return [], None
    fin = []
    for i, d in enumerate(dl, 1):
        title = _clean(d.get('Title'))
        if any(k in title for k in FIN_KW):
            fin.append({'rank': i, 'title': title,
                        'heat': _round(d.get('HotValue'), 0)})
    top = _round(dl[0].get('HotValue'), 0) if dl else None
    return fin, top


# ─────────────────── 6. 股吧：散户原话 ───────────────────

def fetch_guba():
    """沪深300吧帖子标题。只对 zssh000300 有效，其他板块代码全是空页。

    ⚠ 2026-11-13 实测：短时间连跑两次，第二次返回空页。
      这是限流，不是「今天没帖子」。两者在页面上长得一样（都是 0 条），
      但含义完全相反 —— 一个是「散户不说话了」，一个是「我们没抓到」。
      所以这里对空页做退避重试；重试仍空，返回 0 并让调用方
      在体检里标 fail（_fh），而不是当成「情绪中性」。
    """
    url = 'https://guba.eastmoney.com/list,zssh000300.html'
    txt = ''
    for attempt in range(3):
        st, txt = _get(url, referer='https://guba.eastmoney.com/')
        if txt and len(txt) >= 5000:
            break
        if attempt < 2:
            time.sleep(2.0 + attempt * 2.0)   # 2s, 4s
    if not txt or len(txt) < 5000:
        return [], 0
    rows = re.findall(
        r'data-postid="(\d+)"[^>]*href="([^"]+)"[^>]*>([^<]{2,80})</a>', txt)
    out = []
    seen = set()
    for pid, href, title in rows:
        t = _clean(title)
        if not t or t in seen:
            continue
        seen.add(t)
        # 只留真正的股民帖（/news, 开头），财付号文章是媒体稿不是散户
        kind = 'post' if href.startswith('/news,') else 'media'
        out.append({'title': t, 'kind': kind,
                    'url': ('https://guba.eastmoney.com' + href) if href.startswith('/') else href})
    return out, len(out)


# ─────────────────── 共识计算 ───────────────────

# 情绪词表。⚠ 这是粗筛，不是语义理解 —— 页面上必须标「粗略」。
# 而且必须成对：只看「跌」不看「涨」会得到永远悲观的结果。
BULL = ['涨', '牛', '加仓', '抄底', '机会', '反弹', '起飞', '新高', '布局',
        '突破', '看多', '买入', '拉升', '涨停', '做多']
BEAR = ['跌', '崩', '跑路', '割肉', '惨', '亏', '凉', '垃圾', '骗', '危险',
        '减仓', '清仓', '看空', '卖出', '跌停', '跳水', '磨底', '阴跌', '下跌']


def mood_of(titles):
    """粗判多空。返回 {bull, bear, flat, bull_pct, bear_pct, n}。

    ⚠ 一个标题同时含「涨」和「跌」时算中性 —— 这比按出现次数相减更保守，
      因为「涨了就跌」这类标题两边都有词，相减会得出错误的偏向。
    """
    bull = bear = flat = 0
    for t in titles:
        b = sum(1 for k in BULL if k in t)
        s = sum(1 for k in BEAR if k in t)
        if b > s:
            bull += 1
        elif s > b:
            bear += 1
        else:
            flat += 1
    n = len(titles)
    if not n:
        return {'bull': 0, 'bear': 0, 'flat': 0, 'bull_pct': None,
                'bear_pct': None, 'n': 0}
    return {
        'bull': bull, 'bear': bear, 'flat': flat, 'n': n,
        'bull_pct': round(bull / n * 100, 0),
        'bear_pct': round(bear / n * 100, 0),
    }


# 叙事抽取时要去掉的「无信息高频词」。
# ⚠ 不除这些，每天的头号叙事都会是「A股」「今日」「市场」——
#   那不是共识，那是主语。
STOP = set('''A股 股市 今日 今天 明天 市场 指数 公司 股票 亿元 万元 记者 报道
日报 新闻 消息 早报 晚报 快讯 收盘 开盘 盘中 午评 收评 涨幅 跌幅 国内
中国 全球 环球 相关 表示 认为 分析 观点 数据 时间 目前 已经 继续 可能
进行 发布 披露 公告 我们 他们 你们 一个 这个 那个 什么 怎么样 如何
三大 集体 逆势 逆市 板块 个股 沪深 两市 成交额 换手率 市盈率 停牌
复盘 涨停板 龙虎榜 主力 资金 净流入 净流出 北向 南向 午后 早盘 尾盘
高开 低开 走强 走弱 拉升 回落 震荡 分化 普涨 普跌'''.split())

# 纯主语：指数名、板块名、地名。带这些词但没有任何判断的片段，一律不算「叙事」。
# ⚠ 这是第一版最大的漏洞：抽出来的 Top1 是「创业板指」——
#   它只回答了「在说谁」，没回答「说了什么」。主语不是主张。
SUBJECT = set('''创业板指 创业板 沪指 上证指数 深证成指 深成指 科创50 北证50
沪深300 中证500 中证1000 上证50 恒生指数 恒生科技 纳斯达克 标普500 道琼斯
银行 白酒 半导体 证券 保险 煤炭 电力 光伏 军工 医药 医疗 汽车 地产 房地产
黄金 白银 原油 铜 美元 人民币 美债 国债 油价 金价 现货黄金 期货黄金
算力 算力硬件 人工智能 AI 机器人 芯片 存储 光模块 稀土 有色 钢铁 化工'''.split())

# 「事件/判断」词：片段里必须至少含一个，否则它只是在说主语。
# 这一步是把「在说谁」升级成「说了什么」的唯一办法。
EVENT = ['涨', '跌', '破', '失守', '突破', '新高', '新低', '反弹', '回调',
         '下挫', '拉升', '涨停', '跌停', '跳水', '崩', '暴涨', '重挫', '走高',
         '走低', '收跌', '收涨', '翻绿', '翻红', '预警', '风险', '机会',
         '加仓', '减仓', '买入', '卖出', '看多', '看空', '降息', '加息',
         '宽松', '收紧', '超预期', '不及预期', '达成', '签署', '暂停', '恢复',
         '重组', '回购', '分红', '减持', '增持', '爆雷', '立案', '调查',
         '流入', '流出', '创新高', '创新低', '连涨', '连跌', '大涨', '大跌']


# ── 主题词表 ──
# 为什么需要人工词表：中文财经标题对同一个板块有 5 种写法
#   「算力硬件股集体下挫」「算力硬件概念集体下挫」「算力硬件股领跌」
#   「算力硬件方向领跌」「算力概念下挫」
# 精确短语匹配会把它们拆成 5 个各 1 家的孤岛，全被 min_src=3 滤掉。
# 只有把它们归到同一个「主题」下，才数得出「4 家媒体都在说算力硬件在跌」。
TOPIC_LEX = {
    '算力硬件': ['算力硬件', '算力概念', '算力', '光模块', 'CPO', '服务器', '交换机'],
    'AI应用':   ['AI应用', 'AI短剧', '短剧', 'AI视频', 'AI语料', '多模态', 'AI眼镜'],
    '芯片半导体': ['半导体', '芯片', '集成电路', '存储芯片', '晶圆', 'GPU', '光刻'],
    '人工智能': ['人工智能', 'AI', '大模型', '机器人', '智算'],
    '银行':     ['银行', '国有大行', '城商行'],
    '券商':     ['券商', '证券', '证券公司'],
    '保险':     ['保险', '险企'],
    '白酒消费': ['白酒', '消费', '食品饮料'],
    '影视传媒': ['影视', '院线', '传媒', '电影', '游戏'],
    '养殖农业': ['养殖', '猪肉', '生猪', '农业', '种业', '鸡'],
    '医药医疗': ['医药', '医疗', '创新药', 'CXO', '疫苗'],
    '新能源':   ['光伏', '锂电', '储能', '风电', '新能源'],
    '军工':     ['军工', '国防', '航天', '航空'],
    '有色资源': ['有色', '稀土', '铜', '铝', '锂矿', '黄金股'],
    '煤炭电力': ['煤炭', '电力', '火电', '水电', '核电'],
    '地产基建': ['地产', '房地产', '基建', '建筑'],
    '汽车':     ['汽车', '整车', '零部件', '智能驾驶'],
    '港股':     ['港股', '恒生', '恒指', '南向'],
    '黄金':     ['黄金', '金价', '现货黄金', '沪金', '贵金属'],
}

# 方向词 → 正负。⚠ 必须成对，只收「跌」不收「涨」会让每天看起来都在崩。
DIR_UP = ['涨', '走强', '领涨', '拉升', '翻红', '反弹', '走高', '新高', '大涨',
          '涨停', '爆发', '活跃', '强势', '上攻', '回暖', '上涨', '冲高',
          '创历史新高', '涨超', '涨幅居前', '逆市大涨', '逆势拉升', '站上', '收复',
          # ⚠ 这三个是配平补的。上面 DIR_DOWN 加了「失守/跌破/击穿」等 6 个
          #   价格下行说法，如果这边不补，词表就会系统性偏空 ——
          #   涨跌不对称的后果是「每天看起来都在跌」，而那不是市场，是我自己的词表。
          '突破', '创年内新高', '创阶段新高']
DIR_DOWN = ['跌', '走弱', '领跌', '下挫', '翻绿', '回落', '走低', '新低', '大跌',
            '跌停', '跳水', '承压', '低迷', '调整', '重挫', '下跌', '回调',
            '创历史新低', '跌超', '跌幅居前', '逆市下跌', '跌幅最大', '领跌两市',
            # ⚠ 这几个是 2026-11-13 实测补的：「现货黄金失守3870美元/盎司」
            #   被判成 flat，因为词表里没有「失守」。财经标题里「失守/跌破/击穿」
            #   是比「跌」更常用的价格下行说法，漏了它们，黄金那一栏
            #   会有 10/21 家明明在说跌却记为「没方向」。
            '失守', '跌破', '击穿', '探底', '创年内新低', '创阶段新低']


def _topic_spans(title):
    """找出标题里所有【主题词的出现位置】，并按「长词优先」消除重叠。

    ⚠ 为什么需要这一步（2026-11-13 实测发现）：
      「A股市场震荡调整 AI短剧概念再度走强」这一条标题，
      会同时命中 TOPIC_LEX 里的两个主题 ——
        · 'AI短剧'（属于「AI应用」）
        · 'AI'    （属于「人工智能」）
      两者在**同一个位置**上重叠。结果一条标题给两个主题各投一票涨，
      于是「偏涨主题 5 个」里其实有 2 个是同一件事数了两遍。

      这不是「多抽了一个主题」的小问题：pick_judgment 里
      「偏涨主题 5 个 vs 偏跌 1 个」是**第一票的唯一依据**。
      重复计数直接放大了这一票，5:1 的比分可能是 3:1 撑起来的。

    做法：把全部匹配按 (词长降序, 位置升序) 排，贪心接受互不重叠的。
      长词优先 = 具体词优先：「AI短剧」比「AI」具体，「黄金股」比「黄金」具体。
      不同位置上的两个主题仍然都保留 ——
      「银行板块逆市拉升 算力硬件下挫」确实在说两件事，不该被合并。
    """
    cand = []
    for topic, kws in TOPIC_LEX.items():
        for kw in kws:
            p = title.find(kw)
            # 同一个主题里只取最早出现的那一处；再往后找会让
            # 「黄金……黄金股……」这种标题在同一主题下挤占多个位置
            if p < 0:
                continue
            cand.append((topic, p, p + len(kw), kw))

    # 长词优先、同长度取更靠前的
    cand.sort(key=lambda c: (-(c[2] - c[1]), c[1]))

    taken = []          # 已接受的位置区间
    picked = []
    for topic, s, e, kw in cand:
        if any(not (e <= ts or s >= te) for ts, te in taken):
            continue    # 与已接受的区间重叠 → 这是同一个词的短写法，丢弃
        taken.append((s, e))
        picked.append((topic, s, e, kw))

    picked.sort(key=lambda c: c[1])
    return picked


def _topic_hits(title):
    """标题里出现了哪些主题 + 该句的方向。

    ⚠ 方向取**离主题最近**的那个方向词，不是整句多数票。
      实测错例：「银行板块逆势拉升 创业板指跌3.32% 算力硬件下挫」——
      整句里「跌」相关 3 个、「拉升」1 个，多数票会把银行判成跌。

    ⚠ 第二版错法：取主题前后 ±12 字的固定窗口，窗口内涨跌都算 → 判成 mix。
      根因是窗口是**对称**的，但中文财经标题的方向词几乎总在主题**之后**
      （「银行板块逆势拉升」），主题**之前**基本是定语（「今日银行板块」）。
      所以改成**非对称**：往前看 2 字（只为容纳「逆势」这类前缀），
      往后看 10 字，然后往后找**第一个**方向词就停 —— 第一个才是这句话的谓语。

    ⚠ 第三处：主题词本身可能含方向字。比如「养殖」不含，但「大跌」这类
      如果主题词里带了方向字，会把自己的名字当成谓语。
      所以搜索起点从**主题词结束位置**开始，不是从主题词开始位置。
    """
    out = []
    for topic, pos, end, _kw in _topic_spans(title):
        # ⚠ head 只取【主题词本身】，不取前面的字。
        #   实测错例：「A股三大指数收跌 影视板块逆市大涨 银行板块走强」
        #   —— 影视板块前面 2 字正好是「收跌」，被 head 采信后判成 down，
        #   而它真实的谓语是紧随其后的「大涨」。
        #   前缀（「逆势拉升银行」这种倒装）在中文财经标题里几乎不存在，
        #   为它开一个 ±2 字的口子，代价是把上一句的谓语借过来。
        #   **宁可漏掉罕见的倒装，也不能借错谓语** —— 漏了只是少一条，
        #   借错是给出反向结论。
        head = title[pos:end]
        tail = title[end:end + 12]

        ALL_UP = sorted(DIR_UP, key=len, reverse=True)
        ALL_DN = sorted(DIR_DOWN, key=len, reverse=True)

        d = None
        # 主题词本身带方向字的情况（如主题名含「涨/跌」）—— 当前词表没有，
        # 但要留这个分支，否则以后加词会悄悄出错
        for w in ALL_UP:
            if w in head:
                d = 'up'
                break
        if d is None:
            for w in ALL_DN:
                if w in head:
                    d = 'down'
                    break
        # 再往后找**第一个**方向词 —— 第一个才是这句话的谓语。
        # ⚠ 长词优先：否则「大涨」里的「涨」会和「回调」里的判断抢位置。
        if d is None:
            best_p, best_d, best_len = -1, None, 0
            for w, dd in [(w, 'up') for w in ALL_UP] + [(w, 'down') for w in ALL_DN]:
                p = tail.find(w)
                if p < 0:
                    continue
                if best_p < 0 or p < best_p or (p == best_p and len(w) > best_len):
                    best_p, best_d, best_len = p, dd, len(w)
            d = best_d or 'flat'

        out.append((topic, d))
    return out


def consensus_of(news_lists, min_src=3, topn=6):
    """抽「今天媒体在说哪些事，以及说的方向对不对得上」。

    做法（第三版）：**按主题聚合**，不按字面短语聚合。

    ⚠ 前两版都错在同一个地方：以为「同一句话」会字面相同。
      实际中文财经标题对同一件事有 5 种写法（见 TOPIC_LEX 上方的说明），
      精确匹配会把它们拆成孤岛；n-gram 滑窗又会产出「业板指跌」这种错位碎片。
      **正确做法是先归到主题，再数主题。**

    输出每条：{text, sources, dir, up, down, examples}
      text     主题名（人工可读）
      sources  提到该主题的不同媒体家数
      dir      'up' / 'down' / 'mix' / 'flat'（按家数投票）
      examples 原始标题，供人复核 —— 这个算法本质是规则统计，一定会错
    ⚠ 第四处（2026-11-13 实测发现，最严重）：**分母不一致**。
      原写法 `sources = len(所有提到该主题的家数)`，但 `dir` 是只在
      「说涨」和「说跌」的家数之间比出来的。于是页面上出现了这样一行：

          黄金　　在跌　　21 家

      看着像「21 家媒体说黄金在跌」，实际是「21 家提到黄金，
      其中 15 家说跌、1 家说涨、10 家根本没方向」。
      10 家没方向的被算进了「在跌」的分母里。

      这比数字错更糟：**它让一个 15 票的结论看起来像 21 票**，
      而且读者无从发现 —— 除非自己去数原始标题。
      所以现在把三个数分开报：说涨几家、说跌几家、提到共几家。
      页面必须显示「15 家说跌 / 21 家提到」，不能只显示一个数。
    """
    from collections import defaultdict

    # ⚠ 关键：每家媒体在同一主题上只能落进**一个**方向。
    #   第一版是「把来源塞进 up 集和 down 集」，结果同一家媒体
    #   （证券时报网既发了「金价跌」又发了「法兴唱多黄金」）
    #   会同时进两个集合，于是出现「说跌 17 + 无方向 10 = 27 > 提到 21」——
    #   分项之和大于总量，读者一加就发现对不上。
    #   现在改成先统计每家的涨/跌篇数，再按多数把它归到一个方向，**保证分项之和 == 总量**。
    # 两层聚合：topic -> src -> 该源在这个主题上的涨/跌篇数
    # ⚠ 中间这层必须存在。直接 `set.add(src)` 到 up 和 down 两个集合里，
    #   同一家媒体发过一篇涨一篇跌时会被两边都算一遍，分项之和就大于总量。
    #   加一层 per-src 计数，才能把「一家媒体」压成「一个方向」。
    two = defaultdict(lambda: defaultdict(lambda: {'up': 0, 'down': 0, 'flat': 0,
                                                   'titles': []}))
    for arts in news_lists:
        for a in arts:
            title = a.get('title') or ''
            src = a.get('src') or '?'
            for topic, d in _topic_hits(title):
                cell = two[topic][src]
                cell[d] += 1
                if len(cell['titles']) < 4 and title not in cell['titles']:
                    cell['titles'].append(title)

    rows = []
    for topic, by_src in two.items():
        n = len(by_src)
        if n < min_src:
            continue
        # 每家媒体归一个方向：篇数多的那边赢；打平或全 flat → flat
        u = dn = fl = 0
        titles = []
        for src, cell in by_src.items():
            if cell['up'] > cell['down']:
                u += 1
            elif cell['down'] > cell['up']:
                dn += 1
            else:
                fl += 1
            for t in cell['titles']:
                if len(titles) < 4 and t not in titles:
                    titles.append(t)
        assert u + dn + fl == n, f'{topic}: 分项和 {u+dn+fl} != 总量 {n}'

        if u > dn:
            dr = 'up'
        elif dn > u:
            dr = 'down'
        else:
            dr = 'mix' if (u or dn) else 'flat'
        rows.append({
            'text': topic,
            'sources': n,          # 提到该主题的家数（总量）
            'n_up': u,             # 归为「说涨」的家数
            'n_down': dn,          # 归为「说跌」的家数
            'n_flat': fl,          # 提到了但没方向的家数
            'n_dir': u + dn,       # 有方向的家数（这才是 dir 的真正分母）
            'dir': dr,
            'up': u,
            'down': dn,
            # 排序用：有方向的家数优先，其次总量。
            # ⚠ 不用总量排序：一个 21 家提到、只 1 家有方向的模糊主题，
            #   不该压过 5 家提到、5 家全在说跌的清晰主题。
            'score': (u + dn) * 2 + n * 0.1,
            'examples': titles[:2],
        })
    rows.sort(key=lambda r: -r['score'])
    return rows[:topn]


def pick_judgment(cons, mood, vals, heat_pct, index_chg=None):
    """把一堆读数压成**一句话判断**。

    OO 2026-11-13 的原话是「需要，我要一个判断」——
    她要的不是词云，是一个能说的结论。

    ⚠ 这个函数是全站最危险的地方：它在下判断。
      所以三条纪律：
      ① 只用**已有读数**推，不引入任何主观权重（权重全部写在明面上）
      ② 必须同时给出**支撑证据**和**反面证据**，只给支持自己结论的证据就是话术
      ③ 明确写出「这是在描述市场，不是建议你买卖」——她的框架里
         「只动新增不动存量」，任何看起来像指令的东西都会被误读成指令
    """
    ev = []       # 证据（按影响力排序）
    anti = []     # 反面证据

    # 1. 媒体在说什么（最直接）
    if cons:
        top = cons[0]
        dmap = {'up': '在涨', 'down': '在跌', 'mix': '分歧', 'flat': '没方向'}
        # ⚠ 这里必须报「有方向的家数 / 提到该主题的家数」两个数。
        #   只报 sources 会出现「21 家媒体在说黄金在跌」这种假精确 ——
        #   实际是 21 家提到、16 家说跌、4 家没方向（见 consensus_of 注释）。
        # ⚠ 而且「其余几家」不能一律写成「没给方向」：算力硬件那天是
        #   3 家说跌、2 家说涨，写「其余 2 家没给方向」就是直接把反方抹掉了。
        #   反方存在时必须点名，否则证据只剩半边。
        dn_of_top = top.get('n_down') or 0
        up_of_top = top.get('n_up') or 0
        fl_of_top = top.get('n_flat') or 0
        dirn = max(dn_of_top, up_of_top)
        if dirn:
            parts = [f'{dirn} 家媒体在说「{top["text"]}」{dmap[top["dir"]]}']
            rest = []
            other = up_of_top if top['dir'] == 'down' else dn_of_top
            if other:
                rest.append(f'{other} 家说'
                            + ('涨' if top['dir'] == 'down' else '跌'))
            if fl_of_top:
                rest.append(f'{fl_of_top} 家没给方向')
            ev.append(parts[0] + f'（共 {top["sources"]} 家提到'
                      + ('，' + '、'.join(rest) if rest else '') + '）')
        else:
            ev.append(f'{top["sources"]} 家媒体提到「{top["text"]}」，但都没给方向')

        n_up = sum(c.get('n_up') or 0 for c in cons)
        n_dn = sum(c.get('n_down') or 0 for c in cons)
        if n_dn > n_up:
            ev.append(f'A股热门主题合计：说跌 {n_dn} 家次、说涨 {n_up} 家次')
        elif n_up > n_dn:
            ev.append(f'A股热门主题合计：说涨 {n_up} 家次、说跌 {n_dn} 家次')
        # 方向与价格背离，是最值得说的一条
        if top['dir'] == 'up' and n_dn > n_up:
            anti.append('最热的主题在涨，但合计起来多数家次在说跌 —— 这是分化不是普涨')

    # 2. 散户情绪（反向指标，但要谨慎表述）
    g = mood.get('guba') or {}
    g_n = g.get('n') or 0
    g_bull = g.get('bull_pct')
    g_bear = g.get('bear_pct')
    if g_n:
        # ⚠ 中性占比通常极高（实测 45/58 = 78%），因为「功亏一篑」这类
        #   短标题没有任何情绪词。所以这里报的是「在 58 条里能被判出方向的
        #   只有 13 条，其中 11 条看空」—— 分母必须写清，否则 19% 看着很低，
        #   实际是「有明确态度的里 85% 看空」。
        decided = (g.get('bull') or 0) + (g.get('bear') or 0)
        if decided:
            ratio = (g['bear'] / decided * 100)
            if ratio >= 70:
                ev.append(f'股吧 {g_n} 条帖子里有明确方向的只有 {decided} 条，'
                          f'其中 {g["bear"]} 条看空（占 {ratio:.0f}%）')
                anti.append('散户一边倒看空在历史上既出现在底部也出现在下跌中继，'
                            '单看这一条不能当反向信号用')
            elif ratio <= 30:
                ev.append(f'股吧 {g_n} 条帖子里有明确方向的只有 {decided} 条，'
                          f'其中 {g["bull"]} 条看多（占 {100 - ratio:.0f}%）')
            else:
                ev.append(f'股吧 {g_n} 条帖子里有明确方向的 {decided} 条，'
                          f'看空 {g["bear"]}、看多 {g["bull"]}')

    # 3. 估值分位（唯一有历史基准的）
    if vals:
        cheap = [v for v in vals if v.get('pe_pct') is not None and v['pe_pct'] <= 20]
        rich = [v for v in vals if v.get('pe_pct') is not None and v['pe_pct'] >= 80]
        if cheap:
            names = '、'.join(f'{v["name"]}({v["pe_pct"]:.0f}%)' for v in cheap[:3])
            ev.append(f'估值处在历史低分位：{names}')
        if rich:
            names = '、'.join(f'{v["name"]}({v["pe_pct"]:.0f}%)' for v in rich[:3])
            ev.append(f'估值处在历史高分位：{names}')
            anti.append('高分位不等于会跌，低分位不等于会涨 —— 分位只说明贵不贵，不说明方向')

    # 4. 热度分位
    hp = (heat_pct or {}).get('top_heat') or {}
    if hp.get('pct') is not None:
        p = hp['pct']
        if p >= 80:
            ev.append(f'个股热度处在近 {hp["n"]} 天的 {p:.0f}% 分位（很拥挤）')
        elif p <= 20:
            ev.append(f'个股热度处在近 {hp["n"]} 天的 {p:.0f}% 分位（很冷清）')
        else:
            ev.append(f'个股热度处在近 {hp["n"]} 天的 {p:.0f}% 分位')

    if not ev:
        return {'stance': '样本不足', 'line': '今天没有足够的数据形成判断。',
                'evidence': [], 'anti': [], 'note': '',
                'votes': []}

    # ⚠ 缺票必须记下来，不能悄悄少一票。
    #   2026-11-13 实测：股吧被限流返回 0 条，于是「散户情绪」这一票直接消失，
    #   判断从「四票」变成「三票」，但页面上的措辞完全没变 ——
    #   读者会以为「今天散户没表态」，实际是「我们没抓到」。
    #   更糟的是结论会变：股吧那天 11/13 条看空，少了这一票，
    #   「分化」的成因就说不清了。
    missing = []

    # ── 结论：三票制（主题方向 / 散户情绪 / 估值分位），每票写明来源 ──
    votes = []
    if cons:
        # ⚠ 第一票原来数的是「偏涨的主题有几个」，2026-11-13 改成**数家数**。
        #   为什么改：主题个数是个偷懒的代理变量。实测那天
        #   「偏涨主题 5 个 vs 偏跌 1 个」→ 判偏强，但把家数摊开看是
        #   15 家说涨 vs 3 家说跌；更要紧的是，一条标题命中两个语义重叠主题时
        #   （「AI短剧」同时算 AI应用 和 人工智能），个数会被同一件事灌两票。
        #   **数「谁在说」比数「说了几件事」接近真实。**
        #
        # ⚠ 但这仍是**跨主题累加**，同一家媒体可能既说银行涨又说算力跌，
        #   在两个主题里各被算一次。所以这里的单位是「家·主题」，
        #   不是「家」。文案必须写「家次」，不能写「家」——
        #   写「15 家说涨」会让人以为有 15 家不同的媒体，实际可能只有 8 家。
        n_up = sum(c.get('n_up') or 0 for c in cons)
        n_dn = sum(c.get('n_down') or 0 for c in cons)
        if n_up or n_dn:
            v = 1 if n_up > n_dn else (-1 if n_dn > n_up else 0)
            votes.append(('媒体主题方向', v,
                          f'说涨 {n_up} 家次 / 说跌 {n_dn} 家次'
                          f'（同一家在不同主题各算一次）'))
        else:
            votes.append(('媒体主题方向', 0, '媒体只提名了板块，没给出方向'))
    if g_n:
        decided = (g.get('bull') or 0) + (g.get('bear') or 0)
        if decided:
            v = 1 if (g.get('bull') or 0) > (g.get('bear') or 0) else -1
            votes.append(('股吧散户方向', v,
                          f'看多 {g.get("bull")} 条 / 看空 {g.get("bear")} 条'))
        else:
            missing.append('股吧今天抓到了帖子，但没有一条带明确多空态度')
    else:
        # ⚠ 「没抓到」和「抓到了但都中性」是两件事，必须分开报。
        #   前者是数据缺失（限流/改版），后者是市场真的没表态。
        missing.append('股吧今天没抓到帖子（限流或改版），散户情绪这一票缺席')
    if vals:
        avg = [v['pe_pct'] for v in vals if v.get('pe_pct') is not None]
        if avg:
            m = sum(avg) / len(avg)
            v = -1 if m >= 60 else (1 if m <= 30 else 0)
            votes.append(('估值分位中枢', v, f'{len(avg)} 个指数平均 {m:.0f}% 分位'))
    else:
        missing.append('指数估值一个都没取到，估值这一票缺席')

    # ⚠ 第四票：当天大盘实际涨跌。
    #   为什么必须补这一票（2026-11-13 实测发现的硬伤）：
    #   那天创业板指实际跌 3.32%，但前三票算出「媒体偏强 +1」——
    #   因为媒体标题里 33/56 篇讲的是「创业板指跌3%」「沪指跌0.56%」这类
    #   **指数级涨跌**，主题词表一个都命中不了（词表里没有「创业板指」这个主题，
    #   它被当成纯主语排除了）。
    #   于是「板块词表」只看见 5 家说算力跌、15 家说银行/养殖涨，
    #   得出「偏强」，而当天大盘是跌的。
    #   **这不是算法错误，是视角缺失**：板块热度 ≠ 大盘方向。
    #   一个说「偏强」而当天指数跌 3% 的判断，不管内部逻辑多自洽，都是误导。
    #   所以补一票把「今天到底涨没涨」摆进来，让它和前三票并列投票。
    if index_chg is not None:
        v = 1 if index_chg > 0.3 else (-1 if index_chg < -0.3 else 0)
        votes.append(('当天大盘涨跌', v,
                      f'创业板指 {index_chg:+.2f}%（±0.3% 内算中性）'))
        if v < 0 and cons:
            _nu = sum(c.get('n_up') or 0 for c in cons)
            _nd = sum(c.get('n_down') or 0 for c in cons)
            if _nu > _nd:
                anti.append(
                    f'媒体在说板块「偏涨」（{_nu} 家次 vs {_nd} 家次），'
                    f'但当天创业板指跌 {abs(index_chg):.2f}% —— '
                    f'板块热度和大盘方向对着走，说明涨的是少数板块，'
                    f'不是市场整体在涨')

    score = sum(v for _, v, _ in votes)

    # 一致 (3:0) → 强；多数 (2:1) → 偏；平票或全 0 → 分化
    n_pos = sum(1 for _, v, _ in votes if v > 0)
    n_neg = sum(1 for _, v, _ in votes if v < 0)
    span = max(n_pos, n_neg, 1)

    if n_pos == len(votes) and votes:
        stance = '一致偏强'
    elif n_neg == len(votes) and votes:
        stance = '一致偏弱'
    elif n_pos >= 2 and n_pos > n_neg:
        stance = '偏强'
    elif n_neg >= 2 and n_neg > n_pos:
        stance = '偏弱'
    else:
        stance = '分化'

    # 一句话必须说清「几票对几票」，否则「分化」只是个模糊词
    tally = f'{n_pos} 票偏强 / {n_neg} 票偏弱 / {len(votes) - n_pos - n_neg} 票中性'
    # ⚠ 不要写死「三个维度」。票数是会变的（2026-11-13 从 3 票加到 4 票），
    #   写死数字那天就会跟实际票数对不上，而这种错最难被自己发现 ——
    #   页面看起来一切正常，只有数票的人才知道少了一句。
    n_txt = f'{len(votes)} 个维度'
    line = {
        '一致偏强': f'{n_txt}全指向正面（{tally}）。今天的信息是一边倒的。',
        '一致偏弱': f'{n_txt}全指向负面（{tally}）。今天的信息是一边倒的。',
        '偏强': f'多数维度偏正面（{tally}）。',
        '偏弱': f'多数维度偏负面（{tally}）。',
        '分化': f'各维度指向不一致（{tally}），没有统一方向 —— 这是分化，不是趋势。',
    }[stance]

    # 缺的那几票写进结论，不藏在数据里
    if missing:
        line += '（注意：' + '；'.join(missing) + '）'
        for m in missing:
            anti.append(m)

    return {
        'stance': stance,
        'line': line,
        'evidence': ev,
        'anti': anti,
        'votes': [{'name': n, 'v': v, 'why': w} for n, v, w in votes],
        'missing': missing,
        'note': '这是在描述市场，不是买卖建议。你的框架是「只动新增、不动存量」，'
                '这个读数只用来决定新增往哪边偏，不构成对存量仓位的任何指令。',
    }


# ─────────────────── 主题词表覆盖率 ───────────────────

def _topic_coverage(news):
    """每条新闻里有多少篇被主题词表识别到。

    ⚠ 这个数为什么必须算、必须上屏：
      2026-11-13 实测：A股 56 篇里只有 23 篇命中主题（41%），
      未命中的 33 篇几乎全是「创业板指跌3%」「沪指跌0.56%」这种
      **指数级涨跌**——词表按板块组织，指数名一个都没收。

      也就是说「媒体主题方向」这一票，是在四成样本上得出的。
      如果不把这个覆盖率摆出来，读者会以为「5 家媒体说算力硬件在跌」
      就是当天媒体的全部意见；实际它只覆盖了四成报道，
      而剩下六成讲的是「今天大盘跌了」。

      **覆盖率低的结论不是错，是不能单独当结论用。**
    """
    out = {}
    for key, arts in (news or {}).items():
        n = len(arts or [])
        if not n:
            out[key] = {'n': 0, 'hit': 0, 'pct': None}
            continue
        hit = sum(1 for a in arts if _topic_hits(a.get('title') or ''))
        out[key] = {'n': n, 'hit': hit, 'pct': round(hit / n * 100, 0)}
    return out


# ─────────────────── 热度历史 & 分位 ───────────────────

def load_history():
    """读历史存档。分位全靠它。"""
    try:
        with open(OUT, encoding='utf-8') as f:
            j = json.load(f)
        return j.get('history') or []
    except Exception:
        return []


def save_heat_day(hist, today, snap):
    """把今天的热度读数追加进历史（同一天覆盖，不重复追加）。"""
    hist = [h for h in hist if h.get('d') != today]
    hist.append({'d': today, **snap})
    hist.sort(key=lambda h: h.get('d') or '')
    # 只留最近 KEEP_DAYS 天
    if len(hist) > KEEP_DAYS:
        hist = hist[-KEEP_DAYS:]
    return hist


def percentile_of(hist, key, value):
    """value 在历史分布里的分位（0–100）。样本不足返回 None。

    ⚠ 这是全脚本最需要诚实的地方：
      · 样本 < MIN_SAMPLE_DAYS → 返回 None，页面显示「样本不足（N/20 天）」
      · 不要用「当前值 / 历史最大值」冒充分位，那是最大值占比不是分位
      · 分母用「严格小于等于的天数」，不是「全部天数」再乘什么系数
    """
    if value is None:
        return None, 0
    vals = [h.get(key) for h in hist if isinstance(h.get(key), (int, float))]
    n = len(vals)
    if n < MIN_SAMPLE_DAYS:
        return None, n
    if n == 0:
        # ⚠ 这行在 MIN_SAMPLE_DAYS > 0 时永远走不到，但必须留：
        #   反向验证时把门槛改成 0 才暴露出「除零崩」。
        #   一个只在「有人改了上面那个常数」时才炸的分支，正是最该写死的分支。
        return None, 0
    le = sum(1 for v in vals if v <= value)
    return round(le / n * 100, 0), n


# ─────────────────── 主流程 ───────────────────

def main():
    today = date.today().isoformat()
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'▶ 抓取市场共识　{today}')

    # 1. 估值分位
    vals = fetch_valuation()
    print(f'  ✓ 指数估值 {len(vals)} 个')

    # 2. 热股 / 板块
    stocks = fetch_hot_stocks()
    plates = fetch_hot_plates()
    print(f'  ✓ 热股 {len(stocks)} 只　板块 {len(plates.get("industry") or [])}'
          f'+{len(plates.get("concept") or [])}')

    # 3. 东财搜索（每个标的一组关键词）
    news = {}
    hits = {}
    all_news = []
    for key, label, kws in TOPICS:
        merged, total_hit = [], 0
        for kw in kws:
            h, arts = fetch_news(kw)
            if h:
                total_hit = max(total_hit, int(h))
            merged.extend(arts)
        # 按标题去重（多个关键词会搜到同一篇）
        seen, dedup = set(), []
        for a in merged:
            if a['title'] in seen:
                continue
            seen.add(a['title'])
            dedup.append(a)
        dedup.sort(key=lambda x: x.get('date') or '', reverse=True)
        news[key] = dedup
        hits[key] = total_hit or None
        all_news.extend(dedup)
        print(f'  ✓ [{label}] 东财搜索 {len(dedup)} 篇（hitsTotal {total_hit}）')

    # 4. 头条 feed + 热榜
    feed = fetch_toutiao_feed(pages=2)
    hot_fin, hot_top = fetch_toutiao_hot()
    print(f'  ✓ 头条 feed {len(feed)} 条　热榜财经 {len(hot_fin)} 条')

    # 5. 股吧
    guba, guba_n = fetch_guba()
    guba_posts = [x for x in guba if x['kind'] == 'post']
    print(f'  ✓ 股吧 {guba_n} 条（其中散户帖 {len(guba_posts)}）')

    # ── 共识 ──
    # 只用东财搜索的标题算叙事（它有明确的「来源」字段，能判断是不是多家）
    # ⚠ 不把股吧和头条 feed 混进来：股吧是匿名散户、头条 feed 是自媒体，
    #   混在一起统计「几家媒体说同一句话」会造成假共识。
    cons = {}
    for key, label, _ in TOPICS:
        cons[key] = consensus_of([news.get(key) or []])
    print(f'  ✓ 共识：A股 {len(cons.get("a_share") or [])} 条　'
          f'黄金 {len(cons.get("gold") or [])} 条')

    # ── 情绪 ──
    mood = {
        'guba': mood_of([x['title'] for x in guba_posts]),
        'feed': mood_of([x['title'] for x in feed]),
    }

    # ── 热度存档 + 分位 ──
    hist = load_history()
    snap = {
        'top_heat': (stocks[0]['heat'] if stocks else None),
        'top_name': (stocks[0]['name'] if stocks else None),
        'hot_count': len(stocks),
        'toutiao_top': hot_top,
        'toutiao_fin': len(hot_fin),
        'guba_posts': len(guba_posts),
        'feed': len(feed),
        'hit_a': hits.get('a_share'),
        'hit_gold': hits.get('gold'),
        'bear_guba': mood['guba']['bear_pct'],
        'bull_guba': mood['guba']['bull_pct'],
    }
    hist = save_heat_day(hist, today, snap)

    pct = {}
    for k in ('top_heat', 'toutiao_fin', 'guba_posts', 'hit_a', 'hit_gold',
              'bear_guba', 'bull_guba', 'feed'):
        v, n = percentile_of(hist[:-1], k, snap.get(k))   # 不含今天，避免自己跟自己比
        pct[k] = {'pct': v, 'n': n, 'val': snap.get(k)}

    days_have = len([h for h in hist if h.get('d') != today])
    上屏分位 = {k: v for k, v in pct.items() if v['pct'] is not None}
    print(f'  ✓ 历史样本 {days_have} 天　可算分位 {len(上屏分位)}/{len(pct)} 项')

    # ── 一句话判断 ──
    # 当天大盘（事实层，用于给判断加一票「锚」）
    idx_chg, idx_date = fetch_index_chg()
    if idx_chg is None:
        print('  ⚠ 创业板指涨跌没取到（判断里少一票，不算错但不完整）')
    else:
        print(f'  ✓ 创业板指 {idx_chg:+.2f}%　{idx_date}')

    judgment = pick_judgment(cons.get('a_share') or [], mood, vals, pct, idx_chg)
    print(f'  ✓ 判断：{judgment["stance"]}　{judgment["line"][:40]}')

    # ── 市场预判层（对未来的一致性，不是对今天的描述）──
    # ⚠ 这是 OO 2026-11-14 明确要的东西：
    #   「我不需要给我结论市场在说今天的涨跌，
    #     我需要给我市场对未来的预判的一致性。」
    #   它和上面的 consensus 是**两种语料**，不是同一个东西换个写法：
    #   consensus 读的是「今天大家在说什么」（收评/午评/盘中播报），
    #   forecast 读的是「大家预测接下来会怎么走」（研报/策略会/机构研判）。
    #
    # ⚠ 它失败不影响主流程。少一栏比整页失败好，但必须在页面上写出缺。
    forecast = {}
    try:
        import forecast as _F
        for key, label, _ in TOPICS:
            try:
                r = _F.collect(key)
                r['headline'] = _F.headline(r)
                r['caveats'] = _F.caveats(r)
                forecast[key] = r
                print(f'  ✓ [{label}] 预判 {r["n_kept"]} 篇'
                      f'（窗口 {r["window_from"]}~{r["window_to"]}）'
                      f'　{r["lean_txt"]}')
            except Exception as e:
                print(f'  ⚠ [{label}] 预判抓取失败：{type(e).__name__}: {e}')
    except Exception as e:
        print(f'  ⚠ 预判层整个不可用：{type(e).__name__}: {e}')

    # ── 体检上报 ──
    for key, label, kws in TOPICS:
        arts = news.get(key) or []
        _fh.report(f'news_{key}', f'{label}舆论（东财搜索）',
                   'ok' if len(arts) >= 5 else ('partial' if arts else 'fail'),
                   got=len(arts), want=20,
                   note=(f'{len(arts)} 篇，最新 '
                         f'{arts[0]["date"][:10] if arts and arts[0].get("date") else "无日期"}'
                         if arts else '一条都没搜到'))
    _fh.report('guba', '股吧散户情绪（东财）',
               'ok' if guba_posts else 'fail',
               got=len(guba_posts), want=None,
               note=f'{len(guba_posts)} 条散户帖' if guba_posts else '空页，未取到')
    _fh.report('toutiao_feed', '财经自媒体（头条）',
               'ok' if feed else 'fail', got=len(feed), want=20,
               note=f'{len(feed)} 条' if feed else '0 条')

    payload = {
        'at': now,
        'date': today,
        'valuations': vals,
        'hot_stocks': stocks,
        'plates': plates,
        'news': news,
        'hits': hits,
        'consensus': cons,
        'judgment': judgment,
        'mood': mood,
        'toutiao_feed': feed,
        'toutiao_hot_fin': hot_fin,
        'guba': guba_posts,
        'heat': snap,
        'percentile': pct,
        'history_days': days_have,
        'min_sample': MIN_SAMPLE_DAYS,
        # 当天大盘涨跌：这是这一层唯一的事实锚。
        # 单独一个字段而不是塞进 heat —— 因为它的性质不同：
        # heat 是「多少人在讨论」，这个是「价格到底怎么走了」。
        'index_chg': idx_chg,
        'index_chg_date': idx_date,
        # 主题词表覆盖率：命中的新闻占比。这是「媒体主题方向」这一票的置信度。
        # ⚠ 必须上屏。实测 2026-11-13 只有 41%（23/56），意味着
        #   近六成报道没被词表读到，那一票的结论是建立在四成样本上的。
        #   不写这个数，读者会以为 5 家媒体就是全部意见。
        'topic_cov': _topic_coverage(news),
        # 市场预判层：对「接下来会怎么走」的一致性。
        # ⚠ 和 consensus 并列而不是替换 —— 两者回答的是不同问题：
        #   consensus = 今天媒体在说什么（已经发生）
        #   forecast  = 机构预测接下来会怎样（还没发生）
        #   前者可以验证，后者只能统计。混在一起会让后者的「预测性」伪装成事实。
        'forecast': forecast,
    }

    os.makedirs(DATA, exist_ok=True)
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({**payload, 'history': hist}, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, OUT)

    sz = os.path.getsize(OUT)
    print(f'\n=== 完成 === {OUT}  {sz/1024:.0f} KB')
    print(f'  估值 {len(vals)}　热股 {len(stocks)}　'
          f'新闻 {sum(len(v) for v in news.values())}　feed {len(feed)}　'
          f'股吧 {len(guba_posts)}　历史 {days_have} 天')
    return 0


if __name__ == '__main__':
    sys.exit(main())
