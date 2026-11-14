#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预判层测试。全部离线，不联网。

这个文件的每一条断言，都对应一次**实测踩到的错**。
写测试不是为了覆盖代码，是为了把那几个错钉死在这里 ——
只要有人（包括未来的我）把它改回去，这里就会红。
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'scripts'))
import forecast as F                                          # noqa: E402

_fail = []
_n = 0


def chk(name, got, want):
    global _n
    _n += 1
    ok = (got == want) if not callable(want) else want(got)
    print(f'{"  ok " if ok else "  !! "}{name}')
    if not ok:
        print(f'        实际 {got!r}　期望 {want!r}')
        _fail.append(name)


def section(t):
    print(f'\n=== {t} ===')


# ══════════════════════════════════════════════════════════════
section('1. 标的筛选（黄金页面曾被海外个股目标价灌爆）')

# 实测原文：这三条曾在「黄金」语料池里，因为关键词「目标价」匹配到了整个海外研报库
chk('★★★ 巴克莱调康菲石油目标价 → 不是黄金',
    F.is_forecast('gold', '巴克莱将康菲石油目标价从150美元上调至165美元')[0], False)
chk('★★★ Bernstein 调英伟达目标价 → 不是黄金',
    F.is_forecast('gold', 'Bernstein将英伟达目标价从400美元下调至200美元')[0], False)
chk('★★★ 华虹宏力买入评级 → 不是黄金',
    F.is_forecast('gold', '第一上海：维持华虹宏力买入评级 目标价125港元')[0], False)
chk('★ 黄金目标价被下调 → 是黄金预判',
    F.is_forecast('gold', '高盛猛砍黄金目标价！全球央行仍在买买买')[0], True)
chk('★ A股后市展望 → 是A股预判',
    F.is_forecast('a_share', '十大券商看后市｜A股已处于底部位置')[0], True)
chk('★★ A股标题不该被黄金栏收走',
    F.is_forecast('gold', 'A股明年怎么走？多家龙头券商最新研判')[0], False)

# 剔除原因必须可区分：这三类问题不是一回事
chk('★ 剔除原因区分「不是这个标的」和「不是预判」',
    F.is_forecast('gold', '巴克莱下调英伟达目标价')[1], '不是这个标的')


# ══════════════════════════════════════════════════════════════
section('2. 预判 vs 事后描述（描述层 59% 是无效播报）')

chk('★★★ 收评播报不算预判',
    F.is_forecast('a_share', 'A股三大指数收跌 影视板块逆市大涨')[0], False)
chk('★★★ 开盘播报不算预判',
    F.is_forecast('a_share', 'A股开盘：三大指数集体高开')[0], False)
chk('★★ 涨停异动不算预判',
    F.is_forecast('a_share', 'A股涨停潮！002548垂直涨停')[0], False)
chk('★★ ETF 营销稿不算预判',
    F.is_forecast('a_share', '机构：中国资本市场有望保持稳中向好态势，沪深300ETF嘉实(159919)低位布局A股核心资产')[0],
    False)
chk('★ 无预判标记的纯事实也不收',
    F.is_forecast('a_share', '中际旭创，再度位居A股第一')[0], False)


# ══════════════════════════════════════════════════════════════
section('3. 方向判定必须用短语，不能用单词（最要紧的一组）')

# ★ 这三条是「字面有跌、意思看多」。用单词表会全判反
chk('★★★ 「回调空间有限」= 偏多（不是偏空）',
    F.direction('十大券商看后市｜A股已处于底部位置，进一步回调空间有限'), 'up')
chk('★★★ 「不宜过分恐慌」= 偏多',
    F.direction('十大券商看后市｜当前位置不宜过分恐慌 超跌反弹条件进一步改善'), 'up')
chk('★★★ 「最差时刻已过去」= 偏多',
    F.direction('十大券商把脉A股：底部在夯实，最差时刻已过去'), 'up')
chk('★★★ 「调整接近尾声」= 偏多，不能被「调整」抢先判空',
    F.direction('十大券商策略：市场调整接近尾声！积极信号已经开始出现'), 'up')
chk('★★ 「不必悲观」= 偏多',
    F.direction('中长期来看对A股市场不必悲观'), 'up')

