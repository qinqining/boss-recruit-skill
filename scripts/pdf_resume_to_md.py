#!/usr/bin/env python3
"""
将候选人简历 PDF 抽成 Markdown，便于 Claude Code 直接读文本（非版面还原）。

依赖：pip install pymupdf

用法（仓库根目录，Windows 推荐 py）：
  py scripts/pdf_resume_to_md.py 面试方案生成任务/候选人简历/某简历.pdf
  py scripts/pdf_resume_to_md.py path/to.pdf path/to/out.md

扫描件/图片型 PDF 需 OCR（本脚本不处理），请换用 OCR 工具或人工粘贴正文。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    print("需要：pip install pymupdf", file=sys.stderr)
    raise SystemExit(1) from None


def main() -> int:
    ap = argparse.ArgumentParser(description="简历 PDF → Markdown（纯文本）")
    ap.add_argument("pdf", type=Path, help="输入 .pdf")
    ap.add_argument(
        "out_md",
        type=Path,
        nargs="?",
        default=None,
        help="输出 .md（默认同目录同名 .md）",
    )
    args = ap.parse_args()
    pdf = args.pdf.resolve()
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        print("请提供存在的 .pdf 文件", file=sys.stderr)
        return 1
    out = args.out_md.resolve() if args.out_md else pdf.with_suffix(".md")

    doc = fitz.open(pdf)
    n = len(doc)
    parts: list[str] = [f"# 简历摘录：{pdf.name}\n", f"> 由 PDF 自动抽取，版式与表格可能丢失；以原件为准。\n\n"]
    empty_pages = 0
    for i in range(n):
        text = doc[i].get_text("text") or ""
        if not text.strip():
            empty_pages += 1
        parts.append(f"## 第 {i + 1} 页\n\n")
        parts.append(text.strip() or "（本页无文本层，可能为扫描件或纯图）\n")
        parts.append("\n\n")
    doc.close()

    body = "".join(parts)
    body = re.sub(r"\n{4,}", "\n\n\n", body)
    out.write_text(body, encoding="utf-8")
    print(f"已写入：{out}（{n} 页；无文本层页数 {empty_pages}）")
    if empty_pages == n:
        print("警告：所有页均无文本层，可能为扫描 PDF，请用 OCR 或人工处理。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
