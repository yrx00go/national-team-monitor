#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""散户声音：从新浪股吧抓「我看多 / 我看空」投票帖。

═══════════════════════════════════════════════════════════════════
★ 为什么要单独写这个文件（前情，别跳过）

预判层（forecast.py）搜的全是媒体库和研报库，抓到的「机构一致偏多」
没有信息量 —— 机构本来就不发看空报告。OO 原话：
    「搜索到的都是证券 + 机构，机构都不会空啊，不要这样，
      多找找野生散户的看法；看看能不能找到」

为了找散户，实测了 14 条路，全部失败或不可用：
    东财搜索（各种散户关键词）→ 全是媒体稿，换词无效
    东财股吧列表页            → 「身份核实」人机验证页
    gbapi.eastmoney.com       → 403
    雪球热帖 API              → 阿里云 WAF 壳页
    微博 m.weibo.cn API       → Sina Visitor System
    百度贴吧 / 知乎            → 403
    韭研公社 / 淘股吧 / 股吧移动版 → 纯 JS 壳，无 SSR 数据
    今日头条 category=stock   → ✅ 能拿到博主名（枫叶/淡定zcl/…），
                                但只有 19 条唯一标题，behot_time 翻不动

★★★ 唯一可用的是「新浪股吧」：guba.sina.com.cn
    - 返回 gb18030 编码的完整 SSR 页面，每页 40–50 条
    - 有作者名、时间、回复数
    - 能一直翻页（实测 24 页仍有新增，无上限）
    - 5 个宽基指数吧 × 15 页 ≈ 2600 条帖

★ 但股吧标题**不能直接做情感分析**。实测两次：
  第一版（宽词表：涨/跌/垃圾/完蛋…）→ 识别率 35%，但方向错了：
      「外围涨是一点不跟」判成看多（其实是不满）
      「你不会想把指数拉红吧」判成看多（其实是讽刺）
      「砸破8000抄底」判成看多（其实是看空）
      根因：短标题里「涨」这个字高频出现在**抱怨句**里，不是方向词。
  第二版（只认预测句式：看多/抄底/会涨…）→ 识别率跌到 5%，而且仍有反讽。

★★★ 结论：股吧短标题做不了「方向统计」。所以本文件**不做**那种统计。

