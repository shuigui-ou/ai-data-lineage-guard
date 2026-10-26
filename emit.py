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
import verify_conclusions as VC


def _md_cell(s):
    """表格单元格转义：竖线与换行会毁掉 markdown 表格。"""
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def build_deliverable(claims, results, source, out, conclusions=None, warns=None):
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

    # ── 结论段（v0.5.2 修正 A）──────────────────────────────────────
    # 此前 build_deliverable 只渲染 claims 表，第 4 层校验完的结论一个字不落盘，
    # 导致"简报必须有结论"与"只能由 emit 落盘"直接冲突。现在把结论渲染出来。
    if conclusions:
        lines.append("")
        lines.append(f"## 结论（{len(conclusions)} 条，均已通过第 4 层证据链校验）\n")
        lines.append("| # | 类型 | 结论 | 判定 | 证据 | 可核验性 |")
        lines.append("|---|---|---|---|---|---|")
        for c in conclusions:
            ev = "、".join(str(x) for x in (c.get("evidence") or []))
            verif = "人工判定（manual）" if c.get("verifiability") == "manual" else "可核验"
            lines.append(
                f"| {_md_cell(c.get('id'))} | {_md_cell(c.get('type'))} "
                f"| {_md_cell(c.get('statement'))} | {_md_cell(c.get('verdict'))} "
                f"| {_md_cell(ev)} | {verif} |"
            )

    if warns:
        lines.append("")
        lines.append(f"## 提示（{len(warns)} 条，不阻断落盘，建议人工复核）\n")
        for code, msg in warns:
            lines.append(f"- ⚠️ **[{code}]** {msg}")

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
    any_item = False
    for status, label, detail in results:
        if status != "ok":
            any_item = True
            lines.append(f"- {V.ICON[status]} **{label}**：{detail}")
    if not any_item:
        lines.append("（机械对账层无问题；未通过项来自结论层，见下节）")
    lines.append("")
    lines.append(f"**交付文件 `{out}` 未写入。** 跳过校验的产出不允许落盘。")
    return "\n".join(lines)


def _load_conclusions(claims_path):
    """读取顶层 conclusions 数组（无则返回 []）。"""
    try:
        with open(claims_path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return []
    if isinstance(raw, list):
        return []
    if not isinstance(raw, dict):
        return []
    return raw.get("conclusions") or []


def _has_conclusions(claims_path):
    """判断 claims 文件是否启用了结论层（顶层 conclusions 数组非空）。"""
    return bool(_load_conclusions(claims_path))


def _run_conclusion_layer(claims_path, header, rows, claims, results):
    """跑第 4 层，返回 (硬拦issues, 提示issues)。"""
    try:
        with open(claims_path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return [], []
    if isinstance(raw, list):
        raw = {"claims": raw}
    conclusions = raw.get("conclusions") or []

    ids_ok, dups = V.check_claim_ids(claims)
    if not ids_ok:
        return [("H1", f"claim id 重复: {sorted(set(dups))}（id 必须唯一）")], []

    claims_by_id = {}
    for c in claims:
        cid = c.get("id")
        if cid is not None:
            claims_by_id[str(cid)] = c
    claim_results = {}
    for c, r in zip(claims, results):
        claim_results[c.get("id")] = r[0]

    hard, warn = [], []
    for conc in conclusions:
        issues = VC.check_conclusion(conc, claims_by_id, claim_results, header, rows)
        for lvl, code, msg in issues:
            if lvl == "fail":
                hard.append((code, f"[{conc.get('id','(无id)')}] {msg}"))
            else:
                warn.append((code, f"[{conc.get('id','(无id)')}] {msg}"))
    return hard, warn


def main():
    ap = argparse.ArgumentParser(description="产出通道：自校验通过才落盘")
    ap.add_argument("--source", required=True, help="原始数据文件 (.csv/.json)")
    ap.add_argument("--claims", required=True, help="AI 产出 (json 清单 或 md/txt 行内标签)")
    ap.add_argument("--out", required=True, help="最终交付文件路径（仅 PASS 才写）")
    ap.add_argument("--min-claims", type=int, default=0,
                    help="覆盖率门槛：claims 至少 N 条（0=不检查；v0.5.2 修正 H）")
    ap.add_argument("--min-conclusions", type=int, default=0,
                    help="覆盖率门槛：conclusions 至少 N 条（0=不检查）")
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

    # ── 第 4 层：结论链检查（若文件含 conclusions）────────────────
    # 机械 FAIL 已确定拦截；这里追加结论层的硬拦项。
    # 提示级（W1 裸数字）不拦截，但会写进交付件的「提示」段。
    conc_report = None
    conc_warns = []
    conclusions = _load_conclusions(args.claims)
    if conclusions:
        conc_issues, conc_warns = _run_conclusion_layer(args.claims, header, rows, claims, results)
        if conc_issues:
            verdict = "FAIL"
            n_fail += len(conc_issues)
            conc_report = conc_issues
        if conc_warns:
            n_warn += len(conc_warns)

    # ── 覆盖率门槛（v0.5.2 修正 H）：不达标即拦，避免"10 条 claim 的漂亮报告"过闸 ──
    min_fail = []
    if args.min_claims and len(claims) < args.min_claims:
        min_fail.append(f"claims 数 {len(claims)} < 门槛 {args.min_claims}（覆盖率不足）")
    if args.min_conclusions and len(conclusions) < args.min_conclusions:
        min_fail.append(f"conclusions 数 {len(conclusions)} < 门槛 {args.min_conclusions}（覆盖率不足）")
    if min_fail:
        verdict = "FAIL"

    blocked_path = args.out + ".blocked.md"
    if verdict != "PASS":
        blocked = build_blocked(results, V.LAST_SOURCE_DESC, args.out)
        if conc_report:
            blocked += "\n## 结论层（第 4 层）未通过\n\n"
            for code, msg in conc_report:
                blocked += f"- ❌ **[{code}]** {msg}\n"
        if min_fail:
            blocked += "\n## 覆盖率门槛未达标\n\n"
            for m in min_fail:
                blocked += f"- ❌ {m}\n"
        with open(blocked_path, "w", encoding="utf-8") as f:
            f.write(blocked)
        print(f"❌ 闸门拦截：{n_fail} 错位 + {n_warn} 无法验证。")
        for m in min_fail:
            print(f"   ❌ {m}")
        print(f"   交付文件 {args.out} 未写入。")
        print(f"   隔离报告已写：{blocked_path}（修正 claims 后重跑 emit.py）")
        sys.exit(1)

    deliverable = build_deliverable(claims, results, V.LAST_SOURCE_DESC, args.out,
                                    conclusions=conclusions, warns=conc_warns)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(deliverable)
    n_conc = f"，结论 {len(conclusions)} 条" if conclusions else ""
    print(f"✅ 校验通过（{len(results)} 条声明全命中{n_conc}），交付文件已落盘：{args.out}")
    sys.exit(0)


if __name__ == "__main__":
    main()
