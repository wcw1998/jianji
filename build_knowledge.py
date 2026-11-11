#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 knowledge/*.md 打包成 knowledge.js，供 index.html 载入。

文件名前缀决定归属：
  00-*  -> global（所有洞察共用）
  01-*  -> insight    02-* -> value     03-* -> invert
  04-*  -> second     05-* -> cbt       06-* -> mbti

改完 md 重新跑一次：python build_knowledge.py
"""

import json
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
KB_DIR = os.path.join(ROOT, "knowledge")
OUT = os.path.join(ROOT, "knowledge.js")

PREFIX_MAP = {
    "00": "global",
    "01": "insight",
    "02": "value",
    "03": "invert",
    "04": "second",
    "05": "cbt",
    "06": "mbti",
}


def title_of(text, fallback):
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def main():
    packs = {}
    if not os.path.isdir(KB_DIR):
        raise SystemExit("找不到 knowledge 目录")

    for fn in sorted(os.listdir(KB_DIR)):
        if not fn.lower().endswith(".md"):
            continue
        m = re.match(r"(\d\d)", fn)
        key = PREFIX_MAP.get(m.group(1)) if m else None
        if not key:
            print("  跳过（无前缀映射）:", fn)
            continue
        with open(os.path.join(KB_DIR, fn), encoding="utf-8") as f:
            text = f.read().strip()
        if key in packs:
            # 同前缀多文件时拼接
            packs[key]["text"] += "\n\n" + text
        else:
            packs[key] = {"name": title_of(text, fn), "file": fn, "text": text}
        print("  %-8s <- %s (%d 字)" % (key, fn, len(text)))

    body = "// 由 build_knowledge.py 自动生成，请勿手改；改 knowledge/*.md 后重新生成\n"
    body += "window.KNOWLEDGE_PACKS = " + json.dumps(packs, ensure_ascii=False, indent=2) + ";\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(body)
    print("\n已生成 %s（%d 个资料包，%.1f KB）" % (
        os.path.basename(OUT), len(packs), os.path.getsize(OUT) / 1024))


if __name__ == "__main__":
    main()
