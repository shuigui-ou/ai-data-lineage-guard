#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""白名单效果实测：正向（降误报）+ 反向（不能漏真信号）。

方法论：不能只看"误报降了多少"，必须同时看"真信号漏了多少"。
只测正向 = 把闸门拆了也能得满分。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import verify_conclusions as VC
import verify_lineage as V

# ── 反向测试：这些数字必须**不能**被白名单放过 ──────────────
# （每一个都是真实的测量值/量级主张，放过就是假阴性）
MUST_FLAG = [
    ("下行样本量为 800", 800, "样本量：真实测量值"),
    ("效应量达到 0.32", 0.32, "效应量：真实测量值"),
    ("该关系贡献了 12% 的涨幅", 12, "百分比：真实测量值"),
    ("涉及 1563 家上市公司", 1563, "实体计数：真实测量值"),
    ("增长 3 倍", 3, "无约数词的倍数：是量级主张"),
    ("均值达到 45.6", 45.6, "均值：真实测量值"),
    ("涉及 5 个交易日", 5, "无时段标记的计数：不可判是窗口计数"),
    ("收益率为 8.05%", 8.05, "收益率：真实测量值"),
]

# ── 正向测试：这些数字应该被白名单放过（结构性叙述数字）────────
SHOULD_WHITELIST = [
    ("过去 3 年该关系稳定", 3, "时段计数"),
    ("最近 12 个月表现平稳", 12, "时段计数"),
    ("第 2 季度达到峰值", 2, "序数/排名"),
    ("前 5 名传导中表现突出", 5, "序数/排名"),
    ("Q2 窗口共覆盖 2 个交易日区间", 2, "季度标签"),
    ("2019 至 2026 年间保持同向", 2019, "年份/年份区间"),
    ("2026 年度窗口", 2026, "年份"),
    ("共覆盖 2 个交易日区间", 2, "窗口/区间计数"),
    ("覆盖 8 个年度窗口", 8, "窗口/区间计数"),
    ("上行效应增长约 3 倍", 3, "倍数(带约数词)"),
]


def check(text, target_val):
    """返回该文本中等于 target_val 的数字的白名单判定结果。"""
    for raw, val, ns, ne in VC.extract_numbers(text):
        if abs(val - target_val) < 1e-12:
            return VC.classify_descriptive_number(text, raw, val, ns, ne)
    return "NOT_FOUND"


print("=" * 68)
print("反向测试：真信号必须仍被捕获（不得被白名单放过）")
print("=" * 68)
miss = 0
for text, val, why in MUST_FLAG:
    r = check(text, val)
    ok = (r is None)
    if not ok:
        miss += 1
    print(f"  {'✅' if ok else '❌'} {text!r}")
    print(f"      期望=必须报  实际={'放过(' + str(r) + ')' if r else '会报'}   [{why}]")

print()
print("=" * 68)
print("正向测试：结构性叙述数字应被放过（降误报）")
print("=" * 68)
fp = 0
for text, val, why in SHOULD_WHITELIST:
    r = check(text, val)
    ok = (r is not None)
    if not ok:
        fp += 1
    print(f"  {'✅' if ok else '❌'} {text!r}")
    print(f"      期望=放过  实际={r or '未放过(会报)'}   [{why}]")

print()
print("=" * 68)
print(f"反向假阴性 {miss}/{len(MUST_FLAG)} ｜ 正向残留误报 {fp}/{len(SHOULD_WHITELIST)}")
print("=" * 68)
sys.exit(1 if (miss or fp) else 0)
