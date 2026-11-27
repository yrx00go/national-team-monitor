#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓沪深300全收益 + 中证全债，算「股债比价」—— 股债交汇（第 7 类面板）。

═══ 这个面板要回答什么 ═══
「现在这个位置，该多拿一点股票，还是多拿一点债？」
不是预测点位，是回答「股票相对债券，贵还是便宜」。

═══ 为什么是这两个指数（口径，别改）═══
股票端：**沪深300全收益（H00300）**
   刻意不用 000300（价格指数）。价格指数把分红剔掉了，而分红是 A 股年化
   2%～3% 的真实回报，十年下来差 30%。
     · 用价格指数 → 股债比价系统性低估股票收益 → 永远显示「股票便宜」→ 指标废掉
     · 用全收益   → 与持有体验一致（分红再投入）
   一句话：**要比「拿股票的收益」和「拿债券的收益」，就必须两边都是总回报。**

债券端：**中证全债（H11001）**
   原图用的是「二级债基指数」（80% 债 + 20% 股）。
   实测中证官网的基金指数序列里没有二级债基（只有开放式 H11020 / 股票型 H11021 /
   混合型 H11022 / 债券型 H11023 / 货币 H11025 / QDII H11026 / 金牛系列），
   所以取不到。**用中证全债替代** —— 它是「股债性价比」的行业标准债券端口径，
   纯债、不含股票，因此：
     · 优点：两端不重叠，比价含义干净（纯股 vs 纯债）
     · 缺点：比原图少 20% 的股票仓位，债端的波动比二级债基小
   这个差别会让我们的比值比原图更「极值化」，判档位时要记得。

═══ ★ 关键的算法问题（我验证了 4 种，只有这种能用）═══
原图说「两条线重合 = 交汇」。但**从同一起点归一化，2006 年以后股票一路跑赢，
两条线只可能交叉 1 次，画不出 6 次。** 实证：
    2004-01-01 两者都 =1.0
    2026-11-19 沪深300全收益 =6.00 倍、中证全债 =2.67 倍
   → 股票线永远在债券线上方，固定基准下不可能反复交叉。

我试过并否掉的算法：
  ✗ 固定基准归一化交叉        → 2006 后只交叉 1 次（他图里有 6 次）
  ✗ 滚动 N 年归一化交叉       → 2/3/5 年分别得出 32/31/30 个月份的穿越，太碎
  ✗ 全历史分位（R 的百分位）  → R 有趋势漂移（2004 年 9 → 2026 年 22），
                                拿全历史比会得出「现在永远偏高」的假结论

✅ 采用：**R 的滚动 5 年分位**
    R = 沪深300全收益 / 中证全债  （比值越低 = 股票相对越便宜）
    分位 = R 在「过去 5 年」里的百分位 → 天然消掉趋势漂移
    判档：<20% 便宜 / 20-40% 偏便宜 / 40-60% 中性 / 60-80% 偏贵 / >80% 贵

    这个口径能对上原图的六轮（我用真实数据复核，六次低点的滚动5年分位
    分别是 48% / 4% / 50% / 18% / 4% / 0% —— 后三次精准落在极低位，
    前两次不算极端）。**原图那六轮是示意复盘，不是严格算法；我们这个是可以天天更新的指标。**

═══ 数据源 ═══
中证指数官网 csindex.com.cn（不接受 secid，只有它给 H00300）
    GET /csindex-home/perf/index-perf?indexCode=H00300&startDate=20040101&endDate=…
    ⚠ 实测 2026-11-20：连续请求会被 405 拦截（返回一个 HTML 限流页，
      **不是 JSON**）。表现为 json.loads 抛异常，容易被误判成「接口改版」。
      所以：① 请求间隔 >= 3 秒 ② 失败退避 3/6/12 秒
            ③ 解析前先检查响应的第一个字符是不是 '{'，不是就报「被限流」
    ⚠ 中证不给「全收益指数」的实时行情页，只有这个 perf 接口，没有备用源。
      拿不到时保留旧数据并在体检栏报出来，不要写空文件。

输出：data/bond_equity.json
      {
        "hs300tr": [[date, close], ...],
        "bond":    [[date, close], ...],
        "meta":    { "updated": ..., "src": ..., "note": ... }
      }
