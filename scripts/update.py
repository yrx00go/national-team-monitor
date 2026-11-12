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

用法：
    python3 scripts/update.py            # 更新最近 4 天
    python3 scripts/update.py --full     # 连带重算全部衍生数据 + 重建 HTML
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


def main():
    full = '--full' in sys.argv
    fails = []          # 记录失败步骤，最后汇总决定退出码

    # 1. 份额（核心，失败即中止：没有份额后面全没意义）
    run('fetch_etf.py')

    # 2. 判断今天到底有没有新数据 —— 决定后面是否继续
    fp = os.path.join(DATA, 'shares.json')
    shares = json.load(open(fp, encoding='utf-8'))
    sse_days = sorted(k for k, v in shares.items() if '510300' in v)
    latest = sse_days[-1] if sse_days else None
    today = date.today().isoformat()
    print(f'\n最新交易日 {latest}　今天 {today}　'
          f'{"有更新" if latest == today else "无新数据（非交易日或尚未发布）"}')

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
    if not run('compute_demo.py', optional=True):
        fails.append('compute_demo.py（数据未重算，页面会用上一次的结果）')
    if not run('fetch_dividend.py', optional=True):
        fails.append('fetch_dividend.py（股息率未更新）')

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
