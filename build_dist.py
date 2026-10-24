#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_dist.py — 打包成可上传 SkillHub 的 zip（纯文本，无图片/无二进制）

规则（对齐已发布 skill 的实测口径）：
  · zip 内**禁止任何图片/二进制**（png/jpg/ico/xlsx/parquet 等），否则上传报「不支持的文件类型」
  · 点文件（.gitignore/.DS_Store）也不放进去
  · skillhub_meta.json 随包自带（纯文本），供填表复制
  · icon.png 由用户在表单处**单独上传**，不进包
"""
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent / "ai-data-lineage-guard.zip"

# 二进制/图片/点文件/运行时产物 一律排除
EXCLUDE_SUFFIX = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".bmp",
    ".xlsx", ".xlsm", ".xls", ".parquet", ".db", ".sqlite", ".pdf",
    ".zip", ".pyc", ".pyo", ".pkl", ".npy", ".log",
}
EXCLUDE_NAMES = {".DS_Store", ".gitignore", "Thumbs.db"}
EXCLUDE_DIRS = {"__pycache__", ".git", "node_modules", ".evolution", "icon"}

# 运行时产物 / 试跑残留：samples/ 下只保留「输入」与「claims」，
# 排除一切由 emit.py/verify 生成的交付件与隔离报告（名字规则可识别）。
def _is_runtime_artifact(rel: Path) -> bool:
    n = rel.name
    if n.endswith(".blocked.md"):        # 隔离报告（运行时生成）
        return True
    # emit 生成的交付件：deliver_* / d1-d4 / e1-e3 这类短名
    stem = n[:-3] if n.endswith(".md") else n
    if stem.startswith("deliver"):
        return True
    if len(stem) <= 3 and len(stem) == 2 and stem[0] in "de" and stem[1].isdigit():
        return True
    return False


def should_skip(path: Path) -> bool:
    if path.name in EXCLUDE_NAMES:
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIX:
        return True
    return any(part in EXCLUDE_DIRS for part in path.parts)


def main():
    if OUT.exists():
        OUT.unlink()

    included, skipped = [], []
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ROOT.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT)
            # trial/ 是试跑证据(含xlsx)，体积大且非发布必需 → 排除
            if rel.parts and rel.parts[0] == "trial":
                skipped.append(str(rel) + "  (试跑证据，不发布)")
                continue
            if should_skip(rel):
                skipped.append(str(rel) + "  (二进制/点文件)")
                continue
            if _is_runtime_artifact(rel):
                skipped.append(str(rel) + "  (运行时产物/测试残留)")
                continue
            z.write(p, arcname=f"ai-data-lineage-guard/{rel.as_posix()}")
            included.append(str(rel))

    size_kb = OUT.stat().st_size / 1024
    print(f"✅ 打包完成: {OUT.name}  ({size_kb:.1f} KB)")
    print(f"   纳入 {len(included)} 个文件:")
    for f in included:
        print(f"     + {f}")
    print(f"   排除 {len(skipped)} 项:")
    for f in skipped:
        print(f"     - {f}")

    # 自检：确认包内无二进制
    with zipfile.ZipFile(OUT) as z:
        bad = [n for n in z.namelist() if Path(n).suffix.lower() in EXCLUDE_SUFFIX]
    if bad:
        print(f"❌ 包内仍有二进制（上传会被拒）: {bad}")
        return 1
    print("   自检: 包内无图片/二进制 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())