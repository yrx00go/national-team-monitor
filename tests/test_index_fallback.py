#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指数兜底：腾讯当日快照（离线为主，联网仅一段可选校验）。

对应 2026-11-12 深夜的改动：fetch_index.py 在 CI 上连续多轮 **0/3 全挂**，
上证点位和两市成交额天天不更新 —— 而它是「净申赎 × 大盘位置」叠加图的
位置轴，不更新那张图就等于在骗人。

⚠ 这个兜底和 bfq 那个不一样，必须说清差别：
   腾讯指数接口能给**历史点位**（640 根），但**成交额只有当日快照**。
   只兜点位不兜成交额 → 会出现「点位到 11-12、成交额停在 11-11」的半截状态，
   比整体沿用旧值更难解释。所以这里**统一只补当日一根**：两样都有才用。

跑法：python3 tests/test_index_fallback.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'scripts'))

import fetch_index as m

ok_n = fail_n = 0


def chk(label, got, want):
    global ok_n, fail_n
    if got == want:
        ok_n += 1
        print(f'  ✅ {label}: {got!r}')
    else:
        fail_n += 1
        print(f'  ❌ {label}: 得到 {got!r}，应为 {want!r}')


print('\n## 结构：三处失败分支都必须接上兜底')

src = open(os.path.join(HERE, '..', 'scripts', 'fetch_index.py'), encoding='utf-8').read()
chk('★ 有 fetch_tx_snapshot', 'def fetch_tx_snapshot(' in src, True)
chk('★ 有 _apply_snapshot（三处共用，不各写一遍）', 'def _apply_snapshot(' in src, True)
# ⚠ 这三处曾经各写一遍 —— 7.26 的母题：改一处漏两处。
chk('★ 兜底被调用的次数（东财异常/返回空/只有盘中）', src.count('snap = fetch_tx_snapshot()'), 3)
# ⚠ 这里必须只数「调用」，不能数「出现」：
#   `_apply_snapshot(snap)` 这个字符串在 `def _apply_snapshot(snap):` 定义行里
#   也出现一次，直接 count 会得到 4。**检查「代码里调了几次」时，
#   定义和调用必须分开数** —— 否则为了通过测试只能去改函数名，方向就反了。
_calls = [l for l in src.split('\n')
          if '_apply_snapshot(' in l and not l.lstrip().startswith('def ')]
chk('★ _apply_snapshot 被调用 3 次（不含定义行）', len(_calls), 3)

print('\n## 只补一根：快照必须两样都有，缺一样就整体不用')

chk('★ 有「两样都有才用」的说明', '两样都有才用' in src, True)
# 日期不一致时必须放弃 —— 否则会写进一个「沪市 11-12、深市 11-11」的合计
chk('★ 两个市场日期不一致时放弃', '两个市场日期不一致' in src, True)

print('\n## 只追加，不覆盖（和 bfq 同一条纪律）')

chk('★ 只在日期更新时追加点位', "snap['date'] > seq[-1][0]" in src, True)
chk('★ 只在日期更新时追加成交额', "snap['date'] > tseq[-1][0]" in src, True)

print('\n## 兜底必须上屏')

chk('★ note 写明走了腾讯', '走腾讯当日快照' in src, True)
chk('★ 兜底失败时 note 说明', '腾讯快照也不可用' in src, True)

print('\n## 联网校验（可选，失败不算测试失败）')

try:
    snap = m.fetch_tx_snapshot()
    if not snap:
        print('  ⚠ 取不到快照，跳过交叉校验')
    else:
        print(f'  快照：{snap["date"]}  上证 {snap["sh"]}  '
              f'深成 {snap["sz"]}  两市 {snap["turnover"]:,.1f} 亿')
        chk('日期格式 YYYY-MM-DD', len(snap['date']), 10)
        chk('上证点位量级合理（1000–10000）', 1000 < snap['sh'] < 10000, True)
        chk('两市成交额量级合理（2000–60000 亿）', 2000 < snap['turnover'] < 60000, True)
        # ⚠ 成交额必须是「沪 + 深」的合计，不是单市。
        #   实测：沪 8,795 + 深 10,525 = 19,320；单市会落在 8千–1万亿。
        #   如果这里返回 < 12000，大概率只取了一个市场。
        chk('★ 成交额是两个市场之和（>12000 亿）', snap['turnover'] > 12000, True)
        # 与仓库里东财的口径对照（如果同一天）
        import json
        fp = os.path.join(HERE, '..', 'data', 'index_sh.json')
        if os.path.exists(fp):
            old = json.load(open(fp, encoding='utf-8'))
            ov = {d: v for d, v in (old.get('turnover') or [])}
            if snap['date'] in ov:
                diff = abs(ov[snap['date']] - snap['turnover'])
                chk(f'★ 与东财同日成交额差（{ov[snap["date"]]} vs {snap["turnover"]}）',
                    diff < 1.0, True)
            else:
                print(f'  （东财无 {snap["date"]} 的记录，跳过口径比对）')
except Exception as e:
    print(f'  ⚠ 联网校验跳过：{type(e).__name__}: {e}')

print(f'\n{"=" * 50}\n通过 {ok_n} 项，失败 {fail_n} 项')
sys.exit(0 if fail_n == 0 else 1)
