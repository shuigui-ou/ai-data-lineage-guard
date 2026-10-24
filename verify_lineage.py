#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_lineage.py — 数值血缘对账器（防 AI 偷懒核心工具）

读入「原始数据（真值）」与「AI 产出的带血缘标签结果」，机械地逐一核对，
抓出：抄错/算错（值≠源单元格）、派生统计不符、以及来源缺失（懒得查）的红色信号。

零依赖（仅用 Python 标准库），数据源支持 .csv / .json，产出支持 .json 清单 或 .md/.txt 行内标签。
"""
import sys
import json
import csv
import re
import argparse
from pathlib import Path

# 数据源描述（供报告打印，含 xlsx 的 sheet 名等元信息）
LAST_SOURCE_DESC = ""


# ───────────────────────── 数据源加载 ─────────────────────────

def load_source(path):
    """返回 (header: list[str], rows: list[dict])。按扩展名分派；xlsx 走 openpyxl 只读分支。"""
    global LAST_SOURCE_DESC
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return _load_xlsx(p)
    if suffix == ".json":
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            # 容许多包一层：找第一个值是 list 的键
            for v in data.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    data = v
                    break
        header = list(data[0].keys())
        rows = [{(k): ("" if v is None else str(v)) for k, v in row.items()} for row in data]
        LAST_SOURCE_DESC = str(p)
        return header, rows
    # 默认 csv
    with open(p, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        rows = [dict(r) for r in reader]
    LAST_SOURCE_DESC = str(p)
    return header, rows


def _load_xlsx(p):
    """xlsx 源：openpyxl 只读 + data_only（取缓存计算值）。

    五个坑的处理：
    1. read_only=True 省内存；data_only=True 取公式的缓存计算值而非公式串。
    2. 公式无缓存值（文件未经 Excel 保存）→ 显式报错，绝不当空值静默过（防假 PASS）。
    3. 首行当 header，R1=首条数据行，与 csv 分支完全一致；列按表头名引用（同 csv）。
    4. 默认取第一个 sheet，并在报告标注 sheet 名，防"读错表还 PASS"。
    5. 缺 openpyxl 依赖 → 显式 ImportError，不降级、不跳过。
    """
    global LAST_SOURCE_DESC
    try:
        import openpyxl
    except ImportError:
        raise ImportError(
            "XLSX 源需要 openpyxl，请 pip install openpyxl"
            "（缺依赖时显式报错，不降级不跳过）"
        )

    # 第一遍：data_only=True 取缓存计算值
    wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]  # 默认第一个 sheet
    sheet_name = ws.title
    none_cells = set()
    raw = []
    for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        raw.append(row)
        for c_idx, val in enumerate(row):
            if val is None:
                none_cells.add((r_idx, c_idx))
    wb.close()

    # 第二遍：data_only=False 检测"公式单元格无缓存值"
    wb2 = openpyxl.load_workbook(str(p), read_only=True, data_only=False)
    ws2 = wb2[wb2.sheetnames[0]]
    formula_no_cache = 0
    for r_idx, row in enumerate(ws2.iter_rows(values_only=True), start=1):
        for c_idx, val in enumerate(row):
            if isinstance(val, str) and val.startswith("=") and (r_idx, c_idx) in none_cells:
                formula_no_cache += 1
    wb2.close()

    if formula_no_cache > 0:
        raise ValueError(
            f"xlsx 含 {formula_no_cache} 个公式单元格无缓存计算值"
            f"（文件可能未经 Excel 保存，data_only 读到 None）。无法验证："
            f"请用 Excel 打开并保存一次，或将公式转为值后再校验。"
        )

    if not raw:
        raise ValueError("xlsx 空表：无任何行")
    header_row = raw[0]
    header = [(str(h).strip() if h is not None else f"col{i + 1}") for i, h in enumerate(header_row)]
    rows = []
    for data_row in raw[1:]:
        d = {}
        all_none = True
        for i, h in enumerate(header):
            val = data_row[i] if i < len(data_row) else None
            if val is not None:
                all_none = False
            d[h] = "" if val is None else (val if isinstance(val, (int, float, bool)) else str(val))
        if all_none:
            continue
        rows.append(d)
    LAST_SOURCE_DESC = f"{p} (sheet: {sheet_name})"
    return header, rows


# ───────────────────────── 数值与派生工具 ─────────────────────────

def coerce_num(s):
    if isinstance(s, (int, float)):
        return float(s)
    if s is None:
        return None
    s = str(s).replace(",", "").replace("%", "").replace("¥", "").replace("$", "").strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _looks_numeric(s):
    """纯数字文本（含千分位/货币/百分号）走数值比对，其余走文本身份比对。"""
    return coerce_num(s) is not None


def cell_value(header, rows, row, col):
    """row: 1-based 数据行索引（不含表头）。"""
    if row < 1 or row > len(rows):
        raise IndexError(f"行越界: {row} (共 {len(rows)} 数据行)")
    if col not in header:
        raise KeyError(f"列不存在: {col} (可用列: {header})")
    return rows[row - 1][col]


def _resolve_rows(rows_spec, n_rows):
    """把 rows 声明解析成 1-based 行号索引序列。

    支持三种形态：
      - None / 空            → 全部数据行
      - "1-100" / "1 - 100"   → 连续闭区间
      - [1,4,6,11] / "1,4,6,11" → 离散行集（试跑暴露的缺口：目标行常不连续）
    离散行集是显式列举，不做任何"就近取整/补全"推断——标签写什么就核什么。
    """
    if rows_spec in (None, "", []):
        return list(range(1, n_rows + 1))

    # 离散行集：真正的 list/tuple，或全数字的逗号串
    if isinstance(rows_spec, (list, tuple)):
        idxs = []
        for x in rows_spec:
            try:
                idxs.append(int(x))
            except (TypeError, ValueError):
                raise ValueError(f"离散行集含非法行号: {x!r}")
        return idxs

    s = str(rows_spec).strip()
    if "," in s:
        idxs = []
        for part in s.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                idxs.append(int(part))
            except ValueError:
                raise ValueError(f"离散行集含非法行号: {part!r}")
        if not idxs:
            raise ValueError(f"离散行集为空: {rows_spec!r}")
        return idxs

    m = re.match(r"^(\d+)\s*-\s*(\d+)$", s)
    if not m:
        raise ValueError(f"无法解析 rows 声明: {rows_spec!r}（支持 '1-100' / [1,4,6] / '1,4,6'）")
    a, b = int(m.group(1)), int(m.group(2))
    if a > b:
        raise ValueError(f"rows 区间首尾颠倒: {a}-{b}")
    return list(range(a, b + 1))


def recompute(header, rows, op, col, rows_spec=None):
    """对列做聚合。

    算子：
      sum / mean / count / min / max —— 只统计**可转数值**的值（count 数的是可转数值的个数）
      countrows                        —— 数**行数**（与列类型无关，字符串列也适用）
    rows：None / "1-100" / [1,4,6] / "1,4,6"（见 _resolve_rows）
    """
    if op == "countrows":
        # 行数算子：不要求 col 存在，但若给了 col 且不存在则显式报错（防笔误被静默放过）
        if col and col not in header:
            raise KeyError(f"列不存在: {col}")
        idxs = _resolve_rows(rows_spec, len(rows))
        for i in idxs:
            if i < 1 or i > len(rows):
                raise IndexError(f"行越界: {i} (共 {len(rows)} 数据行)")
        return float(len(idxs))

    if col not in header:
        raise KeyError(f"列不存在: {col}")
    idxs = _resolve_rows(rows_spec, len(rows))
    vals = []
    for i in idxs:
        if i < 1 or i > len(rows):
            raise IndexError(f"行越界: {i} (共 {len(rows)} 数据行)")
        v = coerce_num(rows[i - 1][col])
        if v is not None:
            vals.append(v)
    if op == "sum":
        return sum(vals)
    if op == "mean":
        return sum(vals) / len(vals) if vals else 0.0
    if op == "count":
        return float(len(vals))
    if op == "min":
        return min(vals) if vals else 0.0
    if op == "max":
        return max(vals) if vals else 0.0
    raise ValueError(f"不支持的算子: {op}")


# ───────────────────────── 单条核对 ─────────────────────────

def check_claim(claim, header, rows):
    label = claim.get("label", "(未命名)")
    value = claim.get("value")
    src = claim.get("source")
    der = claim.get("derived")

    if src:
        try:
            actual = cell_value(header, rows, int(src.get("row")), src.get("col"))
            # 字符串身份校验：源是文本时必须逐字符比对，不能 coerce_num 转数值
            if isinstance(actual, str) and not _looks_numeric(actual):
                if str(value) == actual:
                    return ("ok", label, f"源[{src.get('col')} 行{src.get('row')}] 文本匹配 = {actual!r}")
                return ("fail", label,
                        f"文本错位: 报告={value!r} 实际源={actual!r} ([{src.get('col')} 行{src.get('row')}])")
            av, bv = coerce_num(actual), coerce_num(value)
            if av is None or bv is None:
                return ("warn", label, f"数值无法解析: 报告={value} 源={actual}")
            if abs(av - bv) < 1e-6:
                return ("ok", label, f"源[{src.get('col')} 行{src.get('row')}] = {av}")
            return ("fail", label, f"抄错: 报告={bv} 实际源={av} ([{src.get('col')} 行{src.get('row')}])")
        except (IndexError, KeyError) as e:
            return ("fail", label, f"来源无效: {e}")

    if der:
        try:
            actual = recompute(header, rows, der.get("op"), der.get("col"), der.get("rows"))
            bv = coerce_num(value)
            if bv is None:
                return ("warn", label, f"报告值无法解析: {value}")
            if abs(actual - bv) < 1e-6:
                return ("ok", label, f"{der.get('op')}({der.get('col')}) 重算 = {actual}")
            return ("fail", label, f"派生不符: 报告={bv} 重算={actual} ({der.get('op')} {der.get('col')})")
        except (KeyError, ValueError, IndexError) as e:
            return ("fail", label, f"派生无效: {e}")

    return ("warn", label, "来源缺失：未标注 provenance（红色信号）")


# ───────────────────────── 解析 AI 产出 ─────────────────────────

def load_claims(path):
    p = Path(path)
    if p.suffix.lower() == ".json":
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data.get("claims", [])
        if isinstance(data, list):
            return data
        raise ValueError("JSON 产出必须是 {claims:[...]} 或 [...]")
    # 文本：解析行内标签
    with open(p, encoding="utf-8") as f:
        return parse_text_claims(f.read())


def parse_text_claims(text):
    claims = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        m = re.match(r"^(.*?)=\s*([-0-9.,%]+)", line)
        if not m:
            continue
        label, value = m.group(1).strip(), m.group(2).strip()
        claim = {"label": label, "value": value}
        src_m = re.search(r"来源[:：]\s*(?:文件\s*\S+\s*)?行\s*(\d+)\s*列\s*(\S+)", line)
        if src_m:
            claim["source"] = {"row": int(src_m.group(1)), "col": src_m.group(2)}
        der_m = re.search(r"派生[:：]\s*(\w+)\s*\(\s*(\S+?)\s*(?:,\s*行\s*([\d,\-]+))?\s*\)", line)
        if der_m:
            claim["derived"] = {"op": der_m.group(1), "col": der_m.group(2), "rows": der_m.group(3)}
        claims.append(claim)
    return claims


# ───────────────────────── 报告 ─────────────────────────

ICON = {"ok": "✅", "fail": "❌", "warn": "⚠️"}

def main():
    ap = argparse.ArgumentParser(description="数值血缘对账器")
    ap.add_argument("--source", required=True, help="原始数据文件 (.csv/.json/.xlsx)")
    ap.add_argument("--claims", required=True, help="AI 产出 (.json 清单 或 .md/.txt 行内标签)")
    args = ap.parse_args()

    try:
        header, rows = load_source(args.source)
    except ImportError as e:
        print(f"❌ 依赖缺失: {e}")
        sys.exit(3)
    except FileNotFoundError:
        print(f"❌ 原始数据文件不存在: {args.source}")
        sys.exit(3)
    except (ValueError, KeyError, IndexError, OSError) as e:
        print(f"❌ 原始数据无法加载: {e}")
        sys.exit(3)

    claims = load_claims(args.claims)
    if not claims:
        print("⚠️ 未解析到任何带标签的数值声明，请检查产出格式。")
        sys.exit(2)

    results = [check_claim(c, header, rows) for c in claims]

    print("=" * 64)
    print("数值血缘对账报告")
    print(f"原始数据: {LAST_SOURCE_DESC}  ({len(rows)} 数据行, 列: {header})")
    print(f"待核声明: {len(results)} 条")
    print("=" * 64)
    for status, label, detail in results:
        print(f"{ICON[status]} {label}: {detail}")
    print("-" * 64)

    n_ok = sum(1 for r in results if r[0] == "ok")
    n_fail = sum(1 for r in results if r[0] == "fail")
    n_warn = sum(1 for r in results if r[0] == "warn")
    print(f"命中 {n_ok} ｜ 错位/不符 {n_fail} ｜ 无法验证 {n_warn}")
    verdict = "PASS" if n_fail == 0 and n_warn == 0 else "FAIL"
    print(f"结论: {verdict}")
    print("=" * 64)

    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
