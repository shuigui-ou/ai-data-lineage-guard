#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
emit.py — 产出通道封装（防 AI 偷懒的强制收尾）

设计哲学（对齐「独立 oracle、不依赖被验证方自证」）：
    AI 不是"自觉去跑校验脚本"，而是"不通过校验就产不出交付文件"。
    emit.py 是**唯一被认可的产出通道**：它内部 生成→自校验→FAIL 拒绝落盘。

流程：
    1. 读原始数据（真值）+ AI 产出（claims，带 provenance）。
    2. 复用 verify_lineage 的对账逻辑逐条核对。
    3. 若 FAIL（有错位/无法验证）：**绝不写交付文件**，改写 .blocked.md 隔离报告，退出码 1。
    4. 若 PASS：把交付文件写到 --out，并盖上校验戳（状态/时间/源）。

边界（诚实声明）：
    - emit.py 是协议层强制：它让"正确路径"成为唯一能产出有效交付物的路径。
    - 但纯靠协议，AI 理论上仍可用别的手段直接写文件；**真正的硬闸门需要框架级
      pre-delivery hook 调本文件**（本文件即是 hook 会调用的 Enforcement 代码，无需返工）。
    - 无论走 hook 还是纯协议，未通过 emit.py 的文件按协议一律视为"非交付物"。
"""
import sys
import os
import json
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_lineage as V


def build_deliverable(claims, results, source, out):
    n_ok = sum(1 for r in results if r[0] == "ok")
    n_fail = sum(1 for r in results if r[0] == "fail")
    n_warn = sum(1 for r in results if r[0] == "warn")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    lines.append("# 数据统计分析结果（已通过数值血缘校验）\n")
    lines.append(f"> 校验状态：✅ PASS ｜ 校验时间：{stamp} ｜ 校验器：emit.py")
    lines.append(f"> 原始数据：`{source}` ｜ 声明数：{len(results)}（命中 {n_ok} / 错位 0 / 无法验证 0）\n")
    lines.append("| 指标 | 报告值 | 来源/派生 | 核对 |")
    lines.append("|---|---|---|---|")
    for (status, label, detail), claim in zip(results, claims):
        ref = claim.get("source") or claim.get("derived") or {}
        if claim.get("source"):
            ref_s = f"行{ref.get('row')} 列{ref.get('col')}"
        elif claim.get("derived"):
            col = ref.get("col")
            ref_s = f"{ref.get('op')}({col})" if col else f"{ref.get('op')}(全行数)"
            rs = ref.get("rows")
            if rs:
                ref_s += f" @行{rs}"
        else:
            ref_s = "—"
        lines.append(f"| {label} | {claim.get('value')} | {ref_s} | {V.ICON[status]} |")
    lines.append("")
    lines.append("---")
    lines.append("数值血缘戳：本文件由 emit.py 在通过 `verify_lineage` 对账后落盘，")
    lines.append("任一数值均可回溯至原始数据。未经 emit.py 的文件不属于交付物。")
    return "\n".join(lines)


def build_blocked(results, source, out):
    n_ok = sum(1 for r in results if r[0] == "ok")
    n_fail = sum(1 for r in results if r[0] == "fail")
    n_warn = sum(1 for r in results if r[0] == "warn")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    lines.append("# ❌ 交付被拦截：数值血缘校验未通过\n")
    lines.append(f"> 拦截时间：{stamp} ｜ 校验器：emit.py")
    lines.append(f"> 原始数据：`{source}` ｜ 待核声明：{len(results)}")
    lines.append(f"> 命中 {n_ok} ｜ **错位 {n_fail}** ｜ **无法验证 {n_warn}** ｜ 结论：FAIL\n")
    lines.append("## 未通过项（必须修正后重跑 emit.py）\n")
    for status, label, detail in results:
        if status != "ok":
            lines.append(f"- {V.ICON[status]} **{label}**：{detail}")
    lines.append("")
    lines.append(f"**交付文件 `{out}` 未写入。** 跳过校验的产出不允许落盘。")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="产出通道：自校验通过才落盘")
    ap.add_argument("--source", required=True, help="原始数据文件 (.csv/.json)")
    ap.add_argument("--claims", required=True, help="AI 产出 (json 清单 或 md/txt 行内标签)")
    ap.add_argument("--out", required=True, help="最终交付文件路径（仅 PASS 才写）")
    args = ap.parse_args()

    try:
        header, rows = V.load_source(args.source)
    except ImportError as e:
        print(f"❌ 依赖缺失: {e}")
        sys.exit(3)
    except FileNotFoundError:
        print(f"❌ 原始数据文件不存在: {args.source}")
        sys.exit(3)
    except (ValueError, KeyError, IndexError, OSError) as e:
        print(f"❌ 原始数据无法加载: {e}")
        sys.exit(3)

    claims = V.load_claims(args.claims)
    if not claims:
        print("⚠️ 未解析到任何带标签的数值声明，拦截。")
        sys.exit(2)

    results = [V.check_claim(c, header, rows) for c in claims]
    n_fail = sum(1 for r in results if r[0] == "fail")
    n_warn = sum(1 for r in results if r[0] == "warn")
    verdict = "PASS" if (n_fail == 0 and n_warn == 0) else "FAIL"

    blocked_path = args.out + ".blocked.md"
    if verdict != "PASS":
        blocked = build_blocked(results, V.LAST_SOURCE_DESC, args.out)
        with open(blocked_path, "w", encoding="utf-8") as f:
            f.write(blocked)
        print(f"❌ 闸门拦截：{n_fail} 错位 + {n_warn} 无法验证。")
        print(f"   交付文件 {args.out} 未写入。")
        print(f"   隔离报告已写：{blocked_path}（修正 claims 后重跑 emit.py）")
        sys.exit(1)

    deliverable = build_deliverable(claims, results, V.LAST_SOURCE_DESC, args.out)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(deliverable)
    print(f"✅ 校验通过（{len(results)} 条声明全命中），交付文件已落盘：{args.out}")
    sys.exit(0)


if __name__ == "__main__":
    main()
