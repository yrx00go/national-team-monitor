#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日常更新入口 —— GitHub Actions 和本地都跑这一个。

设计原则：
  1. 幂等。重复跑同一天不会写重复数据，也不会因为"今天没有新交易日"而失败退出。
  2. 单步失败不回滚已完成的部分。抓取失败只是缺今天的数据，不能把昨天的一起弄丢。
  3. 明确区分两种"没数据"：
       · 非交易日（上交所无当日数据）→ 正常，退出码 0
       · 抓取失败（网络/限速）→ 退出码 1，让 Actions 报警
     把这两种混为一谈，是定时任务最常见的坑：非交易日天天报警，报警就没人看了。
  4. 【一天四个时间点，别指望一次抓全】2026-11-16 起：
       · 18:00 CST —— 收盘后 3 小时，出「价格 / 指数 / 量能 / 情绪 / K线」。
         此时上交所份额还没发布，所以跳过它（--after-close）。
       · 21:30 CST —— 只补份额（--shares-only）。
       · 23:30 CST —— 再补一次份额（--shares-only）。官方承诺 23:00 更新，这是保险。
       · 08:10 CST（次日）—— 兜底，全流程。
     为什么要分这么多次而不是「18:00 一次抓全」：份额是收盘后 20 点以后才发布的，
     18:00 抓必定是空 —— 这不是抓取失败，是源头还没有。实测见 main() 里的注释。
     为什么补份额要用 --shares-only 而不是重跑全流程：价格/情绪/K 线 18:00 后就定盘了，
     21:30 再抓一遍拿到的是同一份数据；而东财已经在拒绝我们（每次都走腾讯兜底），
     重复请求只会让限流更严重。

用法：
    python3 scripts/update.py                   # 更新最近 4 天
    python3 scripts/update.py --full            # 连带重算全部衍生数据 + 重建 HTML
    python3 scripts/update.py --after-close     # 18:00 那次：跳过份额（那时还没发布）
    python3 scripts/update.py --shares-only     # 21:30/23:30 那次：只补份额 + 重算重建
