#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""请求节流 —— 全站唯一的对外请求闸门。

⚠ 为什么必须有它（2026-11-12 晚，实测定性）：

   第四次 CI 里加了探针，在抓取前后各打一次同样的四个请求：

     抓取【前】（干净 IP、本 job 零请求）
       fqt=0 近端  ok  0.96s   47 根
       fqt=1 近端  ok  0.63s   47 根
       fqt=0 全量  ok  1.67s 1906 根      ← 连 1906 根都通
       fqt=1 全量  ok  1.69s 1906 根

     抓取【后】（同一个 runner，跑完一整轮）
       四个全部 broken 0.60s  RemoteDisconnected

   **结论：**
     · fqt=0 和 fqt=1 完全一致 → 「前复权被针对」证伪
     · 干净 IP 上连 1906 根的响应都通 → 「体积太大被掐」证伪
     · 0.60s 就断（不是 20s 超时）= 立刻拒绝 → 是限流/封禁，不是慢
     · 唯一变量是「之前打了多少请求」

   而 update.py 跑一轮要打 **52 次东财**，全部串行在几分钟内完成。
   所以这不是「偶发网络抖动」，是**结构性超额**。

⚠ 我之前在这件事上连错三次，记下来别再犯：
   ① 第一次归因为「重试次数不够」→ 加到大 5 次，反而更快耗光配额
   ② 第二次归因为「份额把自己打进限流」→ 只对了一半，qfq 仍 0/10
   ③ 第三次准备归因为「fqt=1 被针对」→ 探针直接证伪
   **三次都是「想出一个合理故事就动手改」，而不是先测。**
   这个模块存在的意义，就是让第四次动手之前先看数据。

设计：
  · 同一个域名下，两次请求至少间隔 MIN_GAP 秒，并加随机抖动。
    ⚠ 抖动不是「显得礼貌」，是因为**固定间隔本身就是机器指纹**。
      原来脚本里写 `time.sleep(0.3)`、`time.sleep(0.4)`，
      每个请求之间的间隔方差极小，比「完全不睡」更容易被识别。
  · 域名分开计时。打上交所不该让东财那边跟着等。
  · 计数落在一个进程级变量里，方便最后打印账单。
"""
import atexit
import random
import time
import urllib.parse

# ⚠ 这两个数是估的，不是测出来的，所以要能改。
#   探针显示干净 IP 前 4 次全通，但没探出「第几次开始断」——
#   要做那个实验得连打 20 次，反而会把自己的 IP 打黑，不值。
#   先取保守值：1.2~2.0 秒。一轮 52 次 ≈ 80 秒额外开销，可接受。
GAP_MIN = 1.2
GAP_MAX = 2.0

_last = {}      # host -> 上次请求结束的时刻
_count = {}     # host -> 本次进程内累计次数


def throttle(url, gap=None):
    """在打 url 之前调用。会按域名补足间隔。

    ⚠ 给同一域名连续打 N 次时，必须每次都调；只在循环开头调一次等于没调。
    """
    host = urllib.parse.urlsplit(url).netloc or '_'
    lo, hi = gap if gap else (GAP_MIN, GAP_MAX)
    prev = _last.get(host)
    if prev is not None:
        need = random.uniform(lo, hi) - (time.time() - prev)
        if need > 0:
            time.sleep(need)
    _count[host] = _count.get(host, 0) + 1


def mark(url):
    """请求【结束】后调用，记录时刻。

    和 throttle 分开是因为：间隔应该从「上一次结束」算到「这一次开始」，
    而不是「上一次开始」—— 否则一个慢请求会被误判成已经等够了。
    """
    host = urllib.parse.urlsplit(url).netloc or '_'
    _last[host] = time.time()


def bill():
    """本次进程一共打了多少请求，按域名分。

    ⚠ 这个数字很重要，但以前从来没被打印过。
      出现「0/10」时第一句话应该是「这轮总共打了多少次」——
      没有这个数，就只能猜（而我猜错了三次）。
    """
    return dict(_count)


def print_bill(tag=''):
    b = bill()
    if not b:
        return
    total = sum(b.values())
    parts = '　'.join(f'{k} {v} 次' for k, v in sorted(b.items(), key=lambda x: -x[1]))
    print(f'  [请求账单{("·" + tag) if tag else ""}] 共 {total} 次　{parts}')


def _atexit_bill():
    """进程退出时自动打印账单 —— 只在真打过请求时打。

    ⚠ 为什么用 atexit 而不是让每个脚本末尾自己调：
       计数是**进程级**的。update.py 是用 subprocess 一个个跑脚本，
       子进程一退出，它自己的计数就没了，父进程拿不到。
       五个抓取脚本各加一行「末尾打印」当然也行，但那又是一条
       「在 N 个地方各写一遍」的规则 —— 今天已经因为这种写法炸过一次
       （见 _td.py：`>= today` 写了三遍，改一处漏两处）。

       注册在模块导入时，谁 import 了 _net 谁就自动有账单，零调用成本。

    ⚠ 只统计【真正发出去的请求】。全失败的那一轮账单反而最高，
       那正是最需要看到的数字 —— 「0/10 但打了 60 次」和
       「0/10 且只打了 6 次」是完全不同的两个问题。
    """
    print_bill()


atexit.register(_atexit_bill)