# 反向：真的偏空不能被拗成偏多
chk('★★ 「上调目标价...上涨空间」的个股不算，但「高位/泡沫」要判空',
    F.direction('A股高位泡沫风险加剧'), 'down')
chk('★★ 「下调目标价」判空',
    F.direction('高盛猛砍黄金目标价')[0] if False else F.direction('机构下调金价目标价'), 'down')
chk('★ 「看空」明确判空', F.direction('券商看空A股后市'), 'down')
chk('★ 「有望/可期」判多', F.direction('A股有望迎来跨年行情'), 'up')

# 中性短语只在没有方向词时生效
chk('★★ 「静待右侧信号」= 无方向（不是偏多也不是偏空）',
    F.direction('浙商证券展望2027年投资机会：A股筑底整固，静待右侧信号'), 'flat')
chk('★★ 有方向优先于中性：「有望」压过「震荡」',
    F.direction('A股震荡中有望走强'), 'up')


# ══════════════════════════════════════════════════════════════
section('4. 词表对称性（不对称会让页面永远偏多）')

_up = len([p for p, d in F.PHR_UP if d == 'up'])
_dn = len([p for p, d in F.PHR_DOWN if d == 'down'])
chk('★★★ 偏多短语数与偏空短语数都 >= 25', (_up >= 25) and (_dn >= 25), True)
chk('★★★ 两侧数量差 <= 12（实测过差 40 就会系统性偏多）', abs(_up - _dn) <= 12, True)
chk('★★ 每个短语只属于一个方向（不许 up/down 都有）',
    len({p for p, d in F._ALL_PHR}), len(F._ALL_PHR))
chk('★★ 「回调空间有限」和「上涨空间有限」必须都在（成对）',
    ('回调空间有限' in dict(F._ALL_PHR)) and ('上涨空间有限' in dict(F._ALL_PHR)), True)


# ══════════════════════════════════════════════════════════════
section('5. 机构识别（曾把同一家机构的转载算成 4 家）')

chk('★★★ 申万宏源的稿子归到「申万宏源」，不归到媒体名',
    F._org_of('21世纪经济报道：申万宏源定调A股 明年春季行情可期')[0], '申万宏源')
chk('★★★ 同一机构换个媒体发，仍然归到同一家',
    F._org_of('财联社：申万宏源傅静涛 A股仍处两段论框架')[0], '申万宏源')
chk('★★ 两家不同媒体转述同一家机构 → 机构名必须相同',
    F._org_of('央广财经：申万宏源研判A股将迎第二阶段上涨')[0],
    F._org_of('上海证券报：申万宏源静待第二阶段上涨')[0])
chk('★★ 点不出机构的汇集稿单独归一类',
    F._org_of('十大券商看后市｜A股已处于底部位置')[0], '汇集稿（未点名机构）')
chk('★★ 汇集稿的 kind 是 digest，机构稿是 named',
    F._org_of('十大券商策略：转机将现')[1], 'digest')
chk('★ 高盛要能被认出来', F._org_of('高盛下调黄金目标价')[0], '高盛')


# ══════════════════════════════════════════════════════════════
section('6. 样本不足必须拒绝下结论（黄金曾拿 7 篇喊「偏多」）')

_base = {
    'label': '黄金', 'window_from': '2026-10-16', 'window_to': '2026-11-14',
    'window_days': 30, 'n_art': 38, 'a_dir': 7, 'a_up': 5, 'a_down': 2,
    'a_flat': 31, 'lean_txt': '偏多',
    # ⚠ 这两个字段是 caveats() 的入参，测试数据里必须给全。
    #   第一版没给，于是「无方向占比」那条断言拿到 None，
    #   `None >= 30` 抛 TypeError 被吞成 False —— 断言看起来是「功能坏了」，
    #   实际是**测试数据缺字段**。测试数据缺字段比断言写错更难查。
    'unknown_pct': 82, 'n_named': 3, 'n_org': 4,
}
_h = F.headline(_base)
chk('★★★ 只有 7 篇有方向 → 不许出现「偏多」这个词',
    '偏多' in _h.replace('5 多 / 2 空', ''), False)
