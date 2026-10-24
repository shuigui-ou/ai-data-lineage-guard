#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_conclusions.py — 第 4 层：结论链检查（结论 ↔ 证据链）

定位：前三层只看「数字」；本层是唯一看「结论与数字的绑定关系」的层。
不重算结论真伪（那是制造第二份真值），只查证据链是否成立。

分级（按实现前修正）：
  硬拦（❌，纯逻辑/纯事实，无歧义）：
    H1 引用完整性：悬空引用 / 被引证据未 PASS / claim id 重复
    H2 数值矛盾：结论里的数字与被引证据矛盾
    H3 阈值矛盾：自称满足 criterion 但实际不满足
    H4 方向矛盾：结论声称方向与证据显式声明方向矛盾
  提示（⚠️，第一版不硬拦，因误报率高）：
    W1 裸数字：结论里的数字在被引证据中找不到
        → 只在 basis 内较严；statement 内放过。实测误报率 <10% 后才考虑升级。

  不查：
    · 因果/建议/预测类结论的真伪（无 ground truth）
    · 结论的措辞、逻辑跳跃（交第 3 层语义审计）

已知边界（诚实声明，见 docs/CONCLUSION_CHECK_DESIGN.md §8）：
  · 自声明阈值可被放宽以规避（criterion 由结论自己声明，机器只查自洽不查合理性）
  · 证据选择偏差查不出来（只引支持自己的证据）→ 靠人工抽查
  · 本层不能验因果方向

用法：
    python verify_conclusions.py --source <源> --claims <claims(含conclusions)>
