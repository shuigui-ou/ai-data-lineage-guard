#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_semantic.py — 语义审计（第二道闸门）：提示级，不硬拦

与机械对账的分级关系（重要）：
    机械对账 verify_lineage.py  = 硬闸门。FAIL 不落盘。
    语义审计 audit_semantic.py  = 提示级。只产线索，走人工复核。
    理由：LLM 审计会误报，拿概率性判断当硬闸门会制造假拦截，
         假拦截会逼人绕过闸门 → 整道防线失效。

它抓什么（机械对账锚"值↔单元格"，它锚"单元格↔语义实体"）：
    1. 单位混淆：中/万元、百分数 vs 小数、千分位误读
    2. 时间窗口错位：同一实体不同期间取错行
    3. 指代错位："该产品"指到了别的行
    4. 标签文字描述 与 指向行的实体标识列 不一致（值碰巧对得上也要报）

v0.5.0 新增：它还审**结论措辞与逻辑跳跃**（锚"结论措辞↔证据强度"）：
    5. wording：措辞强度与证据不匹配（"显著/大幅/决定性"vs 中等效应量）
    6. leap：逻辑跳跃（相关→因果、样本内→未来、历史同期→普遍规律）

四条硬边界（代码层强制，不是只写在提示里）：
    边界1 只出线索、不下结论、不重算数值
          → finalize 阶段 lint：发现线索文本里含"应为/重算/正确值"等重算口吻，
            标记为 `越界` 并**不计入有效线索**。
    边界2 fresh context 独立实例
          → 本脚本不自己审；它只负责"备料"（把身份列摆到审计者面前），
            审计由独立实例执行（prompt 见 SEMANTIC_AUDIT_PROMPT.md）。
          备料本身是确定性的，因此不存在"审计者找不到实体列就漏掉"的人为疏忽。
    边界3 给固定检查清单，不是笼统问"你对吗"
          → CHECKLIST / CONCLUSION_CHECKLIST 常量，逐项过，prepare 写进审计包。
    边界4 与机械对账分级，不合并
          → 本脚本退出码恒为 0（提示级）；它没有能力、也没有权力拦下交付。

用法（两步）：
    # 步骤1 备料（确定性）
    python audit_semantic.py prepare --source <源> --claims <claims> --out trial/audit_packet.json
    # 步骤2 把 trial/audit_packet.json + SEMANTIC_AUDIT_PROMPT.md 交给独立实例，产出 findings.json
    # 步骤3 汇总成线索报告（不硬拦）
    python audit_semantic.py finalize --packet trial/audit_packet.json --findings <findings.json> \
        --out trial/semantic_audit.md
