#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓大盘日线：上证指数点位 + 两市成交额。用于组合页「净申赎 × 大盘位置」叠加图
与「量能 / 杠杆」面板。

为什么要有它：
    原有的净流入图只能回答「钱进还是出」，回答不了「这个进出发生在什么位置」。
    同一根 +120 亿的柱子，出现在 2700 点和 3600 点，含义完全不同 ——
    前者是「抄底」，后者是「追高」。点位是资金流的分母。
    成交额是第二个分母：同样的 120 亿，在 8,000 亿的地量里是异动，
    在 2.5 万亿的天量里只是噪音。

为什么是指数点位而不是价格：
    组合是 9 只 ETF 的合计，它们没有统一的「价格」。
    上证指数是唯一一条能代表「这个组合所处大盘位置」的公开序列。

接口：东方财富 push2his，klt=101 日线，fqt=0 不复权。
      secid=1.000001 → 上证指数（沪市）
      secid=0.399001 → 深证成指（深市）
      ⚠ 该接口 data.name 返回 None（指数不给名称），但 data.code 与 klines 完整，
        不要因为 name 为 None 就判断抓取失败。

      fields2 字段位：f51=日期 f52=开 f53=收 f54=高 f55=低 f56=成交量(手/f57=成交额(元)
      ⚠ 成交额必须用 f57（元），不是 f56（成交量，单位手）。
        实测 2026-11-11：沪市 f57=5.7e11 元 = 5,690 亿，与交易所公布口径一致。

为什么两市要相加：
    「两市成交额」是市场共识口径（新闻里说的「今日成交 1.6 万亿」都是这个）。
    只取沪市会漏掉深市 40%+ 的量，得出「量能萎缩」的错觉。

⚠ 盘中陷阱：接口在交易时段会返回【当日未收盘】的数据。
   实测 2026-11-12 10:21 抓取，当日成交额只有 8,486 亿（半日量），
   而 11-11 全天是 16,762 亿。若不剔除，页面会把「半天」当「全天」，
   量能萎缩 50% 的假信号就出来了。
   → 处理：只接受日期 < 今天的记录。当日数据一律丢弃，等第二天再取。

输出：data/index_sh.json
      {
        "000001": [[date, close], ...],           ← 上证点位（保持旧格式，别改）
        "turnover": [[date, 两市成交额(亿元)], ...]  ← 新增
      }
"""
import json, os, sys, time, urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fh          # 数据源健康上报，见 _fh.py 开头的说明
import _td          # 交易日历 + 北京时钟，见 _td.py 开头的说明
import _net         # 请求节流 + 账单，见 _net.py 开头的说明

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')

BEG = '20190101'   # 与 fetch_price_bfq.py 保持一致，多留余量
TODAY = _td.bj_today_str()

# 腾讯当日快照（兜底用，见 fetch_tx_snapshot 的说明）
TX_SNAPSHOT = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'
               'param={sym},day,,,2,bfq')


def _get(url, referer='https://quote.eastmoney.com/', retries=5):
    """带退避重试的 GET。

    ⚠ 2026-11-12：这里原来是 3 次。同一天 CI 里，走同一个 push2his 接口的
       fetch_price_bfq（4 次重试）抓到 8/10，而 fetch_history（3 次）0/10。
       东财对境外 IP 是间歇性掐断，实测同一请求前后差不到 1 秒就一通一断 ——
       「多试一次」直接决定成败。三个打东财的脚本现在统一 5 次、退避 1/2/4/8 秒。
       不统一的后果就是：报错看起来像「接口挂了」，实际只是重试少了两次。
    """
    last = None
    for i in range(retries):
        _net.throttle(url)      # 见 _net.py：每次重试前都过闸门，不只是第一次
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                'Referer': referer,
            })
            r = urllib.request.urlopen(req, timeout=30).read()
            _net.mark(url)
            return r
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(min(1.0 * (2 ** i), 8.0))
    raise last


def fetch_kline(secid, fields2='f51,f53'):
    """返回 (code, [(date, 字段...), ...])，字段顺序与 fields2 一一对应"""
    url = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?'
           f'secid={secid}&fields1=f1,f2&fields2={fields2}&klt=101&fqt=0'
           f'&beg={BEG}&end={_td.bj_today():%Y%m%d}')
    js = json.loads(_get(url).decode('utf-8', 'ignore'))
    d = js.get('data') or {}
    kl = d.get('klines') or []
    nf = len(fields2.split(','))
    out = []
    for s in kl:
        p = s.split(',')
        if len(p) < nf:
            continue
        # 除日期外全部转 float
        out.append([p[0]] + [float(x) for x in p[1:nf]])
    return d.get('code') or secid.split('.')[1], out


def fetch_tx_snapshot():
    """腾讯当日快照兜底。返回 {'date':…, 'sh':…, 'sz':…, 'turnover':…} 或 None。

    ⚠⚠ 为什么这个兜底和 bfq 那个长得不一样：
       腾讯的指数接口能给出**历史点位序列**（day 键，640 根），
       但**成交额只有当日快照**（qt 里那个 "价/量/额" 三合一字符串），没有历史序列。
       本文件要的是「点位 + 两市成交额」两样一起，
       只兜点位、不兜成交额 → 量能面板还是会缺一天，
       而且会出现「点位到 11-12、成交额停在 11-11」这种半截状态，
       比整体沿用旧值更难解释。
       所以这里**统一只补当日这一根**：两样都有才用，缺一样就整体不用。

    ⚠ 口径已实测对齐（2026-11-12 深夜，同一交易日相比）：
         上证点位   东财 3655.83  =  腾讯 3655.83
         两市成交额 东财 19,320.8 亿 = 腾讯 8,795.4 + 10,525.4 = 19,320.8 亿
       精确到最后一位。所以兜底不会在序列上留一个断层。

    ⚠ 只试 2 次：兜底源的定位是「快速失败、立刻放弃」，不是在它身上耗时间。
    """
    out = {}
    for sym, tag in (('sh000001', 'sh'), ('sz399001', 'sz')):
        url = TX_SNAPSHOT.format(sym=sym)
        last = None
        got = None
        for i in range(2):
            _net.throttle(url)
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                    'Referer': 'https://gu.qq.com/',
                })
                raw = urllib.request.urlopen(req, timeout=20).read()
                _net.mark(url)
                js = json.loads(raw.decode('utf-8', 'ignore'))
                node = (js.get('data') or {}).get(sym) or {}
                arr = (node.get('qt') or {}).get(sym) or []
                # qt 里有几个字符串是 "价/量/额" 三合一，成交额在第三段
                amt = None
                for v in arr:
                    if isinstance(v, str) and v.count('/') == 2:
                        try:
                            amt = float(v.split('/')[2])
                            break
                        except ValueError:
                            continue
                # 日期：qt 里有个 14 位时间戳 YYYYMMDDHHMMSS
                d = None
                for v in arr:
                    s = str(v)
                    if len(s) == 14 and s.isdigit() and s[:4].startswith('20'):
                        d = f'{s[:4]}-{s[4:6]}-{s[6:8]}'
                        break
                close = float(arr[3]) if len(arr) > 3 else None
                if d and close and amt:
                    got = (d, close, amt)
                    break
            except Exception as e:
                last = e
                if i == 0:
                    time.sleep(1.0)
        if not got:
            print(f'  腾讯快照 {tag} 失败：{type(last).__name__ if last else "字段缺失"}')
            return None
        out[tag] = got
    if out['sh'][0] != out['sz'][0]:
        print(f'  腾讯快照两个市场日期不一致（{out["sh"][0]} vs {out["sz"][0]}）→ 不用')
        return None
    d = out['sh'][0]
    return {'date': d,
            'sh': out['sh'][1],
            'sz': out['sz'][1],
            'turnover': round((out['sh'][2] + out['sz'][2]) / 1e8, 1)}


def _old_last(fn):
    """读旧文件里最新到哪一天。抓取失败时用它当 last —— 报「停在 11-11」
    比报「没有日期」有用得多，因为前者能直接算出落后几天。"""
    fp = os.path.join(DATA, fn)
    if not os.path.exists(fp):
        return None
    try:
        js = json.load(open(fp, encoding='utf-8'))
        for k, v in js.items():
            if k.startswith('000') and v:
                return v[-1][0]
        t = js.get('turnover') or []
        return t[-1][0] if t else None
    except Exception:
        return None


def _why(e):
    """把异常翻译成一句人话。

    ⚠ 这一条是纪律，不是审美：`_fh.py` 的文件头写着「note 直接上屏，要写人话」。
    我原来直接把 `RemoteDisconnected: Remote end closed connection without response`
    丢进 note，结果手机上它折成 3 行、单行 109px，整栏被这一条撑到 373px，
    首屏 844px 里它吃掉 44% —— **报了，但把正事挡掉了**。
    技术细节仍然要留，写进 print 的日志里，那里才是排查时看的地方。
    """
    n = type(e).__name__
    s = str(e)
    if 'RemoteDisconnected' in n or 'RemoteDisconnected' in s:
        return '接口主动断连（境外 IP 常见）'
    if 'timeout' in n.lower() or 'timed out' in s.lower():
        return '接口超时'
    if 'Connection' in n or 'Connection' in s:
        return '连不上接口'
    return '接口异常（详见运行日志）'


def _apply_snapshot(snap):
    """把腾讯快照接进 index_sh.json（只追加、不覆盖）。成功返回 True。

    ⚠ 三处失败分支（东财异常 / 返回空 / 只有盘中数据）都要用它，
       所以抽成函数。三处各写一遍的结果就是改一处漏两处 —— 见 7.26。
    """
    old_fp = os.path.join(DATA, 'index_sh.json')
    old = {}
    if os.path.exists(old_fp):
        try:
            old = json.load(open(old_fp, encoding='utf-8'))
        except Exception:
            old = {}
    for key, val in (('000001', snap['sh']),):
        seq = old.get(key) or []
        if not seq or snap['date'] > seq[-1][0]:
            seq = seq + [[snap['date'], round(val, 2)]]
        old[key] = seq
    tseq = old.get('turnover') or []
    if not tseq or snap['date'] > tseq[-1][0]:
        tseq = tseq + [[snap['date'], snap['turnover']]]
    old['turnover'] = tseq
    json.dump(old, open(old_fp, 'w', encoding='utf-8'), ensure_ascii=False)
    return True


def main():
    # 上证点位
    try:
        code, arr = fetch_kline('1.000001')
    except Exception as e:
        # ⚠⚠ 东财挂了 → 试腾讯当日快照（2026-11-12 深夜新增，见 fetch_tx_snapshot）
        #
        #   为什么值得兜：本文件在 CI 上的失败率是 **100%**（连续多轮 0/3），
        #   而它产出的正是「组合页净申赎 × 大盘位置」那张叠加图的位置轴 ——
        #   天天不更新，那张图就等于在骗人。
        #   腾讯只管当日一根，但这一根恰好就是这里最需要的。
        print(f'上证指数抓取失败：{type(e).__name__}: {e} → 试腾讯快照')
        snap = fetch_tx_snapshot()
        if snap and _td.accept_day(snap['date']):
            _apply_snapshot(snap)
            _fh.report('index', '上证指数', 'ok', last=snap['date'],
                       note='东财拒绝，走腾讯当日快照')
            _fh.report('turnover', '两市成交额', 'ok', last=snap['date'],
                       note='东财拒绝，走腾讯当日快照')
            print(f'  ✓ 腾讯快照兜住当日：{snap["date"]}  上证 {snap["sh"]:.2f}  '
                  f'两市成交额 {snap["turnover"]:,.1f} 亿')
            return 0
        _fh.report('index', '上证指数 / 两市成交额', 'fail',
                   last=_old_last('index_sh.json'), note=_why(e) + '；腾讯快照也不可用')
        return 1
    if not arr:
        # 返回空 → 同样试兜底（「返回空」和「异常」对使用者是一回事：今天没有）
        snap = fetch_tx_snapshot()
        if snap and _td.accept_day(snap['date']):
            _apply_snapshot(snap)
            _fh.report('index', '上证指数', 'ok', last=snap['date'],
                       note='东财返回空，走腾讯当日快照')
            _fh.report('turnover', '两市成交额', 'ok', last=snap['date'],
                       note='东财返回空，走腾讯当日快照')
            print(f'  ✓ 东财返回空，腾讯快照兜住：{snap["date"]}')
            return 0
        _fh.report('index', '上证指数 / 两市成交额', 'fail',
                   last=_old_last('index_sh.json'), note='接口返回空（疑似改版）；腾讯快照也不可用')
        print('上证指数返回为空（接口可能改版）—— 不做任何写入，避免用空文件覆盖旧数据')
        return 1

    # 剔除当日未收盘数据
    # ⚠ 原来是 x[0] < TODAY（无条件丢掉当日）。理由对（防盘中半日量），
    #   但判据错：把「今天已收盘」和「今天正在盘中」当成一件事。
    #   CI 在北京 18:38 跑，量能早已定稿，仍然被扔掉 → 天天「落后 1 天」。
    #   现在改问 _td.accept_day()：这一根定稿了吗。
    arr = [x for x in arr if _td.accept_day(x[0])]
    if not arr:
        snap = fetch_tx_snapshot()
        if snap and _td.accept_day(snap['date']):
            _apply_snapshot(snap)
            _fh.report('index', '上证指数', 'ok', last=snap['date'],
                       note='东财只有盘中数据，走腾讯当日快照')
            _fh.report('turnover', '两市成交额', 'ok', last=snap['date'],
                       note='东财只有盘中数据，走腾讯当日快照')
            print(f'  ✓ 东财只有盘中数据，腾讯快照兜住：{snap["date"]}')
            return 0
        _fh.report('index', '上证指数 / 两市成交额', 'fail',
                   last=_old_last('index_sh.json'), note='只有当日盘中数据，已剔除；腾讯快照也不可用')
        print('上证指数剔除当日盘中数据后为空 —— 不写入')
        return 1

    lo = min(x[1] for x in arr)
    hi = max(x[1] for x in arr)
    pos = [[x[0], round(x[1], 2)] for x in arr]
    print(f'  上证指数 {code}  {len(pos)} 根  {pos[0][0]} → {pos[-1][0]}')
    print(f'  首值 {pos[0][1]:.2f}   末值 {pos[-1][1]:.2f}   区间 {lo:.2f} ~ {hi:.2f}')

    # ② 两市成交额（沪 + 深）
    turn = {}
    ok = 0
    for secid, tag in (('1.000001', '沪'), ('0.399001', '深')):
        try:
            _, kl = fetch_kline(secid, 'f51,f57')
        except Exception as e:
            print(f'{tag}市成交额抓取失败：{type(e).__name__}: {e}')
            continue
        kl = [x for x in kl if _td.accept_day(x[0])]   # 同上：按「定稿与否」而非「是不是今天」
        if not kl:
            print(f'{tag}市成交额为空')
            continue
        ok += 1
        for d, amt in kl:
            turn[d] = turn.get(d, 0.0) + amt / 1e8    # 元 → 亿元
        print(f'  {tag}市  {len(kl)} 根  {kl[0][0]} → {kl[-1][0]}')

    turn_arr = []
    if ok == 2:
        turn_arr = [[d, round(v, 1)] for d, v in sorted(turn.items())]
        if turn_arr:
            print(f'  两市成交额  {len(turn_arr)} 天  '
                  f'最新 {turn_arr[-1][0]} = {turn_arr[-1][1]:,.0f} 亿')
            # 自检：沪市单边应在 2,000–15,000 亿区间，超出说明字段位取错了
            _hi = max(v for _, v in turn_arr)
            if _hi > 60000 or _hi < 2000:
                print(f'  ⚠ 成交额量级异常（最大 {_hi:,.0f} 亿）—— 疑似字段位错误，本次不写入 turn')
                turn_arr = []
    elif ok == 1:
        print('  ⚠ 只拿到单市成交额，两市合计不完整 —— 本次不写入 turn，保留旧值')

    out = {code: pos}
    if turn_arr:
        out['turnover'] = turn_arr
    else:
        # 保留旧的 turnover，避免一次抓取失败把历史量能清空
        old_fp = os.path.join(DATA, 'index_sh.json')
        if os.path.exists(old_fp):
            old = json.load(open(old_fp, encoding='utf-8'))
            if old.get('turnover'):
                out['turnover'] = old['turnover']
                print(f'  沿用旧 turnover {len(old["turnover"])} 天')

    fp = os.path.join(DATA, 'index_sh.json')
    json.dump(out, open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'\n写入 {fp}  ({os.path.getsize(fp)/1024:.0f} KB)')

    # 上报。成交额单独一个 key：它和点位来自同一个接口，但失败模式不同
    # （点位能拿到、成交额只拿到单市），分开记才看得出是哪一半缺了。
    _fh.report('index', '上证指数', 'ok', last=pos[-1][0])
    if turn_arr:
        _fh.report('turnover', '两市成交额', 'ok', last=turn_arr[-1][0])
    else:
        _fh.report('turnover', '两市成交额', 'partial',
                   last=(turn_arr[-1][0] if turn_arr else _old_last('index_sh.json')),
                   note=f'只拿到 {ok}/2 市合计，保留旧值')
    return 0


if __name__ == '__main__':
    sys.exit(main())