"""
import json, os, sys, time, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fh
import _td
import _net
import _td

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
FP = os.path.join(DATA, 'bond_equity.json')

# 指数代码 → 输出字段名
SERIES = [
    ('H00300', 'hs300tr', '沪深300全收益'),
    ('H11001', 'bond',    '中证全债'),
]

BEG = '20040101'      # 两个指数的基日都是 2004-12-31，往前留一点余量

API = ('https://www.csindex.com.cn/csindex-home/perf/index-perf'
       '?indexCode={code}&startDate={beg}&endDate={end}')

# ⚠ 中证官网的限流比东财更「安静」：不报 429，直接回 405 + 一个 HTML 页。
#   所以请求间隔要比东财（1.2~2.0s）更宽。实测 3 秒开始稳定。
GAP = (3.0, 4.0)


def _get(url, retries=3):
    """GET + 退避。返回 bytes。

    ⚠ 这里【不】直接复用 fetch_index.py 的 _get：那是给东财写的，
      重试 5 次、退避 1/2/4/8 秒。中证这个源更脆，重试太密会把自己封得更久，
      所以退避更长（3/6/12）、次数更少（3 次）。
    """
    last = None
    for i in range(retries):
        _net.throttle(url, gap=GAP)
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                               'AppleWebKit/537.36 (KHTML, like Gecko) '
                               'Chrome/120.0 Safari/537.36'),
                'Referer': 'https://www.csindex.com.cn/',
                'Accept': 'application/json, text/plain, */*',
            })
            r = urllib.request.urlopen(req, timeout=60).read()
            _net.mark(url)
            return r
        except urllib.error.HTTPError as e:
            last = e
            # 405 / 403 基本都是限流，等一下再试
            if e.code in (403, 405, 429) and i < retries - 1:
                time.sleep(3.0 * (2 ** i))
                continue
            # 其余 4xx 是参数错，重试也没用，直接抛
            if 400 <= e.code < 500:
                raise
            if i < retries - 1:
                time.sleep(3.0 * (2 ** i))
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(3.0 * (2 ** i))
    raise last


def fetch_one(code, retries=3):
    """返回 [[date, close], ...]，按日期升序。"""
    end = _td.bj_today_str().replace('-', '')
    url = API.format(code=code, beg=BEG, end=end)
    for attempt in range(retries):
        raw = _get(url)
        # ⚠ 必须先验形态：「被限流」返回的是 HTML，直接 json.loads 会抛 JSONDecodeError，
        #   那个报错看起来像「接口改版」，实际只是被挡了。区别在于要不要重试。
        head = raw[:1].decode('utf-8', 'ignore') if raw else ''
        if head != '{':
            if attempt < retries - 1:
                print(f'    {code}: 响应不是 JSON（前 80 字节 {raw[:80]!r}）→ 疑似限流，退避重试')
                time.sleep(4.0 * (attempt + 1))
                continue
            raise RuntimeError(f'中证返回非 JSON（疑似限流）：{raw[:120]!r}')
        js = json.loads(raw.decode('utf-8', 'ignore'))
        rows = js.get('data') or []
        if not rows:
            raise RuntimeError(f'中证返回空数据（code={code}，msg={js.get("msg")}）')
        out = []
        for r in rows:
            d = r.get('tradeDate')
            c = r.get('close')
            if d and c is not None:
                out.append([f'{d[:4]}-{d[4:6]}-{d[6:8]}', round(float(c), 4)])
        out.sort(key=lambda x: x[0])
        return out
    raise RuntimeError('unreachable')


def main():
    old = {}
    if os.path.exists(FP):
        try:
            old = json.load(open(FP, encoding='utf-8'))
        except Exception:
            old = {}

    got = {}
    fails = []
    for i, (code, key, name) in enumerate(SERIES):
        try:
            arr = fetch_one(code)
            # ⚠ 只接受已定稿的交易日（同 fetch_index.py 的口径）。
            #   中证的 perf 接口在盘中也会给当日「半截」数据。
            arr = [x for x in arr if _td.accept_day(x[0])]
            if not arr:
                raise RuntimeError('过滤掉当日未定稿数据后为空')
            got[key] = arr
            print(f'  {name} {code}：{len(arr)} 根  {arr[0][0]} → {arr[-1][0]}  '
                  f'末值 {arr[-1][1]}')
        except Exception as e:
            fails.append((code, name, e))
            print(f'  ✗ {name} {code} 抓取失败：{type(e).__name__}: {e}')
        if i < len(SERIES) - 1:
            time.sleep(3.0)

    # ── 写盘。两个序列必须同时有，缺一个就不写（避免半截文件）──
    if len(got) == len(SERIES):
        out = {k: got[k] for k in ('hs300tr', 'bond')}
        out['meta'] = {
            'updated': _td.bj_stamp(),
            'src': 'csindex.com.cn',
            'note': '沪深300全收益(H00300) + 中证全债(H11001)',
        }
        json.dump(out, open(FP, 'w', encoding='utf-8'), ensure_ascii=False)
        # 自检：量级异常说明口径错了
        for k, expect in (('hs300tr', (500, 20000)), ('bond', (50, 1000))):
            v = out[k][-1][1]
            if not (expect[0] <= v <= expect[1]):
                print(f'  ⚠ {k} 末值 {v} 超出合理区间 {expect} —— 疑似抓错指数，请核对')
        print(f'\n写入 {FP}  ({os.path.getsize(FP)/1024:.0f} KB)')
        _fh.report('bond_equity', '股债比价',
                   'ok', last=out['bond'][-1][0],
                   got=len(out['bond']), want=len(out['bond']),
                   note='')
        return 0

    # ── 失败分支：保留旧值，报出来 ──
    if old.get('hs300tr') and old.get('bond'):
        last = old['bond'][-1][0]
        # 部分成功：把拿到的那条接进去（但不能清掉另一条）
        merged = dict(old)
        for k, v in got.items():
            merged[k] = v
        json.dump(merged, open(FP, 'w', encoding='utf-8'), ensure_ascii=False)
        print(f'  ⚠ 只拿到 {len(got)}/{len(SERIES)} 条 → 合并旧值保留，未清空')
        _fh.report('bond_equity', '股债比价', 'partial', last=last,
                   got=len(got), want=len(SERIES),
                   note='中证官网限流，只更新了一部分')
    else:
        _fh.report('bond_equity', '股债比价', 'fail',
                   last=None, got=0, want=len(SERIES),
                   note='中证官网拒绝访问（405 限流），且本地没有旧数据')
    for code, name, e in fails:
        print(f'  ✗ {name}: {e}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