"""
import sys
import os
import re
import json
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_lineage as V

# ── 边界3：固定检查清单，逐项过 ────────────────────────────────
# 清单项 1-4 审「声明级」（每条 claim）
CHECKLIST = [
    ("unit", "单位混淆：中/万元、百分数 vs 小数、千分位误读"),
    ("window", "时间窗口错位：同一实体不同期间取错行"),
    ("ref", "指代错位：'该产品/该公司'指到了别的行"),
    ("identity", "标签文字描述 与 指向行的实体标识列 不一致（值碰巧对得上也要报）"),
]

# 清单项 5-6 审「结论级」（每条 conclusion）——v0.5.0 新增
CONCLUSION_CHECKLIST = [
    ("wording", "措辞强度与证据不匹配：结论说'显著/大幅/决定性'，但被引证据的效应量或幅度支撑不住"),
    ("leap", "逻辑跳跃：相关→因果、样本内→样本外、历史同期→未来、个案→普遍规律"),
]

ALL_ITEMS = {k for k, _ in CHECKLIST} | {k for k, _ in CONCLUSION_CHECKLIST}

# ── 边界1：重算口吻黑名单——finalize 时 lint，命中即判越界 ──────
RECOMPUTE_PATTERNS = [
    r"应为", r"应该为", r"正确值", r"重算", r"重新计算", r"我算出", r"实际应为",
    r"应为\s*\d", r"正确答案", r"修正为\s*[\d\.\-]",
]

# ── 结论措辞的确定性提示词提取（只标"出现了什么词"，不判"对不对"）──
# 原则：备料阶段只做确定性事实摆位（哪些强措辞词出现了、哪些跳跃词出现了），
#       "是否匹配"由 fresh-context 审计者判断——脚本不下判断结论。
# 否定守卫：命中词前 2 字内含 不/未/非/无 → 不计入（"不显著"不是强措辞）
_NEG_GUARD = ("不", "未", "非", "无", "毫不")

STRONG_WORDING = [
    "显著提升", "显著改善", "显著", "大幅", "强烈", "决定性", "必然", "远超",
    "极大", "爆炸式", "彻底", "惊人", "必然性", "极强", "完美", "完全一致",
    "高度稳定", "巨大", "空前", "彻底性",
]

LEAP_CAUSAL = ["导致", "引起", "造成", "驱动", "使得", "源于", "决定了", "带来"]
LEAP_FUTURE = ["未来", "预计", "预期", "展望", "下一轮", "将", "会继续", "势必"]
LEAP_UNIVERSAL = ["所有", "全部", "任何", "每个", "普遍", "一律", "总是", "必然",
                   "无一例外", "适用于所有", "永远"]
LEAP_SAMPLE = ["回测", "历史数据", "样本中", "样本内", "样本显示", "历史上"]

# 证据列名分类（帮审计者快速定位"效应量/显著性/样本量"，不必逐列猜）
# 分类依据优先级：claim.source.col（真实列名） > claim.label
#   实测踩坑：只按 label 判会漏——中文 label「R01 下行中位效应」不含英文 effect，
#   而 source.col 是 effect_down_rel_median → 被误归到 other，效应量对不上号。
EFFECT_COL_HINTS = ["cohen_d", "effect", "corr", "r2", "beta", "slope", "diff",
                    "lift", "gain", "ratio", "delta", "change",
                    "效应", "弹性", "系数", "变化量"]
PVAL_COL_HINTS = ["p_raw", "p_value", "pval", "p_adj", "q_bh", "p_fdr", "pval_",
                   "显著性", "p值"]
SAMPLE_COL_HINTS = ["n_up", "n_down", "n_", "count", "sample", "样本"]


def _has_neg_guard(text, idx):
    pre = text[max(0, idx - 2): idx]
    return any(g in pre for g in _NEG_GUARD)


def extract_markers(text, wordlist):
    """返回文本中命中的词（去重、保序）。带否定守卫。"""
    if not isinstance(text, str):
        return []
    hits = []
    for w in wordlist:
        start = 0
        while True:
            i = text.find(w, start)
            if i < 0:
                break
            if not _has_neg_guard(text, i):
                hits.append((i, w))
            start = i + len(w)
    hits.sort()
    seen = set()
    out = []
    for _, w in hits:
        if w not in seen:      # 去重：同一词在 statement 与 verdict 各出现一次只报一次
            seen.add(w)
            out.append(w)
    return out


def classify_evidence_columns(claims_by_id, evidence_ids):
    """把被引证据里"看起来像效应量/显著性/样本量"的列名与值挑出来，供审计者比对措辞强度。

    只做列名匹配与原样取值，不做任何统计推断或重算。
    """
    out = {"effect_like": [], "pvalue_like": [], "sample_like": [], "other": []}
    for eid in evidence_ids:
        c = claims_by_id.get(eid)
        if not c:
            continue
        label = str(c.get("label", ""))
        val = c.get("value")
        # 真实列名优先（source.col），label 作补充
        src = c.get("source") or {}
        col = str(src.get("col", ""))
        low = (col + " " + label).lower()
        bucket = None
        if any(h.lower() in low for h in PVAL_COL_HINTS):
            bucket = "pvalue_like"
        elif any(h.lower() in low for h in EFFECT_COL_HINTS):
            bucket = "effect_like"
        elif any(h.lower() in low for h in SAMPLE_COL_HINTS):
            bucket = "sample_like"
        if bucket:
            out[bucket].append({"claim_id": eid, "label": label, "value": val,
                                 "col": col})
        else:
            out["other"].append({"claim_id": eid, "label": label, "value": val,
                                  "col": col})
    return out


def _identity_cols(header):
    """判定哪些列是"实体标识列"（文本类），供审计者核对语义身份。"""
    out = []
    for h in header:
        if h in ("edge_id",):
            out.append(h); continue
        out.append(h)
    return out


def _row_identity(rows, header, row):
    """取第 row 行（1-based 数据行）的全部列原文；越界返回 None。"""
    try:
        r = int(row)
    except (ValueError, TypeError):
        return None
    if r < 1 or r > len(rows):
        return None
    return {h: rows[r - 1].get(h) for h in header}


def cmd_prepare(args):
    header, rows = V.load_source(args.source)
    claims = V.load_claims(args.claims)
    if not claims:
        print("⚠️ 未解析到任何带标签的数值声明。")
        return 1

    # 读原始 JSON 以取 conclusions（向后兼容：没有就不产出结论段）
    try:
        with open(args.claims, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = {}
    if isinstance(raw, list):
        raw = {"claims": raw}
    conclusions = raw.get("conclusions") or []

    claims_by_id = {}
    for c in claims:
        cid = c.get("id")
        if cid is not None:
            claims_by_id[str(cid)] = c

    packet = {
        "_doc": "语义审计备料包。本包只做确定性备料，不含任何判断结论。",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": V.LAST_SOURCE_DESC,
        "n_rows": len(rows),
        "header": header,
        "checklist": [{"item": k, "desc": d} for k, d in CHECKLIST],
        "conclusion_checklist": [{"item": k, "desc": d} for k, d in CONCLUSION_CHECKLIST],
        "boundaries": [
            "只出线索，不下结论，不重算数值（finalize 会 lint 重算口吻）",
            "审计必须由 fresh context 独立实例执行，同实例回看自己产出会合理化",
            "逐项过固定清单，不得笼统判断",
            "提示级：产出走人工复核，不单独拦下交付",
            "措辞/跳跃提示词只标'出现了哪些词'，是否匹配由审计者判断，脚本不下判断",
        ],
        "items": [],
        "conclusions": [],
    }

    for c in claims:
        label = c.get("label", "(未命名)")
        src = c.get("source")
        der = c.get("derived")
        entry = {
            "claim_label": label,          # ← 这是"文字描述"，审计第4项要核它
            "claim_id": c.get("id"),
            "claim_value": c.get("value"),
            "claim_note": c.get("note", ""),
            "provenance": src or der,
            "provenance_kind": "source" if src else ("derived" if der else "none"),
        }
        # 关键：把指向行的【实体标识列】原文摆出来，审计者不必自己去猜/去找
        if src:
            ident = _row_identity(rows, header, src.get("row"))
            if ident is not None:
                entry["referenced_row"] = int(src.get("row"))
                entry["referenced_row_identity"] = ident
        elif der:
            entry["referenced_rows_note"] = (
                "派生聚合声明：请从 label 文字判断它指的是哪些行，"
                "如需逐行核对请回原始数据按 label 的筛选条件自行定位（不要重算数值）。"
            )
        packet["items"].append(entry)

    # ── 结论段（v0.5.0）：把结论措辞与其被引证据摆在一起 ──────────
    for conc in conclusions:
        evid = [str(e) for e in (conc.get("evidence") or [])]
        statement = conc.get("statement", "")
        verdict = conc.get("verdict", "")
        combined = "%s %s" % (statement, verdict)
        ev_digest = []
        for eid in evid:
            c = claims_by_id.get(eid)
            if not c:
                continue
            d = {"claim_id": eid, "label": c.get("label"), "value": c.get("value")}
            src = c.get("source")
            if src:
                ident = _row_identity(rows, header, src.get("row"))
                if ident is not None:
                    d["referenced_row_identity"] = ident
            ev_digest.append(d)
        packet["conclusions"].append({
            "conclusion_id": conc.get("id", "(无id)"),
            "type": conc.get("type", ""),
            "statement": statement,
            "verdict": verdict,
            "stance": conc.get("stance", ""),
            "criterion": conc.get("criterion") or conc.get("threshold") or {},
            "basis": conc.get("basis") or {},
            "verifiability": conc.get("verifiability", ""),
            "evidence": ev_digest,
            "evidence_magnitude_hints": classify_evidence_columns(claims_by_id, evid),
            "wording_markers": extract_markers(combined, STRONG_WORDING),
            "leap_markers": {
                "causal": extract_markers(combined, LEAP_CAUSAL),
                "future": extract_markers(combined, LEAP_FUTURE),
                "universal": extract_markers(combined, LEAP_UNIVERSAL),
                "sample_scope": extract_markers(combined, LEAP_SAMPLE),
            },
        })

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(packet, f, ensure_ascii=False, indent=2)
    print(f"✅ 备料完成：{args.out}"
          f"（{len(packet['items'])} 条声明 × {len(CHECKLIST)} 项清单"
          f"，{len(packet['conclusions'])} 条结论 × {len(CONCLUSION_CHECKLIST)} 项结论清单）")
    print("   下一步：把本文件 + SEMANTIC_AUDIT_PROMPT.md 交给 fresh-context 独立实例产出 findings.json")
    return 0


def cmd_finalize(args):
    with open(args.packet, encoding="utf-8") as f:
        packet = json.load(f)
    with open(args.findings, encoding="utf-8") as f:
        findings = json.load(f)
    if isinstance(findings, dict):
        findings = findings.get("findings", [])

    valid, out_of_bounds, off_checklist = [], [], []
    for fd in findings:
        text = " ".join(str(fd.get(k, "")) for k in ("note", "detail", "evidence", "claim_label"))
        hit = [p for p in RECOMPUTE_PATTERNS if re.search(p, text)]
        if hit:
            out_of_bounds.append({**fd, "_lint_hit": hit})
            continue
        item = str(fd.get("item", ""))
        if item not in ALL_ITEMS:
            off_checklist.append({**fd, "_lint_hit": [f"item='{item}' 不在固定清单内"]})
            continue
        valid.append(fd)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    L = []
    L.append("# 语义审计线索报告（提示级 · 不拦交付）\n")
    L.append(f"> 生成时间：{stamp} ｜ 审计器：audit_semantic.py")
    L.append(f"> 原始数据：`{packet.get('source')}`（{packet.get('n_rows')} 行）")
    L.append(f"> 审计声明：{len(packet.get('items', []))} 条 ｜ 结论：{len(packet.get('conclusions', []))} 条")
    L.append(f"> 检查清单：声明级 {len(CHECKLIST)} 项 + 结论级 {len(CONCLUSION_CHECKLIST)} 项")
    L.append(f"> **有效线索 {len(valid)} 条 ｜ 越界线索 {len(out_of_bounds)} 条"
             f" ｜ 清单外 {len(off_checklist)} 条**\n")

    L.append("## 分级说明（务必读）\n")
    L.append("本报告是**提示**，不是硬闸门。有效线索须走**人工复核**再决定是否修正。")
    L.append("它没有、也无权单独拦下一份机械对账已 PASS 的交付——")
    L.append("因为 LLM 审计本身会误报，拿概率性判断当硬闸门会制造假拦截。\n")
    L.append("它锚的是「**单元格 ↔ 语义实体**」与「**结论措辞 ↔ 证据强度**」，与机械对账的「值 ↔ 单元格」互补：")
    L.append("值对得上，不代表指的实体、时间、单位、指代就对；")
    L.append("证据链成立，也不代表结论的措辞强度与逻辑跨度撑得住。\n")

    L.append("## 有效线索（需人工复核）\n")
    if not valid:
        L.append("（无）\n")
    else:
        L.append("| 对象 | 清单项 | 疑似问题 | 依据 |")
        L.append("|---|---|---|---|")
        for fd in valid:
            L.append(f"| {fd.get('claim_label') or fd.get('conclusion_id','')} | {fd.get('item','')} "
                     f"| {fd.get('note','')} | {fd.get('evidence','')} |")
        L.append("")

    L.append("## 越界线索（已剔除：违反「不重算」边界）\n")
    if not out_of_bounds:
        L.append("（无）\n")
    else:
        for fd in out_of_bounds:
            L.append(f"- ❌ 已剔除 [{fd.get('item','')}]：{fd.get('note','')}"
                     f"（命中重算口吻模式 {fd.get('_lint_hit')}）")
        L.append("")

    L.append("## 清单外线索（item 不在固定清单内，未计入有效线索）\n")
    if not off_checklist:
        L.append("（无）\n")
    else:
        for fd in off_checklist:
            L.append(f"- ⚠️ 未计入 [{fd.get('item','')}]：{fd.get('note','')}"
                     f"（{fd.get('_lint_hit')}）")
        L.append("")

    L.append("## 清单覆盖情况（声明级）\n")
    L.append("| 清单项 | 说明 | 本次线索数 |")
    L.append("|---|---|---|")
    for k, d in CHECKLIST:
        n = len([x for x in valid if x.get("item") == k])
        L.append(f"| {k} | {d} | {n} |")
    L.append("")

    L.append("## 清单覆盖情况（结论级）\n")
    L.append("| 清单项 | 说明 | 本次线索数 |")
    L.append("|---|---|---|")
    for k, d in CONCLUSION_CHECKLIST:
        n = len([x for x in valid if x.get("item") == k])
        L.append(f"| {k} | {d} | {n} |")
    L.append("")

    L.append("## 诚实边界\n")
    L.append("- 本审计**不保证召回**：可能漏（没注意到实体列的语义错位），也可能误报。")
    L.append("- 它的价值是把机械抓不到的盲区从「完全没人看」变成「按清单看过一遍并留痕」，**不是把盲区清零**。")
    L.append("- 越界线索被剔除不代表那条线索无价值——它只是越了「不重算」边界，应由人工重看。")
    L.append("- 措辞/跳跃提示词由脚本确定性提取（只标'出现了哪些词'），"
             "**是否匹配由审计者判断**，脚本不对措辞下结论。")
    L.append("- 「措辞强度是否撑得住」需要领域知识（多大的效应量算显著），"
             "本审计只提供证据原值供比对，**不替人定阈值**。")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"✅ 语义审计线索报告已写：{args.out}")
    print(f"   有效线索 {len(valid)} 条（待人工复核）｜ 越界已剔除 {len(out_of_bounds)} 条"
          f"｜ 清单外 {len(off_checklist)} 条")
    print("   提示级：本脚本不拦交付，退出码恒为 0")
    return 0


def main():
    ap = argparse.ArgumentParser(description="语义审计（提示级，不硬拦）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="备料：摆出身份列 + 固定清单")
    p.add_argument("--source", required=True)
    p.add_argument("--claims", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_prepare)

    q = sub.add_parser("finalize", help="汇总线索报告 + lint 越界")
    q.add_argument("--packet", required=True)
    q.add_argument("--findings", required=True)
    q.add_argument("--out", required=True)
    q.set_defaults(func=cmd_finalize)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
