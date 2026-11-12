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


def need_full(have):
    """这只要不要全量重抓？

    ⚠ 抽成独立函数是为了能测。
       原来这段判断写死在 main() 的循环里，只能靠跑真接口验，
       而真接口一天只让我试几次 —— 等于没法验。
    """
    if not have:
        return True                      # 没历史 → 只能全量
    return have[0][0] > START_NEED       # 历史起点太晚 → 回补不全 → 全量


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
    out, bad, inc_codes = {}, [], []
    for code, mk in CODES.items():
        have = old.get(code) or []
        full = need_full(have)
        beg = None if full else (_td.bj_today() - timedelta(days=NEAR_DAYS * 2)).strftime('%Y%m%d')
        try:
            fresh = fetch(code, mk, beg=beg)
            if not fresh:
                bad.append((code, '空返回'))
                continue
            if full:
                arr = [list(x) for x in fresh]
                print(f'  {code}  {len(arr):>4} 根  {arr[0][0]} → {arr[-1][0]}'
                      f'  末价 {arr[-1][1]:.3f}  [全量]')
            else:
                arr, n_add = merge_incremental(have, fresh)
                inc_codes.append(code)
                print(f'  {code}  增量 +{n_add} 根（旧 {len(have)} 根保留）'
                      f' → 末 {arr[-1][0]}  末价 {arr[-1][1]:.3f}')
            out[code] = arr
        except Exception as e:
            bad.append((code, f'{type(e).__name__}: {e}'))
        time.sleep(0.4)

    if inc_codes:
        print(f'\n  其中 {len(inc_codes)} 只走增量：{" ".join(inc_codes)}')

    if bad:
        print(f'\n失败 {len(bad)} 只：')
        for c, r in bad:
            print(f'  {c}  {r}')

    # ── ① 全失败：不写盘 ───────────────────────────────────────────────
    if not out:
        kept = sum(len(v) for v in old.values())
        # 全失败也要上报 —— 「一只没抓到」正是用户最需要知道的那一条。
        # 页面上会显示成「价格(不复权) · 到 X 日 · 今日未获取 · 沿用旧价」。
        last = max((v[-1][0] for v in old.values() if v), default=None)
        _fh.report('price_bfq', '价格（不复权，算净流入用）', 'fail',
                   last=last, got=0, want=len(CODES),
                   note=f'今日 0/{len(CODES)} 只，沿用旧价')
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
    got_last = max((v[-1][0] for v in out.values() if v), default=None)
    if bad:
        _fh.report('price_bfq', '价格（不复权，算净流入用）', 'partial',
                   last=got_last, got=len(out), want=len(CODES),
                   note=f'{len(bad)} 只未获取，{" ".join(c for c, _ in bad[:4])}'
                        f'{"…" if len(bad) > 4 else ""} 沿用旧价')
    else:
        _fh.report('price_bfq', '价格（不复权，算净流入用）', 'ok',
                   last=got_last, got=len(out), want=len(CODES))

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