退出码：0=通过 1=有硬拦失败 2=未启用结论机制
"""
import sys
import os
import re
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_lineage as V

ICON = {"ok": "✅", "fail": "❌", "warn": "⚠️"}

# 方向词 → 期望符号（+1 正/上升/正相关, -1 负/下降/负相关）
UP_WORDS = ["上升", "上涨", "走强", "增加", "提高", "正相关", "增长", "上行"]
DOWN_WORDS = ["下降", "下跌", "走低", "减弱", "减少", "负相关", "下行"]

# 方向词若修饰的是"分组名/样本名"而非"效应/变化量"，不算方向声明。
# 反例（实测踩坑）："该关系下行样本量为 800" —— "下行"描述的是样本分组(n_down)，
# 不是结论的方向主张；误判会把"数值矛盾"报成"方向矛盾"，判定理由完全错了。
GROUP_CONTEXT = ["样本", "样本量", "分组", "组内", "区间", "情形", "情况下", "上行样本", "下行样本"]
# 明确的效应/变化量修饰 → 方向词有效
EFFECT_CONTEXT = ["效应", "影响", "变化", "变动", "偏移", "弹性", "反应", "收益", "响应"]


def detect_direction(text):
    """返回 +1 / -1 / None。None = 没有明确的方向声明。

    规则（修正后的实证口径）：
      · 方向词必须出现在「效应/变化量」语境内，或独立作谓语 → 才算方向声明
      · 方向词若紧邻「样本/分组」等分组语境 → 不算（那是分组名，不是方向）
    """
    if not isinstance(text, str):
        return None
    up = [w for w in UP_WORDS if w in text]
    down = [w for w in DOWN_WORDS if w in text]
    if up and not down:
        cand, sign = up[0], 1
    elif down and not up:
        cand, sign = down[0], -1
    else:
        return None

    # 取方向词附近的语境词（前后各 6 字）
    idx = text.find(cand)
    around = text[max(0, idx - 6): idx + len(cand) + 6]
    # 若附近出现分组语境且没有效应语境 → 判为分组名，不算方向声明
    has_group = any(g in around for g in GROUP_CONTEXT)
    has_effect = any(e in around for e in EFFECT_CONTEXT)
    if has_group and not has_effect:
        return None
    return sign


def extract_numbers(text):
    """从文本抽数值 token。返回 [(原串, 数值, 起点, 终点)]。
    支持：5613 / 5,613 / 18.05% / 2.9e-11 / -0.198% / 5613 元
    位置信息用于词法白名单的上下文判定（年份/序数/窗口计数需要看左右文）。
    """
    if not isinstance(text, str):
        return []
    out = []
    # 负数或正数 + 可选千分位 + 小数 + 可选科学计数 + 可选百分号
    pat = re.compile(r"-?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?%?")
    for m in pat.finditer(text):
        raw = m.group(0)
        v = V.coerce_num(raw)
        if v is None:
            continue
        out.append((raw, v, m.start(), m.end()))
    return out


# ───────────────── 词法白名单（v0.5.0）─────────────────
# 目的：把"结构性叙述数字"从 W1 裸数字检查里摘掉，降低误报。
# 原则：**只放过不可能是"被研究量的测量值"的数字**。
#   放过：年份、序数/排名、季度标签、窗口/区间计数、带约数词的倍数。
#   不放过：样本量、金额、百分比、效应量、无修饰语的倍数、任何测量值。
# 为什么这样切：叙述性数字（"过去 3 年""第 2 季度""8 个年度窗口"）
#   本来就不该在证据表里找得到，它们的"出处"是语言不是数据；
#   而测量值找不到出处才是真信号。全量放过 = 把闸门拆了（假阴性）。
#
# 诚实边界（写进报告）：白名单是**词法**的，不是语义完美的。
#   "第 800 名"这类数字会被放过——它与证据里的 798 数值接近，
#   但语义上是排名不是测量值，放过比误报硬拦更正确（H2 理由必须准确）。

_WHITELIST_YEAR_NEXT = ("年", "至", "到")
_WHITELIST_RANGE_NEXT = ("至", "到", "年", "季度")
_WHITELIST_ORDINAL_PREV = ("第", "前", "首", "末", "名列", "top")
_WHITELIST_TIME_UNIT = ("年", "个月", "月", "周", "日", "天", "小时", "分钟", "季度")
_WHITELIST_TIME_MARK = ("过去", "最近", "近", "连续", "累计", "长达", "整整",
                        "全年", "前年", "去年", "未来", "下个", "上个")
# 只认【结构词】：尾部含它们才是"结构性窗口/区间计数"；
# "个交易日"本身不算（"涉及 5 个交易日"是实体计数，必须仍报）。
_WHITELIST_WINDOW_KEY = ("窗口", "区间", "周期", "批次", "时段")
_WHITELIST_STRIP = ("个", "年度", "时间", "连续", "共")
_WHITELIST_MULT_SUFFIX = ("倍",)
_WHITELIST_MULT_HEDGE = ("约", "大约", "接近", "差不多", "超过", "超", "逾", "近")


def _near_nonspace(text, idx, step):
    """从 idx 沿 step 方向找第一个非空白字符；找不到返回 ""。"""
    i = idx
    while 0 <= i < len(text):
        if not text[i].isspace():
            return text[i]
        i += step
    return ""


def classify_descriptive_number(text, raw, val, start, end):
    """判定该数字是否属于"结构性叙述数字"（应从 W1/H2 中排除）。

    返回放行理由字符串；不属于叙述性数字则返回 None。
    判定只看词法上下文（左右邻字），不引入语义推断。
    """
    if not isinstance(text, str):
        return None

    nxt = _near_nonspace(text, end, +1)
    prv = _near_nonspace(text, start - 1, -1)
    after = text[end:end + 10]
    before = text[max(0, start - 10):start]

    # R1 季度标签：Q2 / q3 —— 前一字符是 Q/q
    if prv in ("Q", "q") and len(raw) == 1 and raw.isdigit() and raw in "1234":
        return "季度标签"

    # R2 年份
    if float(val).is_integer() and 1000 <= val <= 2999:
        if nxt == "年":
            return "年份"
        if nxt in ("至", "到") and 1900 <= val <= 2099:
            return "年份区间"
        if prv in ("至", "到", "~", "～", "-", "—") and nxt in _WHITELIST_RANGE_NEXT \
                and 1900 <= val <= 2099:
            return "年份区间"

    # R3 序数/排名：第 2 季度 / 前 5 名
    if prv in _WHITELIST_ORDINAL_PREV and float(val).is_integer() and 1 <= val <= 999:
        return "序数/排名"

    # R4 时段计数：过去 3 年 / 最近 12 个月
    after_l = after.lstrip()
    if any(after_l.startswith(u) for u in _WHITELIST_TIME_UNIT) \
            and any(m in before for m in _WHITELIST_TIME_MARK):
        return "时段计数"

    # R5 窗口/区间计数：共覆盖 2 个交易日区间 / 8 个年度窗口
    tail = after_l
    changed = True
    while changed:
        changed = False
        for s in _WHITELIST_STRIP:
            if tail.startswith(s) and len(tail) > len(s):
                tail = tail[len(s):]
                changed = True
                break
    if any(k in tail for k in _WHITELIST_WINDOW_KEY):
        return "窗口/区间计数"

    # R6 带约数词的倍数：增长约 3 倍（无约数词不放行——那是量级主张）
    if nxt in _WHITELIST_MULT_SUFFIX and any(h in before for h in _WHITELIST_MULT_HEDGE):
        return "倍数(带约数词)"

    return None


def _fmt(x):
    return ("%g" % x)


def collect_evidence_values(claim, header, rows):
    """收集被引证据 claim 的「可核对数值集合」。
    包含：claim 自身的 value、派生重算值、以及其 source 单元格值。
    """
    # 注意：不能用 round(v, 6) —— 会把 p=2.9e-11 抹成 0.0，导致小数值核对失效
    # （实测踩坑）。这里保留原值，用相对容差比较。
    vals = set()
    src = claim.get("source")
    der = claim.get("derived")
    v = V.coerce_num(claim.get("value"))
    if v is not None:
        vals.add(float(v))
    if src:
        try:
            cell = V.cell_value(header, rows, int(src.get("row")), src.get("col"))
            cv = V.coerce_num(cell)
            if cv is not None:
                vals.add(float(cv))
        except Exception:
            pass
    if der:
        try:
            r = V.recompute(header, rows, der.get("op"), der.get("col"), der.get("rows"))
            vals.add(float(r))
        except Exception:
            pass
    return vals


def _criterion_op(op, actual, target):
    if op in (">=", "ge"):
        return actual >= target
    if op in (">", "gt"):
        return actual > target
    if op in ("<=", "le"):
        return actual <= target
    if op in ("<", "lt"):
        return actual < target
    if op in ("==", "eq", "="):
        return abs(actual - target) < 1e-9
    return None


def check_conclusion(conc, claims_by_id, claim_results, header, rows):
    """返回 (status, detail) 列表，每项 (level, code, msg)。"""
    issues = []
    cid = conc.get("id", "(无id)")
    ctype = conc.get("type", "")
    statement = conc.get("statement", "")
    verdict = conc.get("verdict", "")
    evidence = conc.get("evidence") or []
    basis = conc.get("basis") or {}

    # ── H1 引用完整性 ─────────────────────────────
    if not evidence:
        issues.append(("fail", "H1", "结论无 evidence 引用（结论必须绑定已核验的数字）"))
    for ev in evidence:
        if ev not in claims_by_id:
            issues.append(("fail", "H1", f"悬空引用：evidence '{ev}' 在 claims 中不存在"))
            continue
        st = claim_results.get(ev)
        if st is None:
            issues.append(("fail", "H1", f"引用了无核验结果的证据 '{ev}'"))
        elif st != "ok":
            issues.append(("fail", "H1",
                           f"引用了未通过机械对账的证据 '{ev}'（该 claim 状态 {st}）"))

    # ── criterion 必带 source（修正 2 的留痕要求）────────────
    crit = conc.get("criterion") or conc.get("threshold")
    if crit:
        if not crit.get("source"):
            issues.append(("fail", "H1",
                           "criterion 缺 source 字段（判定依据必须留痕可审，"
                           "否则宽阈值可被无声放宽）"))

    # ── H3 阈值一致性 ─────────────────────────────
    if crit:
        metric = crit.get("metric")
        op = crit.get("op")
        target = crit.get("value")
        # metric 指向某个 evidence claim 的 label
        metric_val = None
        for ev in evidence:
            c = claims_by_id.get(ev)
            if c and (c.get("label") == metric or c.get("id") == metric):
                mv = V.coerce_num(c.get("value"))
                if mv is not None:
                    metric_val = mv
                    break
        if metric_val is None:
            issues.append(("warn", "H3",
                           f"criterion 的 metric='{metric}' 未匹配到任何 evidence claim 的值"
                           f"（无法核验阈值，仅提示）"))
        elif op and target is not None:
            res = _criterion_op(op, metric_val, V.coerce_num(target))
            if res is False:
                issues.append(("fail", "H3",
                               f"阈值矛盾：criterion 要求 {metric} {op} {target}，"
                               f"但实际 {metric}={_fmt(metric_val)}（自称『{verdict}』却不满足自声明标准）"))

    # ── H4 方向一致性（只查显式矛盾）────────────────
    text_all = "%s %s" % (statement, verdict)
    want = detect_direction(text_all)
    if want is not None:
        # 证据里是否有显式方向声明（claim 带 direction 字段，或 effect 类派生值）
        ev_dir = None
        for ev in evidence:
            c = claims_by_id.get(ev)
            if not c:
                continue
            d = c.get("direction")
            if d in ("up", "down", "+", "-"):
                ev_dir = 1 if d in ("up", "+") else -1
                break
            dv = V.coerce_num(c.get("value"))
            if dv is not None and dv != 0:
                ev_dir = 1 if dv > 0 else -1
                break
        if ev_dir is not None and ev_dir != want:
            issues.append(("fail", "H4",
                           f"方向矛盾：结论称『{verdict or statement[:20]}』方向为"
                           f"{'上升/正' if want > 0 else '下降/负'}，但被引证据的符号为"
                           f"{'正' if ev_dir > 0 else '负'}"))

    # ── H2 数值矛盾 + W1 裸数字（提示级）─────────────
    ev_vals = set()
    for ev in evidence:
        c = claims_by_id.get(ev)
        if c:
            ev_vals |= collect_evidence_values(c, header, rows)

    def scan_numbers(field_name, text, strict):
        for raw, val, ns, ne in extract_numbers(text):
            # -- 词法白名单：结构性叙述数字不参与 H2/W1（v0.5.0）--
            # 必须在比对之前判：否则“第 800 名”这类数字会因与证据 798 接近而被
            # H2 误判成“数值矛盾”——理由完全错（那不是抄错，是排名）。
            wl = classify_descriptive_number(text, raw, val, ns, ne)
            if wl is not None:
                continue
            matched = False
            near_conflict = None   # 找到"数值接近但不相等"的候选 → 明确数值矛盾
            for ev in ev_vals:
                if abs(val - ev) <= max(1e-6, abs(ev) * 1e-6):
                    matched = True
                    break
                if abs(ev) > 0 and abs(val / ev - round(val / ev)) < 1e-9 and abs(val) > abs(ev):
                    matched = True   # 结论给的是证据的整数倍
                    break
                if abs(ev) > 0 and abs(val * 100 - ev) <= max(1e-6, abs(ev) * 1e-6):
                    matched = True   # 百分数形态
                    break
                if abs(ev) > 0 and abs(val - ev * 100) <= max(1e-6, abs(ev) * 1e-6):
                    matched = True
                    break
                # 数值接近但不相等 → 疑似抄错（结论 800 vs 证据 798）
                rel = abs(val - ev) / max(abs(ev), 1e-9)
                if rel < 0.05 and near_conflict is None:
                    near_conflict = ev
            if matched:
                continue
            # ── H2 数值矛盾（硬）：证据里存在"数值接近但不相等"的候选 ──
            if near_conflict is not None:
                issues.append(("fail", "H2",
                               f"数值矛盾：{field_name} 中的数字 {raw} 与被引证据的值 "
                               f"{_fmt(near_conflict)} 接近但不相等（疑似抄错/记错）"))
                continue
            # ── W1 裸数字（提示级，修正 1）──
            issues.append(("warn", "W1",
                           f"{field_name} 中的数字 {raw} 在被引证据中找不到对应值"
                           f"（疑似无出处；第一版为提示级）"))

    scan_numbers("statement", statement, strict=False)
    scan_numbers("basis", json.dumps(basis, ensure_ascii=False) if basis else "", strict=True)

    # ── 因果/建议/预测类：必须显式标 manual ─────────
    if ctype in ("causal", "recommend", "forecast"):
        if conc.get("verifiability") != "manual":
            issues.append(("fail", "H1",
                           f"type='{ctype}' 必须显式标注 verifiability='manual'"
                           f"（不可核验的结论不得伪装成可核验的）"))

    return issues


def main():
    ap = argparse.ArgumentParser(description="结论链检查（第 4 层）")
    ap.add_argument("--source", required=True, help="原始数据文件")
    ap.add_argument("--claims", required=True, help="claims 文件（含 conclusions）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
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
        print("⚠️ 未解析到任何带标签的数值声明，请检查产出格式。")
        sys.exit(2)

    # 读 conclusions（JSON 顶层）
    try:
        with open(args.claims, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = {}
    if isinstance(raw, list):
        raw = {"claims": raw}
    conclusions = raw.get("conclusions") or []
    if not conclusions:
        print("ℹ️ 本文件未包含 conclusions 数组 → 结论层未启用（claims 逐条对账请用 verify_lineage.py）")
        sys.exit(2)

    # claim id 唯一性 + 建索引
    ids_ok, dups = V.check_claim_ids(claims)
    if not ids_ok:
        print("=" * 68)
        print("结论链检查报告（第 4 层）")
        print("=" * 68)
        print(f"❌ [H1] claim id 重复: {sorted(set(dups))}"
              f"（id 必须唯一，否则结论引用有歧义、可被挑着用以绕过检查）")
        print("-" * 68)
        print("硬拦失败 1 ｜ 提示 0")
        print("结论: FAIL")
        print("=" * 68)
        sys.exit(1)

    claims_by_id = {}
    for c in claims:
        cid = c.get("id")
        if cid is not None:
            claims_by_id[str(cid)] = c

    # 先跑第 1 层，拿每条 claim 的核验状态（结论层依赖它）
    claim_results = {}
    for c in claims:
        st, _, _ = V.check_claim(c, header, rows)
        claim_results[c.get("id")] = st

    # 逐条查结论
    all_issues = []
    for conc in conclusions:
        cid = conc.get("id", "(无id)")
        issues = check_conclusion(conc, claims_by_id, claim_results, header, rows)
        all_issues.append((cid, conc.get("type", ""), issues))

    n_fail = sum(1 for _, _, iss in all_issues for lvl, _, _ in iss if lvl == "fail")
    n_warn = sum(1 for _, _, iss in all_issues for lvl, _, _ in iss if lvl == "warn")

    if args.json:
        print(json.dumps({
            "conclusions": [{"id": c, "type": t,
                             "issues": [{"level": l, "code": cd, "msg": m}
                                        for l, cd, m in iss]}
                            for c, t, iss in all_issues],
            "fail": n_fail, "warn": n_warn,
        }, ensure_ascii=False, indent=2))
        sys.exit(1 if n_fail else 0)

    print("=" * 68)
    print("结论链检查报告（第 4 层）")
    print(f"原始数据: {V.LAST_SOURCE_DESC}  ({len(rows)} 数据行)")
    print(f"结论数: {len(conclusions)} ｜ 证据 claim 数: {len(claims)}")
    print("=" * 68)
    for cid, ctype, iss in all_issues:
        print(f"\n[{cid}] ({ctype})")
        if not iss:
            print(f"  {ICON['ok']} 证据链成立，无异常")
        for lvl, code, msg in iss:
            print(f"  {ICON[lvl]} [{code}] {msg}")
    print("-" * 68)
    print(f"硬拦失败 {n_fail} ｜ 提示 {n_warn}")
    print("结论: " + ("PASS" if n_fail == 0 else "FAIL"))
    print("=" * 68)
    print("诚实边界：自声明阈值可被放宽以规避；证据选择偏差查不出来；本层不验因果真伪。")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
