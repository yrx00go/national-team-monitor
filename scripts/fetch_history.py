#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史份额 + 价格 一次性回补

起始日由 START 控制（当前 2020-01-02，覆盖 2020 年至今）。
上交所 ETF 份额公示逐日可查，实测能回溯到 2017 年甚至更早（本次回补已验证：
2020-01-02 返回 169 只，7 只标的命中 6 只 —— 科创50 尚未上市）。
价格走东方财富，可达上市首日；腾讯接口硬上限 640 根，已弃用。
      注：价格用前复权，ETF 分红会导致价格断层，qfq 已做平滑，与份额曲线叠加才可比

⚠ 各标的上市时间不同，直接统一 START 会造成「组合成员数量变化」的假台阶：
     510050/510300/510500/512100/510880/515080  2020-01-02 即有
     588000 科创50   2020-11-16 上市
     159545 港股通红利低波  2024-04-15 上市
   compute_demo.py 必须显式标注每只的可用起点，不能静默拼接。
"""
import json, os, re, sys, time, urllib.request
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fh          # 数据源健康上报，见 _fh.py 开头的说明
import _td          # 交易日历 + 北京时钟，见 _td.py 开头的说明
import _net         # 请求节流 + 账单，见 _net.py 开头的说明

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
os.makedirs(DATA, exist_ok=True)

START = date(2020, 1, 2)

# (指数, 代码, 简称, 交易所)
WATCH = [
    ('上证50',   '510050', '50ETF', 'SSE'),
    ('沪深300',  '510300', '300ETF华泰柏瑞', 'SSE'),
    ('中证500',  '510500', '500ETF南方', 'SSE'),
    ('中证1000', '512100', '1000ETF南方', 'SSE'),
    ('科创50',   '588000', '科创50ETF华夏', 'SSE'),
    ('红利',     '510880', '红利ETF华泰柏瑞', 'SSE'),
    ('红利',     '515080', '中证红利ETF招商', 'SSE'),
    ('创业板',   '159915', '创业板ETF易方达', 'SZSE'),
    ('港股红利', '159545', '港股通红利低波ETF', 'SZSE'),
    ('沪深300',  '159919', '300ETF嘉实', 'SZSE'),
]

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}


def _get(url, referer, timeout=20, retries=5):
    """带退避重试的 GET。

    ⚠ 2026-11-12 实测踩到：这次 CI 里 `fetch_price_bfq.py` 抓到 8/10，
       而这里同一批标的、同一个接口、同样参数，**0/10**。
       对比两边代码，URL 参数一模一样，差别只有一个：
         fetch_price_bfq 重试 4 次（0.8 / 2 / 4 秒）
         这里重试 3 次（1 / 2 秒）
       东财对境外 IP 的掐断是**间歇性**的（不是按 URL 大小区分，实测同一请求
       有时通有时不通，前后差不到 1 秒），所以「多试一次」直接决定成败。
       3 次成功概率约 70%，5 次约 90% —— 这就是 8/10 和 0/10 的全部原因。

       现在统一到 5 次，退避 1/2/4/8 秒（最长约 15 秒 + 5×timeout）。
       `--refresh-prices` 是每天全量重抓，等于每天赌一次，必须把胜率拉高。
    """
    last = None
    for i in range(retries):
        _net.throttle(url)      # 见 _net.py：每次重试前都过闸门，不只是第一次
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(url, headers={**UA, 'Referer': referer}),
                timeout=timeout).read()
            _net.mark(url)
            return r
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(min(1.0 * (2 ** i), 8.0))
    raise last


def fetch_sse(d):
    url = ('http://query.sse.com.cn/commonQuery.do?'
           f'sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&STAT_DATE={d}')
    js = json.loads(_get(url, 'http://www.sse.com.cn/').decode('utf-8', 'ignore'))
    out = {}
    for x in js.get('result') or []:
        if x.get('STAT_DATE') == d:
            out[x['SEC_CODE']] = float(x['TOT_VOL'])
    return out


def fetch_szse_snapshot():
    url = 'http://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1945&TABKEY=tab1'
    raw = _get(url, 'http://www.szse.cn/')
    if b'PK' not in raw[:4]:
        return {}
    import io, zipfile
    z = zipfile.ZipFile(io.BytesIO(raw))
    sh = z.read([n for n in z.namelist() if 'worksheets' in n][0]).decode('utf-8')
    out = {}
    for rm in re.finditer(r'<row[^>]*>(.*?)</row>', sh, re.S):
        cells = []
        for cm in re.finditer(r'<c[^>]*>(.*?)</c>', rm.group(1), re.S):
            t = re.search(r'<t[^>]*>(.*?)</t>', cm.group(1), re.S)
            v = re.search(r'<v>(.*?)</v>', cm.group(1), re.S)
            cells.append(t.group(1) if t else (v.group(1) if v else ''))
        if len(cells) < 4 or not cells[0].strip().isdigit():
            continue
        try:
            out[cells[0].strip()] = float(cells[3].replace(',', '')) / 10000.0
        except ValueError:
            continue
    return out


def fetch_price(code, market, fqt='qfq', beg='20190101'):
    """东方财富日线。fqt='qfq' 前复权 / 'bfq' 不复权。

    ⚠ 原来用腾讯 web.ifzq.gtimg.cn，但它硬上限 640 根（传 2500 直接返回空），
    只能回溯到 2024-03，撑不住 2020 年起的区间。换东财后可达上市首日。
    已验证：510880 前复权序列与腾讯重叠 751 天，中位差 0.0000%、最大差 0.177%。

    beg 参数是 2026-11-12 下午加的：日常增量只需要近 40 天，
    传一个近的起点能把响应体从 1900 根降到 ~30 根，境外 IP 被掐断的概率明显下降。
    不传时仍是 20190101（回补 / 强制全量走这条）。
    """
    secid = ('1.' if market == 'SSE' else '0.') + code
    fq = 1 if fqt == 'qfq' else 0
    url = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?'
           f'secid={secid}&fields1=f1,f2&fields2=f51,f53&klt=101&fqt={fq}'
           f'&beg={beg}&end={_td.bj_today():%Y%m%d}')
    js = json.loads(_get(url, 'https://quote.eastmoney.com/').decode('utf-8', 'ignore'))
    kl = (js.get('data') or {}).get('klines') or []
    # ⚠ 原来这里是 `if p[0] >= today: continue` —— 无条件丢弃当日。
    #   本意是防盘中半日价（对），但把「今天已收盘」和「今天正在盘中」
    #   当成同一件事，于是收盘后跑也照样扔。
    #   实测后果：CI 在**北京 18:38** 跑，收盘价已定稿 3 小时 38 分，
    #   末日期仍然永远停在 T-1 → 页面天天「落后 1 天」，且永远修不好。
    #   改由 _td.accept_day() 判断「这一根定稿了没有」，而不是「是不是今天」。
    out = []
    for s in kl:
        p = s.split(',')
        if len(p) < 2 or not _td.accept_day(p[0]):
            continue
        out.append((p[0], float(p[1])))
    return out


def main():
    start = START
    end = _td.bj_today()

    # ---- 调度：价格段 / 份额段各自可单独跑 ----
    #
    # ⚠⚠ 2026-11-12 晚：这两段被拆开，因为【共用同一个出口 IP 的配额】。
    #   它们读写的文件本来就不重叠（prices.json / shares.json），
    #   唯一的问题是：价格段打东财 push2his（该域名大约容忍十几次请求），
    #   份额段打上交所（另一个域名，不抢东财配额）。放在同一个 job 里，
    #   价格段永远排在后面、永远抢不到配额 —— 实测 1/10。
    #
    #   拆开之后各拿一份 IP，互不影响。
    #   不带任何开关时仍然是「两段都跑」，所以本地手动回补的行为不变。
    shares_only = '--shares-only' in sys.argv
    prices_only = '--prices-only' in sys.argv
    if shares_only and prices_only:
        raise SystemExit('✗ --shares-only 和 --prices-only 不能同时用')

    if not shares_only:
        _fetch_prices(start, end)
    else:
        print('\n价格：跳过（--shares-only）')

    if not prices_only:
        _fetch_shares(start, end)
    else:
        print('\n份额：跳过（--prices-only）')


def _fetch_prices(start, end):
    """价格段：前复权收盘价（画曲线用）。

    ⚠ 2026-11-12 晚从 main() 抽出来，为的是能单独跑。
    """
    px_fp = os.path.join(DATA, 'prices.json')
    prices = json.load(open(px_fp, encoding='utf-8')) if os.path.exists(px_fp) else {}

    # ---- 价格 ----
    #
    # ⚠⚠ 2026-11-12 晚：这两段（价格 / 份额）的先后顺序被【调换过】。
    #   原来是「份额 → 价格」。当时看着合理（份额是主数据），实际是个陷阱：
    #   份额段按日历天算缺口，要打 846 次上交所请求；
    #   等它打完，同一个 IP 紧接着去打东财，就是 0/10。
    #
    #   而份额段根本没必要排在前面 —— 它和价格段读写的文件完全不重叠
    #   （shares.json / prices.json），谁先谁后不影响结果。
    #   既然不互相依赖，就该让【请求数少的那个先跑】：
    #   价格段 10 次（增量模式），份额段最多几百次。
    #   先跑 10 次的，它面对的是一个干净 IP；跑完再让份额段去消耗配额。
    #
    #   一句话：**互不依赖的两步，顺序不该按「谁重要」排，该按「谁会污染谁」排。**
    #   这条同时解释了为什么 fetch_price_bfq（第 2 位跑）7/10、
    #   而这里（第 6 位跑）0/10 —— 当时我以为是重试次数问题，其实一半是排队位置。
    #
    # ⚠ 2026-11-12 发现的问题：这段原来是「起点够早就跳过」，
    #   于是回补一次成功后，它【永远不会再更新】—— prices.json 会永久停在回补那天。
    #   而这正是页面画价格曲线用的数据（前复权），停更了却没有任何提示。
    #   修法一（当天上午）：加 --refresh-prices 无条件全量重抓。
    #   修法二（当天下午，就是下面这段）：不再无条件全量。
    #
    # ⚠⚠ 为什么放弃了「每天全量」：
    #   实测当天 CI 里 fetch_price_bfq 抓到 8/10、这里 0/10。两边 URL 参数一模一样，
    #   差别只有重试次数。而 --refresh-prices 是【每天】赌一次 10 只 × 1900 根，
    #   任何一次全挂，价格曲线就停一天。**把「偶发失败」变成了「每天一次风险」。**
    #
    # 新做法，分两步：
    #   第一步：只抓最近 40 天（数据量减少约 95%，掐断概率大幅下降）。
    #   第二步：拿这 40 天和旧数据【重叠区逐日比对】——
    #           数值对得上 → 说明没分红除权，把新的接上去就行（增量，够用）；
    #           数值对不上 → 说明发生过除权重算，必须【全量重抓整段】。
    #
    # 为什么这个判据是对的：前复权序列一旦某只分红，历史值会整体重算，
    # 同一天会出现两个价。所以「重叠区是否吻合」正是除权的直接指纹 ——
    # 不需要猜哪天分红，让数据自己说。
    refresh = '--refresh-prices' in sys.argv

    NEAR_DAYS = 40      # 增量窗口：覆盖约 26 个交易日，足够容错节假日
    print('\n价格：' + ('（强制全量刷新）' if refresh else '（增量 + 重叠校验）'))
    ok_codes, bad_codes, full_codes = [], [], []   # 本次抓到的 / 失败的 / 被迫全量的

    def _tail_start(seq):
        """旧序列里，从那一天起的尾巴（用来做重叠比对）。"""
        return {d: c for d, c in seq[-NEAR_DAYS:]}

    for idx, code, name, mk in WATCH:
        need = start.isoformat()
        have = prices.get(code) or []

        # 没有旧数据、或旧数据起点不够早 → 只能全量（这是回补，不是日常）
        if not have or have[0][0] > need:
            try:
                p = fetch_price(code, mk)
                prices[code] = [[d, c] for d, c in p]
                ok_codes.append(code)
                full_codes.append(code)
                print(f'  {code} {name} 全量 {len(p)} 根  {p[0][0]} → {p[-1][0]}')
            except Exception as e:
                bad_codes.append(code)
                print(f'  {code} 失败 {e}')
            time.sleep(0.3)
            continue

        if refresh:
            try:
                p = fetch_price(code, mk)
                prices[code] = [[d, c] for d, c in p]
                ok_codes.append(code)
                full_codes.append(code)
                print(f'  {code} {name} 全量 {len(p)} 根  {p[0][0]} → {p[-1][0]}')
            except Exception as e:
                bad_codes.append(code)
                print(f'  {code} 失败 {e}')
            time.sleep(0.3)
            continue

        # 日常路径：先抓近端一小段
        try:
            tail = fetch_price(code, mk, beg=(_td.bj_today()
                                  - timedelta(days=NEAR_DAYS * 2)).strftime('%Y%m%d'))
        except Exception as e:
            bad_codes.append(code)
            print(f'  {code} 近端失败 {e}（旧数据保留，不做任何写入）')
            time.sleep(0.3)
            continue

        if not tail:
            bad_codes.append(code)
            print(f'  {code} 近端返回空（疑似接口改版），旧数据保留')
            time.sleep(0.3)
            continue

        # 重叠校验：同一个日期，两边的前复权价应该完全相等
        old_tail = _tail_start(have)
        overlap = [(d, c, old_tail[d]) for d, c in tail if d in old_tail]
        diff = [x for x in overlap if abs(x[1] - x[2]) > 1e-6]

        if not overlap:
            # 完全没重叠（停机太久）。这时也走全量，稳妥。
            try:
                p = fetch_price(code, mk)
                prices[code] = [[d, c] for d, c in p]
                ok_codes.append(code)
                full_codes.append(code)
                print(f'  {code} {name} 无重叠 → 全量 {len(p)} 根')
            except Exception as e:
                bad_codes.append(code)
                print(f'  {code} 失败 {e}')
            time.sleep(0.3)
            continue

        if diff:
            # ★ 重叠区对不上 = 发生过除权，历史必须整段重算
            try:
                p = fetch_price(code, mk)
                prices[code] = [[d, c] for d, c in p]
                ok_codes.append(code)
                full_codes.append(code)
                print(f'  {code} {name} ⚠ 重叠区 {len(diff)}/{len(overlap)} 天不一致'
                      f'（示例 {diff[0][0]}: {diff[0][2]} → {diff[0][1]}）→ 全量重抓 {len(p)} 根')
            except Exception as e:
                bad_codes.append(code)
                print(f'  {code} 除权后全量失败 {e}（⚠ 旧序列可能已过期）')
            time.sleep(0.3)
            continue

        # 重叠区完全吻合 → 只把新日期接上去
        merged = [list(x) for x in have]
        have_last = merged[-1][0] if merged else ''
        added = [[d, c] for d, c in tail if d > have_last]
        prices[code] = merged + added
        ok_codes.append(code)
        print(f'  {code} {name} 增量 +{len(added)} 天（重叠 {len(overlap)} 天吻合）'
              f' → 末 {prices[code][-1][0]}')
        time.sleep(0.3)

    json.dump(prices, open(px_fp, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'价格完成 → {px_fp}'
          + (f'（其中 {len(full_codes)} 只走了全量：{" ".join(full_codes)}）' if full_codes else ''))

    # 上报。这里必须报 —— 因为 prices.json 曾经完全不在这条链上，
    # 页面上的价格曲线会一天天变旧而没人知道（这就是 ⑤ 号静默失败）。
    #
    # ⚠⚠ 2026-11-12 当天踩的坑，务必记住：
    #   我第一次写的是 _got = len([k for k,v in prices.items() if v]) ——
    #   而 prices 是【先把旧文件读进来】的字典，失败的代码保留旧值，
    #   所以就算 10 只全失败，_got 仍然是 10，上报「ok 10/10 正常」。
    #   同一次运行里，日志写着 10 行「失败」，体检栏却显示「全部正常」。
    #   这跟我在 fetch_price_bfq.py 里专门写注释防的是同一个错误，还是又犯了一遍。
    #   **判据必须是「本次抓取的结果」，不是「合并后字典的长度」。**
    #
    # ⚠⚠ 2026-11-12 晚再修一次，因为「本次抓取的结果」这个说法还是不够细：
    #   那天日志写的是「1 只走了全量（510050）」，上报 got=1/want=10，看着对；
    #   但 last 用的是 max() ——
    #       _last = max(v[-1][0] for v in prices.values() if v)
    #   510050 一只前进到 11-12，就把整项的 last 拉到 11-12 → lag=0 →
    #   页面显示「2026-11-12 · 当日 · 1/10」，读起来像「今天更新了，只是慢半拍」。
    #   **实际是 9 只停更，体检栏报「落后 0 天」。**
    #   这比「全失败」更危险：全失败至少是 ❌，这个是好听的假话。
    #
    #   所以 last 不能用 max。改用 _fh.majority_last()：取「多数资产所在的那一天」，
    #   并且把「比这天更晚的有几只」单独报出来（那几只是特例，不能拿来给整项贴金）。
    _last, _ahead, _behind, _tail_n = _fh.majority_last(
        [prices.get(w[1]) or [] for w in WATCH])
    # 全失败时，仍要从旧文件里读出「曲线停在哪一天」——
    # 报「停在 11-11」比报「没有日期」有用得多，因为前者能直接算出落后几天。
    # 这和 fetch_price_bfq.py 的处理保持一致（那边也是读旧文件的末日期）。
    if _last is None:
        _last, _ahead, _behind, _tail_n = _fh.majority_last(list(prices.values()))
    _want = len(WATCH)
    _got = len(ok_codes)
    # ⚠ 两个方向都要写进 note：
    #   更晚的（个别标的提前一天）说明「这个日期不是全体的成绩」；
    #   更早的（真停更）说明「还有几只没跟上」。只写前者会美化，只写后者会漏特例。
    _lead = ''
    if _ahead:
        _lead += f'；{_ahead} 只已到更晚一天'
    if _behind:
        _lead += f'；{_behind} 只仍停在更早的日期'
    if not ok_codes:
        _fh.report('price_qfq', '价格（前复权，画曲线用）', 'fail',
                   last=_last, got=0, want=_want,
                   note=f'本次 0/{_want} 只，沿用旧价，价格曲线不会前进')
    else:
        _fh.report('price_qfq', '价格（前复权，画曲线用）',
                   'ok' if not bad_codes else 'partial',
                   last=_last, got=_got, want=_want,
                   note=(('全量刷新' if refresh else '') +
                         (f'；{len(bad_codes)} 只失败 {" ".join(bad_codes[:4])}'
                          f'{"…" if len(bad_codes) > 4 else ""}' if bad_codes else '') +
                         _lead).strip('；'))


def _fetch_shares(start, end):
    """份额段：只补真正缺的交易日。

    ⚠ 2026-11-12 晚从 main() 抽出来，为的是能单独跑（--shares-only）。
    打的是上交所 / 深交所，和价格段的东财不是同一个域名，配额互不影响 ——
    但也正因如此，它和价格段拆到两个 job 后，两边都能拿到干净 IP。
    """
    fp = os.path.join(DATA, 'shares.json')
    hist = json.load(open(fp, encoding='utf-8')) if os.path.exists(fp) else {}

    # ---- 份额：只补真正缺的交易日 ----
    #
    # ⚠⚠ 2026-11-12 晚改。原来这里写的是：
    #       days = [start + i 天 for i in range((end-start).days + 1)]
    #       todo = [d for d in days if d not in hist]
    #   也就是【按日历天】算缺口。实测得到 846 天「待补」，
    #   而其中 716 天是周末、其余是春节/国庆 —— 真正的缺口是 0 天。
    #
    #   代价不是「多打几次请求」那么简单，是一条完整的因果链：
    #     多打 846 次（每次还带 5 重试）→ 上交所限流 →
    #     同一进程后面的东财价格段 0/10 → 「落后 1 天 → 曲线不前进」
    #   一个 bug 喂出另一个 bug，日志上看起来是两件事。
    #
    #   现在的判据：从本地价格文件反推真实交易日（交易所真实开市日，自带节假日），
    #   再和已有日期做差。**已有 1661 天 = 真实交易日 1661 天，缺 0 天。**
    todo = _td.missing_trading_days(hist.keys(), start.isoformat())
    if todo is None:
        # 价格文件读不到（首次部署）→ 降级成「只补最近 5 个日历天」，
        # 绝不再退回「全区间日历天」那种打法。
        todo = [(end - timedelta(days=i)).isoformat() for i in range(5)][::-1]
        print('  ⚠ 无价格文件可反推交易日，降级为只补最近 5 个日历天')
    print(f'份额：需回补 {len(todo)} 个交易日（已有 {len(hist)} 天）')
    sse_codes = {w[1] for w in WATCH if w[3] == 'SSE'}
    ok = 0
    for i, d in enumerate(todo):
        try:
            sse = fetch_sse(d)
        except Exception:
            sse = {}
        if not sse:
            continue
        rec = {c: round(v, 2) for c, v in sse.items() if c in sse_codes}
        if rec:
            hist.setdefault(d, {}).update(rec)
            ok += 1
        if i and i % 20 == 0:
            print(f'  {d}  已补 {ok} 天', flush=True)
    # 深市只有快照
    try:
        snap = fetch_szse_snapshot()
        last = sorted(k for k, v in hist.items() if '510300' in v)[-1]
        got = {c: round(v, 2) for c, v in snap.items() if c in {w[1] for w in WATCH if w[3] == 'SZSE'}}
        hist.setdefault(last, {}).update(got)
        print(f'  深市快照写入 {last}：{len(got)} 只')
    except Exception as e:
        print('  深市失败', e)

    json.dump(dict(sorted(hist.items())), open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'份额完成：{len(hist)} 天 → {fp}')


if __name__ == '__main__':
    main()