"""
import json, os, subprocess, sys
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = os.path.join(BASE, 'scripts')
DATA = os.path.join(BASE, 'data')


def run(script, *args, optional=False):
    cmd = [sys.executable, os.path.join(S, script), *args]
    print(f'\n▶ {script} {" ".join(args)}')
    r = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True)
    out = (r.stdout or '') + (r.stderr or '')
    print(out.strip()[-2000:] if out else '(无输出)')
    if r.returncode != 0:
        if optional:
            print(f'  ⚠ {script} 失败（可选步骤，继续）')
            return False
        raise SystemExit(f'{script} 失败，退出码 {r.returncode}')
    return True


def run_shares_only_tail():
    """补份额专用的收尾：重算派生数据 + 重建页面。

    ⚠ 为什么单独抽出来而不是复用 main() 的尾部：
       main() 的尾部顺序里夹着一堆「依赖前面抓取结果」的步骤
       （compute_demo 必须读 sentiment.json 之类）。补份额那次前面什么都没抓，
       按 main() 走一遍会让人以为「那些数据是这次抓的」，排查时误导。
       这里只做两件事，意图明确：份额变了 → 重算 → 重建。

    ⚠ 这两步一步都不能省：
      · 份额变了，净流入 / 存活率 / 杠杆水位全都得跟着重算，否则数字自相矛盾；
      · compute_demo 换了数据但页面没重建，线上就还是旧的
        （2026-11-12 踩过：顺序错开一个位置，体检栏少两项且不报错）。
    """
    fails = []
    if not run('compute_demo.py', optional=True):
        fails.append('compute_demo.py（份额已更新但派生数据未重算）')
    # 重建页面失败必须硬停 —— 没有页面就没有交付物
    run('build_demo.py', '--offline')
    demo = json.load(open(os.path.join(DATA, 'demo.json'), encoding='utf-8'))
    print(f'\n=== 完成（补份额）===  最新 {demo["latest_date"]}  '
          f'交易日总数 {len(demo["days"])}')
    return fails


def main():
    full = '--full' in sys.argv
    # 18:00 那次用。见文件头「两个时间点」的说明。
    after_close = '--after-close' in sys.argv
    # 21:30 / 23:30 那两次用：只补份额，不重复抓价格/情绪/K线。
    # 见下面「为什么补份额不用跑全流程」。
    shares_only = '--shares-only' in sys.argv
    fails = []          # 记录失败步骤，最后汇总决定退出码

    # 1. 份额（核心，失败即中止：没有份额后面全没意义）
    #
    # ⚠⚠ 2026-11-16 新增 --after-close：18:00 那次【不跑这一步】。
    #
    #   实测证据（都是本机直接打接口）：
    #     · 11-13 20:10 左右跑 → 0 条；20:53 再跑 → 拿到 11-13（924 条）
    #     · 11-16 20:10 / 20:20 / 20:28 / 21:05 / 21:13 全部 0 条
    #     · 上交所 ETF 之家自己标注「总规模数据每交易日 23:00 更新」，
    #       而且官网页面上当天一直显示「数据日期:2026-11-13」
    #   结论：份额在收盘后【20 点以后】才发布，18:00 必然没有。
    #
    #   为什么是「跳过」而不是「跑了拿不到就算了」：
    #   跑的话会在 fetch_status.json 写一条「今日无新数据（非交易日或尚未发布）」，
    #   而体检栏读的是最后一条 —— 于是每天 18:00 页面上都会挂一条黄字，
    #   看起来像出事，实际是正常的。跳绳比写假话好：
    #   不跑，体检栏就继续显示上一次（昨晚）的真实结果，那条才是对的。
    if after_close:
        print('\n▶ 跳过 fetch_etf.py（--after-close：份额 20 点后才发布）')
        shares_ok = True
    else:
        shares_ok = run('fetch_etf.py')

    # 2. 判断今天到底有没有新数据 —— 决定后面是否继续
    fp = os.path.join(DATA, 'shares.json')
    shares = json.load(open(fp, encoding='utf-8'))
    sse_days = sorted(k for k, v in shares.items() if '510300' in v)
    latest = sse_days[-1] if sse_days else None
    today = date.today().isoformat()
    print(f'\n最新交易日 {latest}　今天 {today}　'
          f'{"有更新" if latest == today else "无新数据（非交易日或尚未发布）"}')
    if after_close:
        print('  ↑ --after-close 模式下这是预期的：份额今晚会由后面的运行补齐，'
              '本次只更新价格/指数/情绪。')

    # ── 3. 只补份额时，直接跳到「重算 + 重建」，不重复抓其他源 ──
    #
    # ⚠ 为什么补份额那次不该跑全流程（2026-11-16 加的）：
    #   一天四次全跑 = 价格、指数、情绪、K线各自被重复抓 4 遍。
    #   这些源里东财已经在拒绝我们（price_bfq 每次都是「东财失败 → 腾讯兜底」），
    #   重复请求只会让限流更严重。而且**除了份额，其他东西 18:00 之后就定盘了**，
    #   21:30 再抓一遍拿到的还是同一份数据，纯属浪费。
    #
    #   但「重算 + 重建页面」这一步不能省：份额变了，净流入/杠杆/存活率全都得跟着重算，
    #   否则数据更新了、页面还是旧的（这个坑在 2026-11-12 踩过，见下面 compute_demo 的注释）。
    if shares_only:
        print('\n▶ --shares-only：跳过价格/指数/情绪/K线等抓取，直接重算 + 重建')
        if latest != today:
            # 份额还没发布。不重算、不重建 —— 数据一个字都没变，
            # 重建只会产出一个内容相同的文件，然后触发一次无意义的提交和部署。
            print(f'  ⚠ 今天({today})的份额还没发布，本次不做任何改动。'
                  f'最新仍是 {latest}。')
            print('  （这不是失败：上交所 23:00 前发布都算正常，'
                  '后面还有一次运行会再试）')
            return 0
        print(f'  ✓ 拿到 {today} 的份额，开始重算派生数据')
        fails = run_shares_only_tail()
        return 1 if fails else 0

    # ⚠ 这里原来把 3~5 步全标成 optional=True，意思是「失败也继续」。
    # 但那样等于：价格抓不到 → 净流入用旧价算 → 页面照常发布，看起来一切正常。
    # 违背了本文件开头写的第 3 条设计原则。改成：失败要记录，最后以非零退出码报警，
    # 但仍然继续跑完剩下的步骤（不让一个可选步骤拖垮整条链）。
    if not run('fetch_price_bfq.py', optional=True):
        fails.append('fetch_price_bfq.py（价格拿不到，净流入会用旧价）')
    # 上证指数：组合页左轴。它失败不影响净流入，但点位线会停在旧日期，
    # 而 compute_demo.py 会用前值顶格补齐，图上不会出现缺口（只会「横着不动」）。
    # 所以必须报警，不能静默。
    if not run('fetch_index.py', optional=True):
        fails.append('fetch_index.py（上证指数/两市成交额未更新，量能面板会用旧值）')
    # 两融余额：量能与杠杆面板的「杠杆」那一半。T+1 晚间公布，
    # 所以今天没拿到是正常的（昨天的数据仍在），不能当成失败反复报警 ——
    # 但如果连续多天拿不到，面板就会停在旧日期，所以这里仍然记一笔。
    if not run('fetch_margin.py', optional=True):
        fails.append('fetch_margin.py（两融未更新，杠杆面板会用旧值）')

    # ⚠ 顺序很关键：compute_demo.py 必须排在【所有抓取脚本之后】。
    #   2026-11-12 实测踩到：它原来排在这里（第 5），而 fetch_dividend / fetch_history
    #   排在第 6、7。于是那两项的体检结果永远进不了当天页面 ——
    #   data/fetch_status.json 里有 6 项，demo.json 的 health 只有 4 项，
    #   页面上少了两项，而且不会报错。**顺序错了，等于给体检栏开了个后门。**
    #   依赖关系本来是「先抓完 → 再算 → 再出体检」，写成线性的就该按这个顺序排。
    if not run('fetch_dividend.py', optional=True):
        fails.append('fetch_dividend.py（股息率未更新）')

    # 前复权收盘价 —— 页面画价格曲线用的就是它。
    # ⚠ 2026-11-12 才发现它【完全不在这条链上】：只有一次性回补脚本
    #   fetch_history.py 会写它，而那个脚本的判据是「起点够早就跳过」，
    #   于是回补一次成功后永久不再更新。价格曲线会一天天变旧，页面上没有任何提示。
    #
    # ⚠ 不带 --refresh-prices —— 这里踩过一个反复：
    #   上午的修法是「每天无条件全量重抓 10 只 × 1900 根」，理由是「前复权一旦分红
    #   历史会整体重算，增量接不上」。逻辑对，但代价是把偶发失败变成了【每天一次风险】：
    #   实测当天 CI 里 fetch_price_bfq 抓到 8/10，而这个全量的 0/10。
    #   现在的做法是「增量 + 重叠校验」：日常只抓近 40 天，拿重叠区逐日比对，
    #   对得上就增量续接，对不上（= 发生过除权）才自动转全量。
    #   判据从「时间到了」换成「数据自己说要不要」，既不赌，也不漏分红。
    #
    # ⚠⚠ 2026-11-12 晚：这里改成【只补份额，不抓价格】。
    #
    #   第五轮 CI 的请求账单把配额算清楚了：
    #       fetch_price_bfq  10 次 push2his → 10/10
    #       fetch_index       3 次 push2his → 3/3
    #       fetch_history    48 次 push2his → 1/10   ← 排第 6 位，配额早就没了
    #   同一个域名前 13 次全通、第 14 次起几乎全断。也就是说：
    #   **qfq 抓不到的真正原因是它排在「已经把配额花光的那个 job 里」。**
    #
    #   修法不是继续调顺序，而是把它挪到【另一个 job】去 ——
    #   GitHub Actions 的 job 跑在各自的 runner 上，出口 IP 不同、配额各算各的。
    #
    #   所以这里只跑 --shares-only（上交所份额，另一个域名，不抢东财配额），
    #   价格段由 daily.yml 里那个独立的 qfq job 负责。
    if not run('fetch_history.py', '--shares-only', optional=True):
        fails.append('fetch_history.py --shares-only（份额回补未完成）')

    # 市场共识层（媒体 + 股吧 + 热度 + 估值分位 + 当天大盘）。
    #
    # ⚠ 必须排在 compute_demo.py 之前 —— 它写 data/sentiment.json，
    #   而 compute_demo 要把这个文件读进 demo.json 的 sentiment 字段。
    #   排到后面就是「今天抓的共识，明天才上页面」，
    #   而且因为日期字段会显示昨天的日期，看起来又像「今天没更新」，
    #   排查时极容易怀疑到抓取脚本头上而不是顺序上。
    #
    # ⚠ 它不抢东财 push2his 的配额：走的是 search-api（另一个服务）、
    #   dq.10jqka、danjuanfunds、guba、toutiao、qt.gtimg.cn，全部不同域名。
    #   所以放在 fetch_history --shares-only 之后不会加剧那边的限流。
    #
    # ⚠ 标 optional 的理由和上面几条不同：这一层是「别人在怎么说」，
    #   拿不到不影响事实层（份额/价格/流入）的正确性，页面会显示
    #   「还没有抓过这一层的数据」。宁可少一栏，不能用旧共识冒充今天。
    if not run('fetch_sentiment.py', optional=True):
        fails.append('fetch_sentiment.py（市场共识未更新，共识栏会停在旧日期）')

    # 散户声音（新浪股吧的「我看多/我看空」投票帖）。
    #
    # ⚠ 必须排在 fetch_sentiment.py【之后】：retail.py 是读-改-写
    #   data/sentiment.json，往里加一个 retail 键。而 fetch_sentiment.py
    #   每次都会整个重写该文件 —— 顺序反了，昨天抓的散户数据会被今天
    #   重写的文件抹掉，且不会报错。
    #
    # ⚠ 也必须排在 compute_demo.py 之前，理由同上一段（它要把
    #   sentiment.json 读进 demo.json）。
    #
    # ⚠ 为什么不并进 fetch_sentiment.py：那一栏是「机构/媒体在怎么说」，
    #   这一栏是「散户在怎么想」，两者数据源、口径、可信度都不一样。
    #   混在一个脚本里，迟早会有人把散户投票算进「机构一致度」。
    if not run('retail.py', optional=True):
        fails.append('retail.py（散户声音未更新，那一块会显示旧日期或空）')

    # K 线 OHLC（上证指数 + 黄金 ETF）。
    #
    # ⚠ 必须排在 compute_demo.py 之前 —— 同 sentiment 的理由。
    #
    # ⚠ 它走 web.ifzq.gtimg.cn（腾讯），不抢东财 push2his 的配额，
    #   所以放在这一串东财脚本之后不会加剧那边的限流。
    #   实测这个接口比东财宽容得多（连打 6 次全通），但仍过 _net 节流。
    if not run('fetch_kline.py', optional=True):
        fails.append('fetch_kline.py（K 线未更新，蜡烛图会停在旧日期）')

    # 黄金共识：Kitco 周度调查（机构 + 散户分开统计），经中金网转载。
    #
    # ⚠ 为什么不是「数中金网名家点金的标题方向」：
    #   那批稿子是**日内交易策略**，同一篇正文里同时给多单和空单
    #   （「3869-70 轻仓多，损3852」+「3905-10 空单，止损3933」），
    #   标题落哪个方向取决于当天行情走到哪。更硬的证据是
    #   许安丰 11-15 与 11-16 两篇正文一字不差，只改了标题日期。
    #   用它算出来的 up 占比与金价涨跌 corr=+0.346（仅 14 个交易日），
    #   撑不住一条曲线。详见 fetch_gold_survey.py 文件头。
    #
    # ⚠ 频率是**每周 1 条**。45 天语料里只有 4 条调查。
    #   这是源头事实，不是抓取失败 —— 所以它更新「成功但没有新数据」
    #   是常态，不能每次报警。只有真出错（返回码非 0）才记 fails。
    if not run('fetch_gold_survey.py', optional=True):
        fails.append('fetch_gold_survey.py（黄金调查未更新）')

    # 每日共识快照存档。**顺序必须在上面全部抓完之后** ——
    # 它读的是 gold_survey.json / sentiment.json 的最终结果。
    #
    # ⚠ 这一步是整个「从今天开始记录」的落点：
    #   所有源的共识回溯深度都有硬上限（东财 ~30 天、股吧 ~3 天、
    #   中金网 ~45 天）。今天不存，三个月后回头还是只有这 45 天。
    #   图只是它的一个视图，这个文件才是长期资产。
    if not run('snapshot_consensus.py', optional=True):
        fails.append('snapshot_consensus.py（今日共识快照未写入，'
                     '历史会出现缺口）')

    # 放在最后：它要读上面所有抓取步骤写下的 fetch_status.json
    if not run('compute_demo.py', optional=True):
        fails.append('compute_demo.py（数据未重算，页面会用上一次的结果）')

    # 6. 重建 HTML —— 这一步失败必须硬停，没有页面就没有交付物
    #
    # ⚠ 必须带 --offline。2026-11-12 才发现：这里原来写的是 run('build_demo.py')，
    #   而 build_demo.py 默认走 CDN（1,762 KB），只有 --offline 才把 Chart.js 内联（1,967 KB）。
    #   于是每天 CI 跑完 → 用 CDN 版把仓库里的离线版覆盖掉 → 再 commit 推回仓库。
    #   本地明明构建的是离线版，线上每天变回 CDN 版，且 diff 里只有这一处 20 行的差别，
    #   极容易被「data: 更新至 xxx」这种提交信息盖过去。
    #   直接后果：「加到手机桌面」后在地铁 / 弱网 / CDN 被拦时，文字全在、图全空。
    run('build_demo.py', '--offline')

    # 7. 报告
    demo = json.load(open(os.path.join(DATA, 'demo.json'), encoding='utf-8'))
    print(f'\n=== 完成 ===  最新 {demo["latest_date"]}  交易日总数 {len(demo["days"])}')

    if fails:
        print('\n=== 有步骤失败（数据可能不完整，已发布的是降级版本）===')
        for f in fails:
            print(f'  ✗ {f}')
        return 1          # 非零退出码 = Actions 会标红报警
    return 0


if __name__ == '__main__':
    sys.exit(main())