chk('★★★ 必须明说「样本不足」', '样本不足' in _h, True)
chk('★★★ 必须写出门槛数字，让读者知道差多少', str(F.MIN_DIR) in _h, True)

# 样本够了才给倾向
_base2 = dict(_base, a_dir=41, a_up=39, a_down=2, a_flat=29, n_art=70,
              lean_txt='几乎一致偏多')
_h2 = F.headline(_base2)
chk('★★ 41 篇有方向 → 可以给倾向', '几乎一致偏多' in _h2, True)
chk('★★ 一句话里必须同时有分子和分母（39 篇 / 分母 41）',
    ('39' in _h2) and ('41' in _h2), True)
chk('★★ 一句话里必须写明「未给方向的 N 篇不计入」',
    '不计入' in _h2, True)

# 零样本 / 零方向
chk('★★★ 完全没抓到 → 必须说「不是没人预判，是我们没抓到」',
    '没抓到' in F.headline(dict(_base, n_art=0, a_dir=0)), True)
chk('★★ 抓到了但一条方向都没有 → 不许硬造倾向',
    '没有一篇给出明确方向' in F.headline(dict(_base, a_dir=0, a_up=0, a_down=0)), True)


# ══════════════════════════════════════════════════════════════
section('7. 口径必须写出「不能怎么读」')

_cv = F.caveats(_base)
_cv_txt = ' '.join(_cv)
chk('★★★ 必须点出「A股研报结构上就偏多」这个先验',
    '结构上就偏多' in _cv_txt, True)
chk('★★★ 必须写窗口起止，不能只给一个数',
    ('2026-10-16' in _cv_txt) and ('2026-11-14' in _cv_txt), True)
chk('★★★ 必须声明「这是读数不是信号」', '不是信号' in _cv_txt, True)
chk('★★ 无方向占比 >= 30% 时必须单独提示',
    '没给出方向' in _cv_txt, True)
chk('★★ 无方向占比很低时不提示这一条',
    '没给出方向' in ' '.join(F.caveats(dict(_base, unknown_pct=5))), False)
chk('★★ 点名的机构太少时必须解释是媒体写法、不是抓漏',
    '不是抓漏' in _cv_txt, True)


# ══════════════════════════════════════════════════════════════
section('8. 窗口配置（7 天和 30 天是两句话，不能混）')

chk('★★★ 黄金窗口必须比 A股 长（语料稀疏）',
    F.WINDOW_DAYS['gold'] > F.WINDOW_DAYS['a_share'], True)
chk('★★ 两个标的都要配窗口', sorted(F.WINDOW_DAYS.keys()), ['a_share', 'gold'])
chk('★★ 每个标的关键词池 >= 10 个（实测 4 个词只有 5 家机构）',
    all(len(v) >= 10 for v in F.QUERY.values()), True)
chk('★ 关键词池里不许出现「看空」这类预设立场的词（会自己造出偏空）',
    any('看空' in k or '看多' in k for v in F.QUERY.values() for k in v), False)


# ══════════════════════════════════════════════════════════════
section('9. 代码层面的红线')

_src = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'scripts', 'forecast.py'), encoding='utf-8').read()
# 剥掉注释和 docstring 再查 —— 否则设计说明里写着的「不能这样」会被当成违规
_body = re.sub(r'""".*?"""', '', _src, flags=re.S)
_body = re.sub(r"(?m)^\s*#.*$", '', _body)

chk('★★★ 不许出现「建议买/应该买」这类话术',
    any(w in _body for w in ['建议买入', '应该买', '推荐买']), False)
chk('★★★ 不许出现胜率/收益率这类伪回测说法',
    any(w in _body for w in ['胜率高达', '必胜', '稳赚']), False)
chk('★★ 必须用 pageSize=80（实测 20 太小，20 个词才能凑够数）',
    'page_size=80' in _src, True)
chk('★★ 不许按媒体名（src）当聚合成机构——那是转载量不是共识',
    "a.get('src') or '未知来源'" in _body, False)


print(f'\n{"="*56}')
if _fail:
    print(f'✗ {len(_fail)} 项失败 / 共 {_n} 项')
    for f in _fail:
        print('   ·', f)
    sys.exit(1)
print(f'✓ 全部通过　{_n} 项')
