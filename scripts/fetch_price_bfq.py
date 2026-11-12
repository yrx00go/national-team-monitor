#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补抓【不复权】日线，用于计算净流入。

为什么必须用不复权价：
    净流入 = 份额差 × 当日真实成交价。ETF 分红除权那天，真实成交价会掉一个分红额，
    这时「份额不变、价格下跌」不代表亏损，而是分红落到了持有人账上。
    用前复权价算净流入是正确方向（消除了分红断层），
    但前复权价在历史区间上会被系统性压低（实测 2024-06-03：上证50 −5.4%、
    红利 −8.8%、中证红利 −10.9%），导致早期净流入被低估。
    正确做法：净流入一律用不复权（当日真实价），画价格曲线一律用前复权（趋势连续）。

输出：data/prices_bfq.json  {code: [[date, close], ...]}
"""
import json, os, sys, time, urllib.request
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fh          # 数据源健康上报，见 _fh.py 开头的说明
import _td          # 交易日历 + 北京时钟，见 _td.py 开头的说明
import _net         # 请求节流 + 账单，见 _net.py 开头的说明

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')

# 抓取起始日。真正决定页面区间的是 compute_demo.py 的 START，
# 这里留出余量早抓几天，免得 START 前移后要重抓。
BEG = '20190101'

CODES = {
    '510050': 'sh', '510300': 'sh', '510500': 'sh', '512100': 'sh',
    '588000': 'sh', '510880': 'sh', '515080': 'sh',
    '159915': 'sz', '159545': 'sz', '159919': 'sz',
}


def _get(url, referer='https://gu.qq.com/', tries=5):
    """带退避重试的 GET。

    ⚠ 2026-11-12 在 GitHub Actions 上实测出来的问题：
       10 只标的里 4 只（588000 / 510880 / 515080 / 159919）报
       `RemoteDisconnected: Remote end closed connection without response`，
       而上交所份额、两融余额同一台 runner 上全都成功。
       说明不是「美国 IP 被整体封」，而是东财 push2his 对【境外 IP + 高频连打】
       做了间歇性掐断 —— 典型瞬时错误，等一下再问就通了。

       原来只请求一次，撞上就整只标的作废，于是 compute_demo 因为
       缺 510880 直接 IndexError（两个 bug 串成一条链）。

       ⚠ 下午补充：同一天，走同一接口的 fetch_history 只重试 3 次，结果 0/10，
         而这里 4 次抓到 8/10。所以现在把打东财的三个脚本统一到 5 次、
         退避 1/2/4/8 秒 —— **重试次数不统一，会让故障看起来像「接口挂了」，
         实际只是少试了两次。**

       对交易所正常情况没有副作用：成功时只请求一次，一秒都不多等。
    """
    last = None
    for i in range(tries):
        # ⚠ 每次重试【之前】都要过闸门，不只是第一次。
        #   已经在限流的 IP 上连打，是把自己往更深的坑里推 ——
        #   这一点是探针测出来的，不是推测（见 _net.py 文件头）。
        _net.throttle(url)
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                'Referer': referer,
            })
            r = urllib.request.urlopen(req, timeout=30).read()
            _net.mark(url)
            return r
        except Exception as e:                     # 网络类异常一律重试
            last = e
            if i < tries - 1:
                time.sleep(min(1.0 * (2 ** i), 8.0))
    raise last


def fetch(code, market, beg=None):
    """拉日线。beg 为 None 时从 BEG（2019-01-01）全量拉。

    ⚠ 2026-11-12 晚：原来这个函数签名是 `fetch(code, market, bars=900)`，
       而 `bars` **从头到尾没被用过**（传进来就扔掉，实际起点由模块级 BEG 决定）。
       一个假装能控制范围的参数，比没有参数更危险 ——
       读代码的人会以为调用方控制了什么，其实什么都没控制。改成真的 beg。
    """
    """⚠ 这里曾经用腾讯接口，但腾讯硬上限 640 根（实测 2500 根直接返回空），
    只能回溯到 2024-03 左右，无法支撑 2020 年起的区间。
    改用东方财富 push2his，可回溯到上市首日，且【前复权序列与腾讯逐日吻合】
    （实测 510880 重叠 751 天，中位差 0.0000%，最大差 0.177%），换源不会改变已有结论。

    fqt=0 = 不复权（这里要的）；fqt=1 = 前复权。

    ⚠ 盘中陷阱（2026-11-12 实测）：
       接口在交易时段会返回【当日未收盘】的 K 线。那天 16:31 那次 CI 抓到
       510050 末价 2.884、510300 末价 4.114，日期都是 2026-11-12 —— 而当天
       上交所份额还没有（"2026-11-12 上交所无数据"），也就是说：
       价格有 11-12、份额只到 11-11。
       fetch_index.py 早就剔除当日盘中数据（它注释里写了 10:21 抓到半日量的坑），
       这里当时没跟上。后果：净流入 = 份额差 × 当日真实价，当日价是半天的，
       一旦份额也是半天的（上交所确实会盘中就更新），两边都残，算出的流量没意义。
       → 统一规则：只接受日期 < 今天的记录。当日数据一律丢弃，等收盘后再取
         （CI 在 UTC 08:30 ≈ 北京时间 16:30 跑，已收盘，但接口仍可能挂着未定稿的当日行）。
    """
    secid = ('1.' if market == 'sh' else '0.') + code
    url = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?'
           f'secid={secid}&fields1=f1,f2&fields2=f51,f53&klt=101&fqt=0'
           f'&beg={beg or BEG}&end={_td.bj_today():%Y%m%d}')
    js = json.loads(_get(url, 'https://quote.eastmoney.com/').decode('utf-8', 'ignore'))
    kl = (js.get('data') or {}).get('klines') or []
    out = []
    for s in kl:
        p = s.split(',')
        if len(p) < 2:
            continue
        # ⚠ 原来是 `if p[0] >= today: continue`（无条件丢弃当日）。
        #   上面那段注释把「盘中坑」写得很清楚，但结论下错了一格：
        #   该防的是「盘中未定稿」，不是「今天这个日期」。
        #   CI 在北京 18:38 跑，收盘价定稿 3 小时 38 分，照样被扔掉，
        #   于是末日期永远停在 T-1，页面天天「落后 1 天」——
        #   一条永远琥珀的提示 = 没人再看 = 等于没装。
        if not _td.accept_day(p[0]):
            continue
        out.append((p[0], float(p[1])))
    return out


START_NEED = '2020-01-02'   # 全站数据起点（与 compute_demo 的 START 一致）
NEAR_DAYS = 40              # 增量窗口的自然日数


# ══════════════════════════════════════════════════════════════════════════
# 备用源：腾讯（只在东财挂掉时兜底，不替换）
# ══════════════════════════════════════════════════════════════════════════
#
# ⚠⚠⚠ 为什么需要它（2026-11-12 深夜，第九轮 CI 之后的决定）：
#
#   那天 update job 的东财请求 **0/10 全挂**，账单 50 次（10 只 × 5 重试），
#   每一次都是首次就被拒 —— 不是「重试不够」，是这个 IP 那份配额根本没有了。
#   后果：净流入用旧价算。而 qfq 那个 job 同一时刻 5/10（它有自己的 IP），
#   说明这不是代码问题，是**出口 IP 配额**问题，重试和调顺序都治不了。
#
#   用户选的方向：**加一个备用源，专门保证「今天这一根」拿得到。**
#
# ⚠ 为什么腾讯只能兜底、不能替代东财：
#   实测腾讯 fqkline 硬上限 **641 根**（请求 640/641/1000 都返回 641，
#   只回溯到 2024-03；传 2500 直接 TypeError）。
#   本项目需要 2020 年起的 ~1900 根 → **腾讯做不了全量回补**。
#   但净流入只需要「今天这一根」和少量近期数据 → 641 根**完全够**。
#   所以分工必须是：
#     东财 = 主力（能全量、能回补）
#     腾讯 = 兜底（只在东财失败时，提供近端增量）
#
# ⚠⚠ 口径验证（这是方案成立的前提，不是假设）：
#   2026-11-12 深夜实测，腾讯 bfq vs 东财 fqt=0，逐日比对：
#       510050  640 天  完全一致 640  最大差 0.000000
#       510300  640 天  完全一致 640  最大差 0.000000
#       510880  640 天  完全一致 640  最大差 0.000000
#       159915  640 天  完全一致 640  最大差 0.000000
#       512100  640 天  完全一致 640  最大差 0.000000
#   **0 处差异**。两个源的不复权价就是同一个数，混用不会制造断层。
#   （对比：前复权两个源会差一个分红额，所以 qfq 那边不能这么干。）
#
# ⚠ 失败时的行为：兜底也失败 → 该只标的算失败，**沿用旧价**，不写空。
#   绝不因为「两个源都没通」就去写一个残缺序列。

TX_URL = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'
          'param={sym},day,,,{bars},{fq}')
TX_MAX_BARS = 640           # 实测硬上限 641，取 640 留一格余量


def parse_tx(js, sym):
    """把腾讯 fqkline 的 JSON 解析成 [(date, close), ...]。

    ⚠⚠ 腾讯的行格式是 [日期, 开, 收, 高, 低, 量, ...]，收盘价在 **row[2]**。
       东财的 klines 是字符串 "日期,收盘,量"，收盘在 split(',')[1]。
       **两个源的列位置不同**，照抄东财的 p[1] 会拿到开盘价。
       这个错不会报异常、不会看起来奇怪（开收价通常只差千分之几），
       但净流入 = 份额差 × 当日价，用开盘价算出来的每一天都是错的。
       所以解析必须单独成函数、单独被测 —— 揉在 fetch_tx 里只能靠联网验。
    """
    node = (js.get('data') or {}).get(sym) or {}
    k = node.get('day') or node.get('bfqkline') or []
    out = []
    for row in k:
        # 腾讯行格式：[日期, 开, 收, 高, 低, 量, ...]
        if len(row) < 3:
            continue
        if not _td.accept_day(row[0]):
            continue
        out.append((row[0], float(row[2])))
    return out


def fetch_tx(code, market, n=TX_MAX_BARS):
    """从腾讯拉不复权日线（兜底用）。返回 [(date, close), ...]。

    ⚠ 这里【不】用 _get（那个带 5 重试 + 东财 referer）。
       兜底源的定位是「快速失败、立刻换源」，不是「在原源上耗时间」。
       如果腾讯也挂，多试 4 次只是把整轮拖长，而结果一样 —— 那还不如早点
       认清「今天两个源都不行」，把「沿用旧价」这件事如实报出去。
       所以这里只试 2 次（1 次可能是瞬时抖动，2 次足够区分）。
    """
    sym = market + code
    url = TX_URL.format(sym=sym, bars=n, fq='bfq')
    last = None
    for i in range(2):
        _net.throttle(url)
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                'Referer': 'https://gu.qq.com/',
            })
            raw = urllib.request.urlopen(req, timeout=25).read()
            _net.mark(url)
            return parse_tx(json.loads(raw.decode('utf-8', 'ignore')), sym)
        except Exception as e:
            last = e
            if i == 0:
                time.sleep(1.0)
    raise last


def need_full(have):
    """这只要不要全量重抓？

    ⚠ 抽成独立函数是为了能测。
       原来这段判断写死在 main() 的循环里，只能靠跑真接口验，
       而真接口一天只让我试几次 —— 等于没法验。
    """
    if not have:
        return True                      # 没历史 → 只能全量
    return have[0][0] > START_NEED       # 历史起点太晚 → 回补不全 → 全量


def acquire(code, mk, have, fetch_fn=None, tx_fn=None):
    """抓一只标的：东财优先，失败则腾讯兜底（仅限增量场景）。

    返回 (fresh, src, err)：
        fresh = [(date, close), ...] 或 None
        src   = '东财' / '腾讯' / None
        err   = 失败原因字符串 或 None

    ⚠⚠ 为什么把这段从 main() 里抠出来成函数（2026-11-12 深夜）：
       兜底分支的触发条件是「东财失败」。而 CI 上东财有时通、有时不通 ——
       **东财通了，兜底就永远不执行，那段代码永远没被跑过。**
       一段从未执行过的错误处理，和没有错误处理是一回事，只是更让人安心。
       抽出来、把两个源做成可注入的参数，就能在离线测试里
       把「东财必挂」「腾讯也挂」「需要全量」每种情况都跑一遍。

    ⚠ 需要全量回补时不兜底。腾讯硬上限 641 根，给不了 2020 年起的序列。
       硬用它补全量 → 得到「起点 2024-03」的残缺序列 →
       need_full 判据（起点 > 2020-01-02 就重抓）永远成立 →
       **每天重抓、每天得到同一段残缺数据，死循环且不自知。**
       所以宁可如实报失败、沿用旧价，也不接受一个假装完整的序列。
    """
    fetch_fn = fetch_fn or fetch
    tx_fn = tx_fn or fetch_tx
    full = need_full(have)
    beg = None if full else (_td.bj_today() - timedelta(days=NEAR_DAYS * 2)).strftime('%Y%m%d')

    try:
        fresh = fetch_fn(code, mk, beg=beg)
        if not fresh:
            raise RuntimeError('空返回')
        return fresh, '东财', None
    except Exception as e:
        if full:
            return None, None, f'{type(e).__name__}: {e}（需全量，腾讯兜底不可用）'
        try:
            fresh = tx_fn(code, mk, n=TX_MAX_BARS)
            if not fresh:
                raise RuntimeError('空返回')
            return fresh, '腾讯', None
        except Exception as e2:
            return None, None, f'东财 {type(e).__name__} / 腾讯 {type(e2).__name__}'


def merge_incremental(have, fresh):
    """把新抓的近端接进旧序列。返回 (合并后序列, 新增根数)。

    ⚠ 不复权价的核心性质：**除权不改写历史**。
       所以这里可以放心「只追加、不重算」——
       前复权就不能这么干（一次分红整段重来），那边必须走重叠校验。

    ⚠ 去重规则是 `d > 旧末日期`，不是 `d >= 旧末日期`。
       写成 `>=` 的话，旧末日期那根会用新值覆盖旧值 ——
       看着无害，但如果新抓的那根是【盘中半日价】（时钟判断被绕过、
       或者跑在别人的机器上时区不对），就会把一根定稿的收盘价
       悄悄换成一个半天的价。**只追加，永不覆盖**，是这里唯一安全的写法。
    """
    if not have:
        return list(fresh), len(fresh)
    last = have[-1][0]
    added = [[x[0], x[1]] for x in fresh if x[0] > last]
    return [list(x) for x in have] + added, len(added)


def _save(fp, obj):
    """原子写盘：先写 .tmp 再 os.replace。

    直接 open(fp,'w') 写，如果中途异常（磁盘满、进程被杀、JSON 序列化炸），
    会留下一个【半截文件】。下游 compute_demo.py 读到坏 JSON 直接崩，
    而它是可选步骤 —— 崩了页面就沿用旧数据，表面一切正常。
    临时文件 + replace 是原子操作，要么旧的完整、要么新的完整，没有中间态。
    """
    tmp = fp + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, fp)


def main():
    fp = os.path.join(DATA, 'prices_bfq.json')

    # ⚠⚠ 2026-11-12 第二次 CI（run 43182246241）炸出来的最严重 bug，记录在这里：
    #   那一次东财全站掐断，10 只标的全部 RemoteDisconnected，
    #   而这几行代码【无条件】json.dump(out, ...)，于是：
    #       写入 data/prices_bfq.json  (0 KB)  成功 0/10
    #   把仓库里原本 227 KB 的有效数据【覆盖成了空文件】，再由 CI 的 git commit
    #   提交回仓库 → 线上页面「组合单日净流入 +0.00 亿 / 近5日 +0.0 亿」全部归零。
    #
    #   比崩溃更糟：崩溃至少能看见，写空是静默的，日志里只有一行 "0 KB"，
    #   页面长得跟正常一样，只是数字全变成 0。
    #   上一轮我加「判空兜底」正是为了不让 compute_demo.py 崩 —— 结果把崩溃
    #   换成了静默写空，等于把响警报拆了却没修火。
    #
    #   现在的三条规矩：
    #     ① 全失败（一只都没抓到）→ 一个字都不写，直接 return 2，旧文件原样保留
    #     ② 部分成功 → 【合并】进旧数据，不能整体替换。否则某只标的瞬时故障，
    #        它那一段历史就被清空了，净流入从该日起全变 0
    #     ③ 抓到的写入 + 旧的里没抓到的保留，两边都以「日期」为准去重，取新的
    old = {}
    if os.path.exists(fp):
        try:
            old = json.load(open(fp, encoding='utf-8')) or {}
        except Exception as e:
            print(f'  ⚠ 旧文件读不出来（{type(e).__name__}: {e}），按空处理')
            old = {}

    # ⚠⚠ 2026-11-12 晚：从「每天全量 1906 根 × 10 只」改成「增量」。
    #
    #   探针测出来的事实：干净 IP 上全量 1906 根也是通的（1.67s），
    #   但跑完一轮之后连 47 根的近端都断。**敌人是请求总量，不是体积。**
    #   不过体积也不是完全无关 —— 1906 根 × 10 只 = 19 万条记录，
    #   响应体加起来好几 MB，打的时候占用的带宽和时长都在给限流计时器加分。
    #
    #   不复权价比前复权安全得多：**分红除权不改写历史**，
    #   所以旧数据永远不需要重算，只要把新日期接上去。
    #   （前复权则相反，一次分红就要整段重来 —— 那边才需要重叠校验。）
    #
    #   判据：旧数据已经覆盖到 START（2020-01-02）→ 只补最近一段；
    #         旧数据缺失或起点太晚 → 全量回补。
    out, bad, inc_codes, tx_codes, tx_stale = {}, [], [], [], []
    for code, mk in CODES.items():
        have = old.get(code) or []
        full = need_full(have)
        fresh, src, err = acquire(code, mk, have)
        if err:
            bad.append((code, err))
            if '需全量' in err:
                print(f'  {code} 东财失败且需全量回补 → 腾讯只有 {TX_MAX_BARS} 根，兜不了')
            else:
                print(f'  {code} 两个源都失败 → 沿用旧价')
                time.sleep(0.4)
            continue
        if full:
            arr = [list(x) for x in fresh]
            if src == '腾讯':
                tx_codes.append(code)
            print(f'  {code}  {len(arr):>4} 根  {arr[0][0]} → {arr[-1][0]}'
                  f'  末价 {arr[-1][1]:.3f}  [全量·{src}]')
        else:
            arr, n_add = merge_incremental(have, fresh)
            inc_codes.append(code)
            print(f'  {code}  增量 +{n_add} 根（旧 {len(have)} 根保留）'
                  f' → 末 {arr[-1][0]}  末价 {arr[-1][1]:.3f}  [{src}]')
            if src == '腾讯':
                # ⚠⚠ 腾讯「通了」不等于「给了今天的价」。
                #   实测过：兜底成功、但返回的末日期还是旧的那一天（源本身滞后），
                #   于是 merge 新增 0 根 —— 这只会被算成「成功」，
                #   而 note 里写着「走腾讯备用源」，读的人会以为它有保障。
                #   实际它是「换了个源，照样没拿到今天的」，比彻底失败更误导。
                #   所以必须分开计：真帮忙了 vs 也停在旧日期。
                if n_add > 0:
                    tx_codes.append(code)
                else:
                    tx_stale.append(code)
                print(f'  {code}  东财失败 → 腾讯兜底'
                      f'{"（+%d 根，帮上了）" % n_add if n_add else "（末日期没前进，没帮上）"}')
        out[code] = arr
        time.sleep(0.4)

    if inc_codes:
        print(f'\n  其中 {len(inc_codes)} 只走增量：{" ".join(inc_codes)}')
    if tx_codes:
        # ⚠ 必须显式说出来。混源不是错（口径已验一致），但读日志的人
        #   有权知道今天有一部分的数不是主力源给的。
        print(f'  ⚠ 其中 {len(tx_codes)} 只用的是腾讯备用源：{" ".join(tx_codes)}')
    if tx_stale:
        # 这类最隐蔽：腾讯接通了、也返回了数据，但末日期没比旧数据新。
        # 表面上「兜底成功」，实际上今天这一根照样没有。
        print(f'  ⚠ 其中 {len(tx_stale)} 只腾讯接通但末日期没前进'
              f'（照样没有今天）：{" ".join(tx_stale)}')

    if bad:
        print(f'\n失败 {len(bad)} 只：')
        for c, r in bad:
            print(f'  {c}  {r}')

    # ── ① 全失败：不写盘 ───────────────────────────────────────────────
    if not out:
        kept = sum(len(v) for v in old.values())
        # 全失败也要上报 —— 「一只没抓到」正是用户最需要知道的那一条。
        # 页面上会显示成「价格(不复权) · 到 X 日 · 今日未获取 · 沿用旧价」。
        #
        # ⚠ 这里原来写的是 max(...)，是同一轮里第三次出现的同一个错
        #   （见 7.27 / 7.30）。全失败时各只标的的旧末日期也可能不一致，
        #   max 会把最前面那只的日期当成整批的日期。改用 majority_last。
        last, _a, _b, _t = _fh.majority_last(list(old.values()))
        _note = f'今日 0/{len(CODES)} 只，沿用旧价'
        # ⚠ 这里原来判的是 tx_codes —— 而全失败时 out 为空，tx_codes 必定为空，
        #   那段「两个源都失败」的提示**永远不触发**。等于写了个死分支。
        #   要看的是 bad 里有没有「腾讯也试过了」的痕迹。
        if any('腾讯' in r for _, r in bad):
            _note += '（东财与腾讯备用源均失败）'
        _fh.report('price_bfq', '价格（不复权，算净流入用）', 'fail',
                   last=last, got=0, want=len(CODES), note=_note)
        print(f'\n✗ 一只都没抓到，不写盘。旧文件保留 {len(old)} 只 / {kept} 根 '
              f'({os.path.getsize(fp)/1024:.0f} KB)' if os.path.exists(fp) else
              '\n✗ 一只都没抓到，且本地无旧文件，本次不产出 prices_bfq.json')
        return 2

    # ── ② 合并：抓到的覆盖旧的，没抓到的沿用旧数据 ─────────────────────
    merged, filled = {}, []
    for code in CODES:
        if code in out:
            merged[code] = out[code]
        elif code in old and old[code]:
            merged[code] = old[code]
            filled.append(code)

    _save(fp, merged)

    # 上报：last 用「真正抓到的那几只」的末日期，不用合并后的 ——
    # 合并后末日期看起来是新，但那几只其实是旧价，报上去就是骗自己。
    #
    # ⚠⚠ 但「抓到的那几只」也不能取 max()。2026-11-12 晚在 fetch_history.py
    #   抓到的同一个错：9 只停在 T-1、1 只到了 T，max() 会把整项报成 T，
    #   lag 归零，页面显示「当日」，而实际上九只停更。
    #   要取「多数所在的那一天」，并把领先的那几只单独写进 note。
    got_last, got_ahead, got_behind, _n = _fh.majority_last(list(out.values()))
    _lead = ''
    if got_ahead:
        _lead += f'；{got_ahead} 只已到更晚一天'
    if got_behind:
        _lead += f'；{got_behind} 只仍停在更早的日期'
    # ⚠ 用了备用源必须上屏。混源本身不丢人（口径已逐日验过一致），
    #   但「今天这份数不是主力源给的」这件事，看页面的人有权知道 ——
    #   否则以后主力源烂了半个月，页面还一片绿，就成了第 N 个静默失败。
    if tx_codes:
        _lead += f'；{len(tx_codes)} 只走腾讯备用源（东财拒绝）'
    # ⚠⚠ 腾讯「接通」≠「给了今天的价」。这类必须单独说，
    #   而且不能说成「走了备用源」—— 那读起来像「有备用所以没事」。
    #   事实是：换了个源，今天这一根照样没拿到。比直接失败更容易误导。
    if tx_stale:
        _lead += f'；{len(tx_stale)} 只腾讯接通但末日期没前进（仍无当日）'
    if bad or tx_stale:
        _fh.report('price_bfq', '价格（不复权，算净流入用）', 'partial',
                   last=got_last, got=len(out), want=len(CODES),
                   note=(f'{len(bad)} 只未获取，{" ".join(c for c, _ in bad[:4])}'
                         f'{"…" if len(bad) > 4 else ""} 沿用旧价' + _lead
                         if bad else _lead.lstrip('；')))
    else:
        _fh.report('price_bfq', '价格（不复权，算净流入用）', 'ok',
                   last=got_last, got=len(out), want=len(CODES), note=_lead.lstrip('；'))

    print(f'\n写入 {fp}  ({os.path.getsize(fp)/1024:.0f} KB)  '
          f'抓到 {len(out)}/{len(CODES)}')
    if filled:
        # 不是错误，但要显式说出来：这几只的末价是旧的，
        # 净流入会按「旧价 × 新份额」算 —— 误差只在该标的当日涨跌里，比归零好得多。
        print(f'  沿用旧数据 {len(filled)} 只：{" ".join(filled)}')

    # ③ 部分成功也返回 1：让 Actions 标红，人能看到「今天没抓全」。
    #    但数据是对的（合并后的），页面能正常用。
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
