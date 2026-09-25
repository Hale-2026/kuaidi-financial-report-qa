#!/usr/bin/env python3
"""把 Markdown 报告合成一份可交付的 PDF（中文打印排版）。

用 markdown 库转 HTML，再用本机 Chrome 无头打印 —— 不引入 LaTeX/weasyprint
那套重依赖。

    python code/make_report.py
    → output/作业3A_报告.pdf      （一页结论 + 逐题评测台账）
    → output/一页结论.pdf          （只含一页结论）
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

try:
    import markdown
except ImportError:                                              # noqa: BLE001
    sys.exit("!! 缺 markdown 库（用于 Markdown → HTML 转换）。\n"
             "   装（在项目 venv 里）：pip install markdown\n"
             "   或一键：zsh code/bootstrap_deps.sh —— 它会自动补装\n"
             "   （markdown 已列在 code/requirements.txt 的必需依赖里）")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output")

# Chrome/Chromium 常见安装位置 —— 不写死单一路径，换浏览器或换系统也能找到
CHROME_CANDS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]
CHROME = next((p for p in CHROME_CANDS if os.path.exists(p)),
              CHROME_CANDS[0])

CSS = """
@page { size: A4; margin: 17mm 15mm 16mm; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "PingFang SC", "Hiragino Sans GB",
       "Songti SC", "Microsoft YaHei", sans-serif;
       font-size: 10.5pt; line-height: 1.72; color: #1b1b1b; margin: 0; }
h1 { font-size: 17pt; color: #111; margin: 0 0 4px;
     border-bottom: 2.5px solid #b5121b; padding-bottom: 8px; }
h2 { font-size: 13.5pt; color: #b5121b; margin: 22px 0 8px;
     border-left: 4px solid #b5121b; padding-left: 9px; }
h3 { font-size: 11.5pt; color: #222; margin: 16px 0 6px; }
p { margin: 7px 0; }
strong { color: #000; }
table { border-collapse: collapse; width: 100%; font-size: 9pt;
        margin: 10px 0; page-break-inside: avoid; }
th, td { border: 1px solid #d0d0d0; padding: 5px 7px;
         text-align: left; vertical-align: top; line-height: 1.5; }
th { background: #f4f4f4; font-weight: 600; }
tr:nth-child(even) td { background: #fafafa; }
code { background: #f2f2f2; padding: 1px 4px; border-radius: 3px;
       font-size: 9pt; font-family: "SF Mono", Menlo, monospace; }
pre { background: #f7f7f7; border: 1px solid #e6e6e6; border-radius: 4px;
      padding: 9px 11px; font-size: 8.8pt; line-height: 1.5;
      white-space: pre-wrap; word-break: break-all; }
pre code { background: none; padding: 0; }
blockquote { border-left: 3px solid #d8d8d8; margin: 9px 0;
             padding: 2px 0 2px 12px; color: #4d4d4d; font-size: 9.8pt; }
hr { border: none; border-top: 1px solid #e4e4e4; margin: 18px 0; }
ul, ol { padding-left: 22px; margin: 7px 0; }
li { margin: 3px 0; }
.cover { text-align: center; padding-top: 26mm; page-break-after: always; }
.cover .t { font-size: 26pt; font-weight: 700; color: #b5121b;
            letter-spacing: 2px; margin-bottom: 10px; }
.cover .s { font-size: 13pt; color: #444; margin-bottom: 30px; }
.cover .m { font-size: 10.5pt; color: #666; line-height: 2; }
.cover .box { display: inline-block; text-align: left; margin-top: 16px;
              border: 1px solid #e0e0e0; border-radius: 6px;
              padding: 14px 22px; font-size: 10pt; color: #333;
              line-height: 1.9; background: #fcfcfc; }
.brk { page-break-before: always; }
"""

COVER = """<div class="cover">
  <div class="t">作业 3-A</div>
  <div class="s">快递物流行业财报问答知识库</div>
  <div class="m">
    从交易所下载年报与半年报 · 提取文字还原表格 · 向量 + BM25 双索引<br>
    带出处的问答页面 · 10 道题逐题评测
  </div>
  <div class="box">
    <b>语料</b>：17 家公司 × 2 类报告 = 34 份（A 股 + 港股）<br>
    <b>知识块</b>：18,596 块（其中表格块 8,321）<br>
    <b>索引</b>：BM25（jieba + 62 词领域词典）＋ bge-small-zh（512 维）<br>
    <b>评测</b>：10 题 × 4 种检索模式，逐题记录召回与出处<br>
    <b>数据源</b>：深交所官网 · 巨潮资讯网 · 港交所披露易
  </div>
</div>
"""

TITLES = {
    "一页结论.md": "第一部分 · 一页结论",
    "eval_table.md": "第二部分 · 逐题评测台账",
    "工作流程说明.md": "附录 · 工作流程说明",
}


def md2html(path: str, heading: str | None) -> str:
    src = open(path, encoding="utf-8").read()
    if heading:
        src = f"# {heading}\n\n" + src
    html = markdown.markdown(
        src, extensions=["tables", "fenced_code", "sane_lists", "nl2br"])
    return html


def html_to_pdf(html: str, pdf_path: str) -> bool:
    doc = (f"<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
           f"<style>{CSS}</style></head><body>{html}</body></html>")
    fd, tmp = tempfile.mkstemp(suffix=".html", dir="/tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(doc)
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    cmd = [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
           "--disable-dev-shm-usage", "--no-first-run",
           "--no-default-browser-check", "--disable-extensions",
           "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
           "--virtual-time-budget=8000", "file://" + tmp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    os.remove(tmp)
    if not os.path.exists(pdf_path):
        print("  !! PDF 未生成：", (r.stderr or r.stdout)[-400:])
        return False
    return True


def build(parts: list[tuple[str, str]], out_name: str, cover: str = "") -> None:
    body = cover
    for i, (fn, heading) in enumerate(parts):
        p = os.path.join(OUT, fn)
        if not os.path.exists(p):
            print(f"  跳过（不存在）：{fn}")
            continue
        cls = "" if not cover and i == 0 else "brk"
        body += f'<div class="{cls}">{md2html(p, heading)}</div>'
    # 目录页码控制：正文从新页开始
    pdf = os.path.join(OUT, out_name)
    if html_to_pdf(body, pdf):
        print(f"  → output/{out_name}   "
              f"{os.path.getsize(pdf) / 1024:.0f} KB")


def main() -> int:
    if not os.path.exists(CHROME):
        print("!! 找不到 Chrome / Edge / Chromium，无法把 Markdown 打印成 PDF。")
        print("   已查找这些位置：")
        for p in CHROME_CANDS:
            print(f"     {p}")
        print("   装任一即可；这一步只影响 PDF 生成，"
              "前面 1–5 步（下载/提取/建索引/问答/评测）不受影响。")
        return 1
    print("生成 PDF 报告 ...")
    if os.path.exists(os.path.join(OUT, "一页结论.md")):
        build([("一页结论.md", None)], "一页结论.pdf")
    else:
        print("  跳过 一页结论.pdf —— output/一页结论.md 不存在")
        print("  （该文件是人工撰写的结论文本，未纳入代码仓库；"
              "跑完 05_eval.py 后可参照 output/eval_table.md 自拟）")
    build([("一页结论.md", TITLES["一页结论.md"]),
           ("eval_table.md", TITLES["eval_table.md"])],
          "作业3A_报告.pdf", cover=COVER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
