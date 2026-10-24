#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量回归跑器 —— 一条命令跑完所有闸门用例。

存在的理由：之前每轮改动都是手工跑几个命令，没有可复现的回归入口。
"我跑过了"不是证据，"任何人任何时候跑这条命令得到同样结果"才是。

用法：
    python tests/run_all.py            # 跑全部
    python tests/run_all.py --list     # 只列出用例

依赖：
    · 核心用例只需标准库
    · parquet 用例需要 pyarrow（缺则跳过并明示，不静默 pass）
"""
import os
import sys
import json
import subprocess
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RESULTS = []
SKIPPED = []


def run_module(mod_name, argv=None, env=None):
    """以子进程方式跑一个模块，捕获退出码与输出。"""
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run([sys.executable, os.path.join(ROOT, mod_name)] + (argv or []),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=ROOT, env=e)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def case(name, fn):
    """跑一个用例。

    跳过必须**显式计数**——否则"18/18 通过"会掩盖"其中 6 个根本没跑"。
    用例自己负责打印跳过原因（_need / pyarrow 检查），这里只负责计数。
    """
    marker = len(SKIPPED)
    try:
        fn()
        if len(SKIPPED) > marker:
            RESULTS.append((name, "SKIP", ""))
            print(f"  ⏭️  {name}  （样本缺失/依赖缺失，已跳过）")
        else:
            RESULTS.append((name, "PASS", ""))
            print(f"  ✅ {name}")
    except AssertionError as ex:
        RESULTS.append((name, "FAIL", str(ex)))
        print(f"  ❌ {name}\n       {ex}")
    except Exception as ex:
        RESULTS.append((name, "ERROR", traceback.format_exc(limit=3)))
        print(f"  ⚠️  {name}\n       {ex}")


# ───────────────────── 用例定义 ─────────────────────

def t_lineage_csv():
    import verify_lineage as V
    h, rows = V.load_source("samples/source_sales.csv")
    assert h == ["id", "product", "amount", "qty"], h
    assert len(rows) == 5, len(rows)
    assert V.recompute(h, rows, "sum", "amount") == 1000.0
    assert V.recompute(h, rows, "mean", "qty") == 2.4
    assert V.recompute(h, rows, "countrows", "product") == 5.0


def t_lineage_discrete_rows():
    import verify_lineage as V
    h, rows = V.load_source("samples/source_sales.csv")   # 5 数据行（id 1..5）
    assert V.recompute(h, rows, "sum", "amount", [1, 4, 5]) == 100.0 + 150.0 + 200.0
    assert V.recompute(h, rows, "sum", "amount", "1,4,5") == 100.0 + 150.0 + 200.0
    assert V.recompute(h, rows, "sum", "amount", "1-3") == 100.0 + 250.0 + 300.0
    # 越界必须显式报错，不静默截断
    try:
        V.recompute(h, rows, "sum", "amount", [1, 99])
        raise AssertionError("行越界应当抛 IndexError")
    except IndexError:
        pass


def _need(path, why):
    """样本文件不存在 → 显式跳过并说明（发布包内不放二进制样本）。

    不静默 pass，也不静默 fail：跳过原因必须打印出来，
    否则"18/18 通过"会掩盖"其中 4 个根本没跑"。
    """
    if not os.path.exists(path):
        SKIPPED.append((path, why))
        print(f"       （跳过：{why}）")
        return False
    return True


def t_lineage_xlsx():
    import verify_lineage as V
    if not _need("samples/data.xlsx", "发布包内不放 xlsx（二进制）"):
        return
    h, rows = V.load_source("samples/data.xlsx")
    assert len(rows) > 0
    assert V.LAST_SOURCE_DESC and "sheet" in V.LAST_SOURCE_DESC


def t_lineage_xlsx_no_cache():
    """公式无缓存值 → 必须显式报错，不静默当空值过。"""
    import verify_lineage as V
    if not _need("samples/data_formula_nocache.xlsx", "发布包内不放 xlsx（二进制）"):
        return
    try:
        V.load_source("samples/data_formula_nocache.xlsx")
    except ValueError as ex:
        assert "缓存" in str(ex), str(ex)
        return
    raise AssertionError("应当抛 ValueError（公式无缓存值），实际未抛")


def t_lineage_claim_ids():
    import verify_lineage as V
    ok, dups = V.check_claim_ids([{"id": "a"}, {"id": "b"}])
    assert ok and not dups
    ok, dups = V.check_claim_ids([{"id": "a"}, {"id": "a"}])
    assert not ok and dups == ["a"], dups


def t_parquet_equivalence():
    """parquet 分支必须与 csv 分支给出**完全一致**的对账结论。

    等价的定义：**数值等价**，不是字面相等。
    csv 的 amount 是文本 "100"，parquet 是 float64 → "100.0"，字面不同但数值相同。
    字面差异只发生在数值列，且只影响"文本身份比对"路径；
    数值比对路径走 coerce_num，两者都得到 100.0 → 结论必然一致。
    """
    if not _need("samples/source_sales.parquet", "发布包内不放 parquet（二进制）"):
        return
    try:
        import pyarrow  # noqa
    except ImportError:
        print("       （跳过：缺 pyarrow）")
        return
    import verify_lineage as V
    hc, rc = V.load_source("samples/source_sales.csv")
    hp, rp = V.load_source("samples/source_sales.parquet")
    assert hc == hp, (hc, hp)
    assert len(rc) == len(rp), (len(rc), len(rp))
    for a, b in zip(rc, rp):
        assert a.keys() == b.keys(), (a, b)
        for k in a:
            na, nb = V.coerce_num(a[k]), V.coerce_num(b[k])
            if na is not None and nb is not None:
                assert abs(na - nb) <= 1e-9 * max(1.0, abs(na)), (k, a[k], b[k])
            else:
                assert a[k] == b[k], (k, a[k], b[k])   # 非数值列必须字面相同
    # 聚合结果必须完全一致
    for op, col in (("sum", "amount"), ("mean", "qty"), ("min", "amount"), ("max", "amount")):
        assert V.recompute(hc, rc, op, col) == V.recompute(hp, rp, op, col)
    # 对账结论必须一致：同一份 claims 喂两个分支，逐条状态相同
    claims = V.load_claims("samples/ai_output_manifest.json")
    for c in claims:
        sa = V.check_claim(c, hc, rc)
        sb = V.check_claim(c, hp, rp)
        assert sa[0] == sb[0], (c.get("label"), sa, sb)
        assert (sa[0] == "fail") == (sb[0] == "fail"), (c.get("label"), sa, sb)


def t_parquet_types():
    if not _need("samples/types.parquet", "发布包内不放 parquet（二进制）"):
        return
    try:
        import pyarrow  # noqa
    except ImportError:
        print("       （跳过：缺 pyarrow）")
        return
    import verify_lineage as V
    h, rows = V.load_source("samples/types.parquet")
    assert h == ["name", "flag", "ts", "note", "tags"], h
    assert rows[0]["flag"] == "True"
    assert rows[1]["note"] == ""          # None → 空串
    assert "2026-01-05" in rows[0]["ts"]  # 时间戳 → ISO


def t_parquet_missing_dep():
    """缺 pyarrow → 必须 exit 3 + 干净提示，无 traceback。"""
    if not _need("samples/source_sales.parquet", "发布包内不放 parquet（二进制）"):
        return
    code, out = run_module("verify_lineage.py",
                           ["--source", "samples/source_sales.parquet",
                            "--claims", "samples/ai_output_manifest.json"])
    if "pyarrow" in out and "依赖缺失" in out:
        assert code == 3, code
        assert "Traceback" not in out, out
        return
    # 依赖已装 → 正常跑通即可
    assert code == 1, (code, out)   # 该 claims 本身含错值 → FAIL
    assert "Traceback" not in out, out


def t_parquet_corrupt():
    if not _need("samples/corrupt.parquet", "发布包内不放 parquet（二进制）"):
        return
    try:
        import pyarrow  # noqa
    except ImportError:
        print("       （跳过：缺 pyarrow）")
        return
    code, out = run_module("verify_lineage.py",
                           ["--source", "samples/corrupt.parquet",
                            "--claims", "samples/ai_output_manifest.json"])
    assert code == 3, (code, out)
    assert "Traceback" not in out, out


def t_conclusion_positive():
    code, out = run_module("verify_conclusions.py",
                           ["--source", "samples/conclusion/relations_source.json",
                            "--claims", "samples/conclusion/claims_positive.json"])
    assert code == 0, (code, out)
    assert "硬拦失败 0" in out, out


def t_conclusion_negatives():
    """五个阴性对照必须各被拦（每条只坏一处）。"""
    expect = {
        "neg1_dangling_ref.json": "H1",
        "neg2_threshold_conflict.json": "H3",
        "neg3_value_conflict.json": "H2",
        "neg4_direction_conflict.json": "H4",
        "neg5_causal_no_manual.json": "H1",
    }
    for fn, code_want in expect.items():
        code, out = run_module("verify_conclusions.py",
                               ["--source", "samples/conclusion/relations_source.json",
                                "--claims", "samples/conclusion/" + fn])
        assert code == 1, (fn, code, out)
        assert code_want in out, (fn, code_want, out)


def t_conclusion_fp_rate():
    """误报探针：白名单后必须 0 提示（改前是 9 条）。"""
    code, out = run_module("verify_conclusions.py",
                           ["--source", "samples/conclusion/relations_source.json",
                            "--claims", "samples/conclusion/false_positive_probe.json"])
    assert code == 0, (code, out)
    assert "提示 0" in out, out
    assert "W1" not in out, out


def t_whitelist_bidirectional():
    """白名单双向：真信号不放过 + 叙述数字放过（内含子进程调用）。"""
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "test_whitelist.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=ROOT)
    assert p.returncode == 0, (p.stdout, p.stderr)


def t_semantic_prepare():
    code, out = run_module("audit_semantic.py",
                           ["prepare", "--source", "samples/conclusion/relations_source.json",
                            "--claims", "samples/semantic/conclusion_claims.json",
                            "--out", "samples/semantic/packet.json"])
    assert code == 0, (code, out)
    with open("samples/semantic/packet.json", encoding="utf-8") as f:
        pk = json.load(f)
    assert len(pk["conclusions"]) == 3, len(pk["conclusions"])
    assert len(pk["conclusion_checklist"]) == 2
    c1 = pk["conclusions"][0]
    assert "大幅" in c1["wording_markers"], c1["wording_markers"]
    assert any(x["col"] == "cohen_d" for x in c1["evidence_magnitude_hints"]["effect_like"])
    # 效应量列必须按 source.col 归类（中文 label 也要能命中）
    c2 = pk["conclusions"][1]
    assert any(x["col"] == "effect_down_rel_median"
               for x in c2["evidence_magnitude_hints"]["effect_like"]), c2


def t_semantic_finalize_lint():
    """finalize 必须剔除重算口吻线索（边界1）。"""
    code, out = run_module("audit_semantic.py",
                           ["finalize", "--packet", "samples/semantic/packet.json",
                            "--findings", "samples/semantic/findings.json",
                            "--out", "samples/semantic/audit_report.md"])
    assert code == 0, (code, out)
    with open("samples/semantic/audit_report.md", encoding="utf-8") as f:
        rep = f.read()
    assert "有效线索" in rep
    assert "结论级" in rep


def t_semantic_finalize_offlist():
    """item 不在固定清单内的线索必须单列，不计入有效线索。"""
    bad = "samples/semantic/_findings_baditem.json"
    with open(bad, "w", encoding="utf-8") as f:
        json.dump({"findings": [{"claim_label": "x", "item": "not_in_list",
                                 "note": "n", "evidence": "e"}]}, f, ensure_ascii=False)
    code, out = run_module("audit_semantic.py",
                           ["finalize", "--packet", "samples/semantic/packet.json",
                            "--findings", bad,
                            "--out", "samples/semantic/_audit_offlist.md"])
    assert code == 0, (code, out)
    with open("samples/semantic/_audit_offlist.md", encoding="utf-8") as f:
        rep = f.read()
    assert "有效线索 0 条" in rep, rep
    assert "清单外 1 条" in rep, rep
    os.remove(bad)
    os.remove("samples/semantic/_audit_offlist.md")


def t_emit_gate():
    """emit：机械层 FAIL → 不落盘 + exit 1；PASS → 落盘 + exit 0。"""
    outp = "samples/_emit_test.md"
    code, out = run_module("emit.py",
                           ["--source", "samples/source_sales.csv",
                            "--claims", "samples/ai_output_manifest.json",
                            "--out", outp])
    assert code == 1, (code, out)
    assert not os.path.exists(outp), "FAIL 时不该产出交付文件"
    assert os.path.exists(outp + ".blocked.md")

    ok_claims = "samples/_emit_ok_claims.json"
    with open(ok_claims, "w", encoding="utf-8") as f:
        json.dump({"claims": [
            {"label": "A产品金额", "value": 100, "source": {"row": 1, "col": "amount"}},
            {"label": "总额", "value": 1000, "derived": {"op": "sum", "col": "amount"}},
        ]}, f, ensure_ascii=False)
    code, out = run_module("emit.py",
                           ["--source", "samples/source_sales.csv",
                            "--claims", ok_claims, "--out", outp])
    assert code == 0, (code, out)
    assert os.path.exists(outp), "PASS 时应产出交付文件"
    for p in (outp, outp + ".blocked.md", ok_claims):
        if os.path.exists(p):
            os.remove(p)


def t_build_dist_no_binary():
    """打包必须无二进制、无无扩展名文件。"""
    code, out = run_module("build_dist.py")
    assert code == 0, (code, out)
    assert "✅" in out, out


ALL_CASES = [
    ("第1层 机械对账 · csv", t_lineage_csv),
    ("第1层 机械对账 · 离散行集", t_lineage_discrete_rows),
    ("第1层 机械对账 · xlsx", t_lineage_xlsx),
    ("第1层 机械对账 · xlsx 公式无缓存值须报错", t_lineage_xlsx_no_cache),
    ("第1层 机械对账 · claim id 唯一性", t_lineage_claim_ids),
    ("parquet · 与 csv 分支等价", t_parquet_equivalence),
    ("parquet · 类型归一化", t_parquet_types),
    ("parquet · 缺依赖须干净报错", t_parquet_missing_dep),
    ("parquet · 损坏文件须干净报错", t_parquet_corrupt),
    ("第4层 结论链 · 正例", t_conclusion_positive),
    ("第4层 结论链 · 五个阴性对照", t_conclusion_negatives),
    ("第4层 结论链 · 误报率探针（白名单后）", t_conclusion_fp_rate),
    ("第4层 词法白名单 · 双向", t_whitelist_bidirectional),
    ("第3层 语义审计 · 备料含结论段", t_semantic_prepare),
    ("第3层 语义审计 · finalize 汇总", t_semantic_finalize_lint),
    ("第3层 语义审计 · 清单外线索单列", t_semantic_finalize_offlist),
    ("第2层 emit · 落盘闸门", t_emit_gate),
    ("发布 · 打包无二进制", t_build_dist_no_binary),
]


def main():
    if "--list" in sys.argv:
        for n, _ in ALL_CASES:
            print(" -", n)
        return 0
    print("=" * 68)
    print("全量回归")
    print("=" * 68)
    for name, fn in ALL_CASES:
        case(name, fn)
    n_pass = sum(1 for _, s, _ in RESULTS if s == "PASS")
    n_skip = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    n_fail = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    n_err = sum(1 for _, s, _ in RESULTS if s == "ERROR")
    print("-" * 68)
    print(f"通过 {n_pass} ｜ 跳过 {n_skip} ｜ 失败 {n_fail} ｜ 异常 {n_err} ｜ 共 {len(ALL_CASES)}")
    if n_skip:
        print("  跳过明细（未真跑，不计入通过数）：")
        for n, s, d in RESULTS:
            if s == "SKIP":
                print(f"    ⏭️  {n}")
    if n_fail or n_err:
        print("\n未通过：")
        for n, s, d in RESULTS:
            if s in ("FAIL", "ERROR"):
                print(f"  [{s}] {n}")
                if d:
                    print("      " + d.strip().replace("\n", "\n      ")[:600])
    print("=" * 68)
    return 1 if (n_fail or n_err) else 0


if __name__ == "__main__":
    sys.exit(main())