★ 唯一干净的是股吧自己的**投票模板**：
      「我看多 上证指数(SH000001)[我」
      「我看空 上证指数(SH000001)[我」
  这是新浪提供的按钮，用户点一下就发一条，标题是系统生成的。
  **语义 100% 无歧义**，不存在反讽。这是散户自己点的票，不是我猜的。

  实测密度：每抓 400 条帖能拿到 15–20 条投票（3–5.5%）。
  5 个指数吧 × 15 页 ≈ 2600 帖 → 约 130 条投票。

★ 口径纪律（写进页面，不许省略）
  1. 只统计投票模板，不统计自由发言 —— 自由发言只做样本展示，不计数
  2. 按**独立作者**去重：同一人一天点 5 次看空，只算 1 人
  3. 样本量必须显示出来。130 条投票不等于「市场情绪」
  4. 样本 < MIN_VOTE 时只报数、不给倾向词
  5. 这是「谁在股吧点按钮」，不是「散户仓位」，更不是「明天涨跌」
═══════════════════════════════════════════════════════════════════
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')

UA = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36'),
    'Referer': 'https://guba.sina.com.cn/',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}

# 只取宽基指数吧。个股吧的投票问的是「这只股」，不是「大盘」。
BARS = [
    ('sh000001', '上证指数'),
    ('sz399001', '深证成指'),
    ('sz399006', '创业板指'),
    ('sh000905', '中证500'),
    ('sh000016', '上证50'),
]

PAGES = 28          # 每吧页数。实测 28 页约 400 条/吧（后段重复率高），5 吧合计约 2800 条
WINDOW_DAYS = 30    # 只统计近 30 天的投票。⚠ 试过 7 天：只有 46 人，太薄
MIN_VOTE = 30       # 少于这么多条投票就不给倾向词（只报数）

# 一行帖子的结构。新浪股吧是 SSR 表格，字段顺序稳定：
#   点击量 / 回复数 / 标题链接 / 作者 / 时间
# ⚠ 两列顺序踩过坑：表头是「点击 | 回复」，第一列是**点击**不是回复。
#   一开始把第一列当回复数，结果样本里出现「4638 条回复」这种数字 ——
#   一个散户帖不可能有 4638 条回复，那是阅读量。
#   别改成「按 class 找」：class 只有 tr / tr2 两种且交替出现，没有语义。
ROW = re.compile(
    r'<tr class="[^"]*">\s*'
    r'<td><span[^>]*>(\d+)</span></td>\s*'          # 1 点击量（不是回复数）
    r'<td><span[^>]*>(\d+)</span></td>\s*'          # 2 回复数
    r'<td>\s*<a href="(/[^"]*thread[^"]*)"[^>]*>(.*?)</a>.*?'  # 3 链接 4 标题
    r'<div class="author"><a href=\'[^\']*\' title=\'([^\']*)\'>.*?</div>.*?'  # 5 作者
    r'<td>([^<]*)</td>',                            # 6 时间
    re.S)

# 新浪的投票模板。必须整条匹配 —— 只要标题里出现「看多」就判，
# 会把「我看空，但XX看多」这类自由发言也算进来。
VOTE = re.compile(r'^我看(多|空)\s')

# 标题里的尾巴噪音：投票帖带「上证指数(SH000001)[我」这类后缀，
# 展示时要剥掉，否则列表里全是重复的括号
NOISE = re.compile(r'\s*[（(][A-Z]{2}\d{4,6}[)）]|\[我\]?$|\s+$')

# ── 机器发的帖子，不是散户说的话 ──
# 实测「中证500」吧里混进大量系统播报，标题长这样：
#   【中证500股指期货(IC)主力合约日内涨超1%】
#   【又一外资巨头宣布加仓中国资产】富达国际
#   这些是新浪自己推的行情快讯，算进「散户在喊什么」就是张冠李戴。
# ⚠ 只用于**样本展示**过滤，投票帖不受影响（投票帖标题格式固定、
#   且人点不了机器帖的投票按钮）。
BOT = re.compile(r'^(【|中证500股指期货|沪深300股指期货|上证50股指期货)')

# 作者名里带这些的也是官方号，不是散户
BOT_AUTHOR = ('新浪财经', '新浪财经客户端', '证券之星', '财联社', '每日经济新闻',
              '中国证券报', '上海证券报', '证券时报', '东方财富')


def get(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('gb18030', 'ignore')


def parse_time(raw, today):
    """把股吧的时间写法转成 date。转不了返回 None（不猜）。

    实测见过的四种写法：
        「3分钟前」「2小时前」  → 今天
        「今天09:43」           → 今天
        「11月14日」            → 今年的 11-14（股吧不显示年份）
        「2026-11-14 09:43」    → 直接解析
    ⚠ 「11月14日」不含年份。跨年时会错（1 月看到「12月30日」其实是去年），
      所以这里加一条：算出来的日期比今天晚 30 天以上，就减一年。
    """
    t = (raw or '').strip()
    if not t:
        return None
    if '分钟前' in t or '秒前' in t or '小时前' in t or t.startswith('今天'):
        return today
    if t.startswith('昨天'):
        return today - timedelta(days=1)
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', t)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            return None
    m = re.match(r'(\d{1,2})月(\d{1,2})日', t)
    if m:
        try:
            d = datetime(today.year, int(m.group(1)), int(m.group(2))).date()
        except ValueError:
            return None
        # ★ 跨年：股吧不显示年份，「12月03日」在 11 月看到只能是**去年**的。
        #
        # ⚠⚠ 这里原来写的是 `if d > today + timedelta(days=30)`（晚 30 天以上才减年），
        #   这是个假的宽松阈值，害处不是「少算了几天」，是**去年 12 月的帖子
        #   被当成今年 12 月**，然后因为「今年 12 月」还 >= 窗口起点，直接进了
        #   「近 30 天」的统计和样本展示。
        #
        #   实测（2026-11-16 抓到的 12 条样本）：12月03日 / 12月04日 ×2 / 11月25日
        #   共 4 条是未来日期 —— 今天是 11-16，12 月还没到。而这 4 条「未来帖」
        #   在页面上看起来完全正常，只是日期比今天晚，不仔细看根本发现不了。
        #
        #   判据改成：**只要算出比今天晚，就一定是上一年的**（帖子不可能来自未来）。
        #   不能写成 `d > today` 减年再返回 —— 1 月 3 日看到「12月30日」是对的，
        #   减年也没问题；但 12 月 30 日看到「01月03日」应该减年，d > today 却不成立…
        #   所以还要配合下面 main() 里的窗口上界一起看：真正该拦的是
        #   「进窗口之后仍是未来日期」，而那条在 main() 里用 d > today 直接剔掉。
        if d > today:                        # 帖子不会来自未来 → 是去年的
            try:
                d = d.replace(year=d.year - 1)
            except ValueError:               # 2 月 29 日
                return None
        return d
    return None


def clean_title(t):
    return NOISE.sub('', re.sub(r'<[^>]+>', '', t)).strip()


def crawl_bar(bar, label, pages=PAGES):
    """抓一个吧的帖子。返回 (帖子列表, 是否被挡)。"""
    out, seen = [], set()
    for p in range(1, pages + 1):
        try:
            html = get(f'https://guba.sina.com.cn/?s=bar&name={bar}&num=80&page={p}')
        except Exception as e:
            if p == 1:
                return [], f'{label} 第 1 页就失败：{type(e).__name__}'
            break
        # 反爬兜底：正常页面 40 KB 以上，被挡时会是很短的验证页
        if len(html) < 8000:
            if p == 1:
                return [], f'{label} 返回 {len(html)} 字节，疑似被挡'
            break
        rows = ROW.findall(html)
        if not rows:
            if p == 1:
                return [], f'{label} 解析出 0 行，页面结构可能变了'
            break
        new = 0
        for click, rep, href, title, author, tm in rows:
            tid = re.search(r'tid=(\d+)', href)
            tid = tid.group(1) if tid else None
            if not tid or tid in seen:
                continue
            seen.add(tid)
            out.append({
                'bar': label,
                'tid': tid,
                'title': clean_title(title),
                'author': (author or '').strip(),
                'time_raw': (tm or '').strip(),
                'reply': int(rep),      # 回复数（第二列）
                'click': int(click),    # 点击量（第一列）
            })
            new += 1
        if new == 0:      # 翻不动了
            break
        time.sleep(0.3)   # 别把人家站点打疼
    return out, None


def main():
    today = datetime.now().date()
    since = today - timedelta(days=WINDOW_DAYS - 1)

    all_posts, notes = [], []
    for bar, label in BARS:
        posts, err = crawl_bar(bar, label)
        if err:
            notes.append(err)
            print(f'  ✗ {err}')
            continue
        all_posts.extend(posts)
        print(f'  ✓ {label}: {len(posts)} 条')

    # ── 只留窗口内、且时间解析得出来的 ──
    # ⚠ 窗口是**闭区间，两端都要卡**。原来只有 `d < since` 一个下界，
    #   上界靠 parse_time 自觉 —— 而 parse_time 的跨年阈值曾经放过未来日期
    #   进来（见那个函数的注释）。两处都得有，任何一个漏了都会让
    #   「去年 12 月的帖子」混进「近 30 天」的统计里，而且不会有任何报错。
    inwin = []
    undated = 0
    future = 0
    for p in all_posts:
        d = parse_time(p['time_raw'], today)
        if d is None:
            undated += 1
            continue
        if d < since or d > today:
            if d > today:
                future += 1      # 不该发生；真发生了就是 parse_time 又放水了
            continue
        p['date'] = d.isoformat()
        inwin.append(p)

    # ── 投票帖：只认系统模板，只按独立作者计数 ──
    votes = []
    for p in inwin:
        m = VOTE.match(p['title'])
        if not m:
            continue
        votes.append({'lean': 'up' if m.group(1) == '多' else 'down',
                      'author': p['author'], 'bar': p['bar'], 'date': p['date'],
                      'title': p['title'], 'time_raw': p['time_raw']})

    # ★ 去重口径：一个人一天只算一票。
    #   理由：同一个人 10 分钟内点三次看空，是情绪，不是三个人的判断。
    #   （实测「天朗气清的伊芙」一小时内点了 4 次看空 —— 不去重会把这个
    #     人的情绪放大成 4 个人的共识。）
    uniq = {}
    for v in votes:
        k = (v['author'], v['date'])
        # 同一天同一人既有看多又有看空 → 记 mixed，两个方向都不加
        if k in uniq and uniq[k]['lean'] != v['lean']:
            uniq[k]['lean'] = 'mixed'
        elif k not in uniq:
            uniq[k] = v
    people = list(uniq.values())
    n_up = sum(1 for v in people if v['lean'] == 'up')
    n_dn = sum(1 for v in people if v['lean'] == 'down')
    n_mx = sum(1 for v in people if v['lean'] == 'mixed')
    n_bot = sum(1 for p in inwin
                if BOT.match(p['title']) or any(a in p['author'] for a in BOT_AUTHOR))
    # ⚠ 分母口径：mixed（同一天既点看多又点看空）**不进分母** ——
    #   他两边都点了，说明他自己没定，拿他算任何一边都是替人表态。
    decided = n_up + n_dn

    # ── 倾向词：样本不够就不给 ──
    if decided < MIN_VOTE:
        lean, lean_txt = 'insufficient', '样本不足'
        note = (f'近 {WINDOW_DAYS} 天只收到 {decided} 个独立散户的明确表态，'
                f'不到 {MIN_VOTE} 个就不给倾向词 —— 分母撑不起结论时给词，等于用噪声冒充判断。')
    else:
        r = n_dn / decided
        if r >= 0.65:
            lean, lean_txt = 'strong_down', '散户明显偏空'
        elif r >= 0.55:
            lean, lean_txt = 'down', '散户略偏空'
        elif r <= 0.35:
            lean, lean_txt = 'strong_up', '散户明显偏多'
        elif r <= 0.45:
            lean, lean_txt = 'up', '散户略偏多'
        else:
            lean, lean_txt = 'mixed', '散户分歧'
        note = (f'近 {WINDOW_DAYS} 天 {decided} 个独立散户点了按钮：'
                f'{n_dn} 人看空 / {n_up} 人看多。')

    # ── 自由发言样本：**不统计方向**，只给人读 ──
    #    实测这批短标题做不了方向统计（反讽太多），所以这里只当「今天散户
    #    在喊什么」的样本，挑回复最多的几条 —— 回复多代表共鸣多。
    chat = [p for p in inwin
            if not VOTE.match(p['title'])
            and not BOT.match(p['title'])
            and not any(a in p['author'] for a in BOT_AUTHOR)]
    # 按点击量排：股吧短帖的回复数大多是 0，用回复排等于没排。
    # 点击量代表「多少人真的看了」，更接近「这话有多少共鸣」。
    chat.sort(key=lambda x: -x['click'])
    samples = [{'title': p['title'][:60], 'bar': p['bar'],
                'time': p['time_raw'], 'reply': p['reply'], 'click': p['click']}
               for p in chat[:12] if 4 <= len(p['title']) <= 60]

    out = {
        'at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'date': today.isoformat(),
        'source': '新浪股吧（上证指数 / 深证成指 / 创业板指 / 中证500 / 上证50）',
        'window_days': WINDOW_DAYS,
        'window_from': since.isoformat(),
        'window_to': today.isoformat(),
        'n_post': len(inwin),
        'n_post_raw': len(all_posts),
        'n_bot': n_bot,             # 机器帖（行情快讯/官方号），样本展示时剔掉
        'n_undated': undated,
        'n_future': future,         # 解析成未来日期后被剔掉的条数（正常应为 0）
        'n_vote_post': len(votes),
        'n_people': decided,
        'n_up': n_up,
        'n_down': n_dn,
        'n_mixed': n_mx,
        'lean': lean,
        'lean_txt': lean_txt,
        'note': note,
        'min_vote': MIN_VOTE,
        'by_bar': {lab: sum(1 for v in people if v['bar'] == lab) for _, lab in BARS},
        'samples': samples,
        'errors': notes,
    }
    return out


def merge_into_sentiment(retail):
    """写进 sentiment.json 的 retail 字段。

    ⚠ 不动其它字段。fetch_sentiment.py 每次会重写整个文件，
      所以它跑完之后必须再跑一次本脚本（见 update.py 的调用顺序）。
    """
    p = os.path.join(DATA, 'sentiment.json')
    if not os.path.exists(p):
        print(f'✗ {p} 不存在，先跑 fetch_sentiment.py')
        return False
    # 读-改-写会丢掉文件里的其它内容吗？不会 —— 这里只加一个键。
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    d['retail'] = retail
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, separators=(',', ':'))
    print(f'✓ 已写入 {p} 的 retail 字段')
    return True


if __name__ == '__main__':
    print(f'抓新浪股吧，窗口 {WINDOW_DAYS} 天，每吧 {PAGES} 页')
    r = main()
    print()
    print(f"帖子 {r['n_post']} 条（原始 {r['n_post_raw']}，无日期 {r['n_undated']}）")
    print(f"投票帖 {r['n_vote_post']} 条 → 去重后 {r['n_people']} 人明确表态"
          f"（看空 {r['n_down']} / 看多 {r['n_up']}），另有 {r['n_mixed']} 人两面都点")
    print(f"读数：{r['lean_txt']} —— {r['note']}")
    if r['errors']:
        print('失败：', '；'.join(r['errors']))
    if '--no-write' not in sys.argv:
        merge_into_sentiment(r)
