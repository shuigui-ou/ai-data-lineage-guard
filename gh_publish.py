#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gh_publish.py — 把本 skill 推到 GitHub（`shuigui-ou/ai-data-lineage-guard`）

沿用本机已验证的路径（预测系统 gh_publish_os.py 同款）：
  · `git push` 走环境代理会 502（CONNECT tunnel failed），`gh api` 同网络正常
  → 故用 Contents API 逐文件上传（幂等：已存在且内容一致则跳过）
  · base64 必须走 `--input <json文件>`：塞命令行会超 Windows 32KB 上限
  · 查远端时 **404 = 文件不存在（正常）**，其他错误才是查询失败（2026-10-21 踩过：
    误判成失败 → 新文件全部漏传）
  · TLS handshake timeout 是瞬时的，重试 3 次

只推纯文本源文件；icon.png 不进仓库（发布包与仓库都走纯文本口径）。
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
OWNER = "shuigui-ou"
REPO = os.environ.get("REPO_NAME", "ai-data-lineage-guard")
BRANCH = "main"

# 排除：二进制、点文件、试跑证据、构建产物
EXCLUDE_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
                  ".xlsx", ".xlsm", ".xls", ".parquet", ".db", ".sqlite",
                  ".pdf", ".zip", ".pyc", ".pyo", ".log"}
EXCLUDE_DIRS = {"trial", "__pycache__", ".git", "node_modules"}
EXCLUDE_NAMES = {".DS_Store", ".gitignore", "Thumbs.db"}


def gh(args, payload=None):
    cmd = ["gh"] + args
    tmp = None
    if payload is not None:
        tmp = SRC / "_gh_payload.json"
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        cmd += ["--input", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if tmp and tmp.exists():
        tmp.unlink()
    if r.returncode != 0:
        return None, (r.stderr or "").strip()[:400]
    try:
        return (json.loads(r.stdout) if r.stdout.strip() else {}), ""
    except Exception:
        return {}, ""


def collect_files():
    files = []
    for p in sorted(SRC.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(SRC)
        if rel.parts and rel.parts[0] in EXCLUDE_DIRS:
            continue
        if p.name in EXCLUDE_NAMES or p.name.startswith("."):
            continue
        if p.suffix.lower() in EXCLUDE_SUFFIX:
            continue
        files.append((rel.as_posix(), p))
    return files


def ensure_repo():
    """仓库不存在则创建（幂等）。"""
    res, err = gh(["api", "repos/%s/%s" % (OWNER, REPO)])
    if res is not None:
        print("[repo] 已存在: %s/%s" % (OWNER, REPO))
        return True
    res, err = gh(["api", "-X", "POST", "user/repos", "-f",
                    "name=%s" % REPO, "-f", "description=AI 统计数据核对：数字必须带出处，对不上的直接拦住",
                    "-f", "private=false"])
    if res is None:
        print("[repo] 创建失败: %s" % err[:200])
        return False
    print("[repo] 已创建: %s/%s" % (OWNER, REPO))
    return True


def main():
    if not ensure_repo():
        return 1

    files = collect_files()
    print("[publish] repo=%s/%s branch=%s files=%d"
          % (OWNER, REPO, BRANCH, len(files)))

    ok = skip = fail = 0
    failures = []
    for i, (rel, path) in enumerate(files, 1):
        raw = path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")

        exist, qerr = gh(["api", "repos/%s/%s/contents/%s?ref=%s"
                          % (OWNER, REPO, rel, BRANCH)])
        if exist is None:
            if "404" in (qerr or "") or "Not Found" in (qerr or ""):
                exist = {}          # 远端没有 → 新建
            else:
                fail += 1
                failures.append((rel, "查询远端失败（未上传）: %s" % qerr[:100]))
                print("  [%d/%d] QUERY-FAIL %s — %s" % (i, len(files), rel, qerr[:80]))
                continue
        sha = exist.get("sha") if isinstance(exist, dict) else None
        if sha:
            cur, _ = gh(["api", "repos/%s/%s/contents/%s?ref=%s"
                          % (OWNER, REPO, rel, BRANCH)])
            try:
                if base64.b64decode(cur["content"]).decode("utf-8", "replace") == \
                        raw.decode("utf-8", "replace"):
                    skip += 1
                    print("  [%d/%d] skip  %s" % (i, len(files), rel))
                    continue
            except Exception:
                pass

        payload = {"message": "chore: add %s" % rel, "content": b64}
        method = ["api", "-X", "PUT", "repos/%s/%s/contents/%s" % (OWNER, REPO, rel)]
        if sha:
            payload["sha"] = sha
        else:
            payload["branch"] = BRANCH
        res, err = gh(method, payload)
        tries = 0
        while res is None and "TLS handshake timeout" in (err or "") and tries < 3:
            tries += 1
            res, err = gh(method, payload)
        if res is None:
            fail += 1
            failures.append((rel, err))
            print("  [%d/%d] FAIL  %s — %s" % (i, len(files), rel, err[:120]))
        else:
            ok += 1
            print("  [%d/%d] ok    %s" % (i, len(files), rel))

    print("\n[publish] 上传=%d 跳过=%d 失败=%d" % (ok, skip, fail))
    if failures:
        print("失败明细：")
        for r, e in failures[:10]:
            print("  ✗ %s : %s" % (r, e))
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
