#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 parquet 测试样本（需要 pyarrow；.parquet 样本本身不随发布包分发）。

设计要点：source_sales.parquet 与现有 source_sales.csv **逐行同构**，
这样同一份 claims 可以分别喂给 csv 分支和 parquet 分支，
用来做「同数据不同格式 → 结论必须完全一致」的等价性检验。

另外再造两个边界样本：
    empty.parquet    → 空表（验证：显式报错，不静默 PASS）
    corrupt.parquet  → 非 parquet 内容（验证：干净报错，不漏 traceback）
"""
import os
import sys

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    print("需要 pyarrow：pip install pyarrow")
    sys.exit(1)

HERE = os.path.dirname(os.path.abspath(__file__))

# ── 1. 与 source_sales.csv 同构的主样本 ──────────────────────────
rows = [
    {"id": 1, "product": "A", "amount": 100.0, "qty": 2},
    {"id": 2, "product": "B", "amount": 250.0, "qty": 1},
    {"id": 3, "product": "C", "amount": 300.0, "qty": 3},
    {"id": 4, "product": "D", "amount": 150.0, "qty": 2},
    {"id": 5, "product": "E", "amount": 200.0, "qty": 4},
]
schema = pa.schema([
    ("id", pa.int64()),
    ("product", pa.string()),
    ("amount", pa.float64()),
    ("qty", pa.int64()),
])
out = os.path.join(HERE, "source_sales.parquet")
pq.write_table(pa.Table.from_pylist(rows, schema=schema), out)
print("wrote", out)

# ── 2. 类型覆盖样本：bool / 时间戳 / 空值 / 嵌套列 ────────────────
rows2 = [
    {"name": "甲", "flag": True,  "ts": 1767571800, "note": "首条", "tags": ["x", "y"]},
    {"name": "乙", "flag": False, "ts": 1767658200, "note": None,    "tags": []},
]
schema2 = pa.schema([
    ("name", pa.string()),
    ("flag", pa.bool_()),
    ("ts", pa.timestamp("s")),
    ("note", pa.string()),
    ("tags", pa.list_(pa.string())),
])
out2 = os.path.join(HERE, "types.parquet")
pq.write_table(pa.Table.from_pylist(rows2, schema=schema2), out2)
print("wrote", out2)

# ── 3. 边界样本 ──────────────────────────────────────────────────
empty = pa.Table.from_pylist([], schema=schema)
out3 = os.path.join(HERE, "empty.parquet")
pq.write_table(empty, out3)
print("wrote", out3)

out4 = os.path.join(HERE, "corrupt.parquet")
with open(out4, "wb") as f:
    f.write(b"this is definitely not parquet")
print("wrote", out4)
