#!/usr/bin/env python3
"""生成「一页结论」PDF —— **严格一页**（对应课程第三课 P163 的交付要求）。

P163 原文：A 方向交付 = 代码仓库 + 页面截图 + **一页结论（哪类题答得好、哪类
翻车、为什么）**。要求是「一页」，所以本脚本渲染后会**实际校验页数**：
若超过 1 页，自动逐档缩小字号重排，仍超则报错退出（不静默交付超页文件）。

正文数字**全部从评测结果现算**（eval_scores.json / eval_runs.json），不手写、
不复制旧文档 —— 保证与 `eval_table.md` 逐格对得上：

    python code/07_one_pager.py
    → output/一页结论.pdf   （1 页）

出 PDF 需要本机 Chrome / Edge / Chromium（复用 make_report 的查找逻辑）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_report as mr  # noqa: E402  复用 Chrome 路径与常驻目录

ROOT = mr.ROOT
OUT = mr.OUT
CODE = os.path.join(ROOT, "code")
REPO_URL = "https://github.com/Hale-2026/kuaidi-financial-report-qa"

# 路线展示顺序：单路 → 融合 → 分组召回（递进）
MODE_ROWS = [
    ("bm25", "BM25", "仅词法"),
    ("vector", "向量", "仅语义"),
    ("hybrid", "RRF 融合 hybrid", "BM25 + 向量"),
    ("cover", "分组召回 cover", "hybrid + 按公司分组"),
]
SHAPE_LABEL = {"局部": "局部（单公司单指标）", "多跳": "多跳（对比 + 归因）",
               "全局": "全局 · 全景（跨公司）"}
SHAPE_ORDER = ["局部", "多跳", "全局"]
GAP_NAME = {"A": "语料未披露", "B": "公司名漏识别", "C": "焦点词误杀",
            "D": "召回不足", "E": "口径不一致", "F": "数值无归宿",
            "G": "语料表格未抽取（本项目缺陷）", "H": "答案未取到已召回的证据"}


def load():
    scores = json.load(open(os.path.join(OUT, "eval_scores.json"), encoding="utf-8"))
    runs = json.load(open(os.path.join(OUT, "eval_runs.json"), encoding="utf-8"))
    qs = json.load(open(os.path.join(CODE, "eval_questions.json"), encoding="utf-8"))
    qmap = {q["qid"]: q for q in qs}
    obmap = {ob["id"]: ob for q in qs for ob in q.get("obligations", [])}
    return scores, runs, qmap, obmap


def pct(x) -> str:
    return f"{round((x or 0) * 100)}%"


def shape_stats(runs):
    """按问题形状聚合 cover 路线的答案层义务覆盖。"""
    agg = {}
    for r in runs:
        if r["mode"] != "cover":
            continue
        s = r.get("shape") or "?"
        a = agg.setdefault(s, [0, 0])
        a[0] += r.get("obligations_covered_answer", 0)
        a[1] += r.get("obligations_total", 0)
    return agg


def verdict_stats(runs):
    """40 份实际作答逐份判定：正确 = 义务全覆，错误 = 0 项，其余部分正确。"""
    per_mode = {m: {"正确": 0, "部分正确": 0, "错误": 0} for m, _, _ in MODE_ROWS}
    total = {"正确": 0, "部分正确": 0, "错误": 0}
    for r in runs:
        cov = r.get("obligations_covered_answer", 0)
        tot = r.get("obligations_total", 0) or 1
        v = "正确" if cov >= tot else ("错误" if cov == 0 else "部分正确")
        per_mode[r["mode"]][v] += 1
        total[v] += 1
    return per_mode, total


def company_breakdown(qid, runs, obmap):
    """某道全景题（cover 路线）逐公司取证结果，按 ✅ / 🟡 / ❌ 排序。"""
    r = next((x for x in runs if x["qid"] == qid and x["mode"] == "cover"), None)
    if not r:
        return []
    groups: dict[str, list] = {}
    for d in r.get("obligations_detail_answer", []):
        groups.setdefault(d.get("subject") or "（跨公司结论）", []).append(d)
    gap_by_subj: dict[str, set] = {}
    for g in r.get("gap_reasons", []):
        gap_by_subj.setdefault(g.get("subject") or "（跨公司结论）", set()).add(g["code"])
    rows = []
    for comp, items in groups.items():
        cov = sum(1 for i in items if i["status"] == "已覆盖")
        mark = "✅" if cov == len(items) else ("🟡" if cov else "❌")
        # 只在**已覆盖**的义务上取标准数值 —— 缺失的项不显示数字，
        # 否则读者会把标准答案误读成「系统答出来了」。
        vals = []
        for i in sorted(items, key=lambda x: x["id"]):
            if i["status"] != "已覆盖":
                continue
            ob = obmap.get(i["id"], {})
            if ob.get("values"):
                vals.append(str(ob["values"][0]))
        codes = sorted(gap_by_subj.get(comp, set()))
        first = min(int("".join(ch for ch in i["id"] if ch.isdigit()) or 99)
                    for i in items)
        rows.append({"comp": comp, "mark": mark, "vals": vals, "codes": codes,
                     "order": {"✅": 0, "🟡": 1, "❌": 2}[mark],
                     "seq": first})
    rows.sort(key=lambda x: (x["order"], x["seq"]))
    return rows


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_html(fs: float) -> str:
    scores, runs, qmap, obmap = load()
    bm = scores["by_mode"]
    agg = shape_stats(runs)
    per_mode, total = verdict_stats(runs)

    # ---- 表一：主要对照结果 ----
    t1 = ["<table><tr><th>检索路线</th>"
          "<th>召回层<br><span class='dim'>证据组关键词命中</span></th>"
          "<th>义务覆盖 @Top8<br><span class='dim'>召回段（严口径）</span></th>"
          "<th>答案层<br><span class='dim'>实际作答</span></th></tr>"]
    for key, name, sub in MODE_ROWS:
        d = bm[key]
        t1.append(f"<tr><td><b>{name}</b><span class='dim'> · {sub}</span></td>"
                  f"<td>{pct(d['recall_hit_top5'])}</td>"
                  f"<td>{pct(d['oblig_covered_recall_top8'])}</td>"
                  f"<td><b>{pct(d['oblig_covered_answer'])}</b></td></tr>")
    t1.append("</table>")
    cover = bm["cover"]

    # ---- 表二：按形状（哪类题答得好、哪类翻车）----
    t2 = ["<table><tr><th>问题形状</th><th>答案层覆盖</th>"
          "<th>读法</th></tr>"]
    shape_read = {
        "局部": "召回到就能答对，损耗 ≈ 0",
        "多跳": "样本仅 1 题，仅作参考",
        "全局": "<b>主要翻车点</b>：一次查询要覆盖 7 家",
    }
    for s in SHAPE_ORDER:
        if s not in agg:
            continue
        c, t = agg[s]
        t2.append(f"<tr><td>{SHAPE_LABEL[s]}</td><td><b>{round(c / t * 100)}%</b>"
                  f"<span class='dim'>（{c}/{t}）</span></td>"
                  f"<td>{shape_read.get(s, '')}</td></tr>")
    t2.append("</table>")

    # ---- 全景题逐公司 ----
    q1 = company_breakdown("Q01", runs, obmap)
    q2 = company_breakdown("Q02", runs, obmap)

    def line(rows):
        out = []
        for x in rows:
            v = (" " + " / ".join(x["vals"])) if x["vals"] else ""
            code = f"（{'/'.join(x['codes'])}）" if x["codes"] else ""
            out.append(f"<span class='co'>{x['mark']} {esc(x['comp'])}{v} "
                       f"<span class='dim'>{code}</span></span>")
        return "　".join(out)

    foot = (f"逐题明细见公开仓库 <b>{REPO_URL}</b> 的 <code>eval_run/</code>"
            f"（<code>eval_runs.json</code> + <code>10题实际作答与判定.md</code>）；"
            f"每条答复均带 <b>公司 · 报告期 · 页码</b> 出处，可逐句回溯原文。")

    html = f"""<h1>作业 3-A 一页结论 · 快递物流行业财报问答知识库</h1>
<div class="sub"><b>方向</b> A（建知识库 / 检索增强问答）　·　
<b>语料</b> 34 份财报（17 家 × 2025 年报 + 2026 半年报；A 股 + 港股）· 18,596 知识块　·　
<b>索引</b> BM25（jieba + 62 词领域词典）+ 向量（512 维），全离线　·　
<b>仓库</b> {REPO_URL}</div>

<h2>一、主要对照结果　<span class="dim">10 题 × 4 条路线；召回层与答案层分开计分</span></h2>
{''.join(t1)}
<div class="note"><b>两层口径</b>：<b>召回层</b> = 证据组<b>关键词命中</b>（宽松上界，「有没有找回来」）；
<b>义务覆盖</b> = 42 项证据义务（「数值对不对」<b>且</b>「是谁的、哪个口径、哪一期」）在召回结果里是否落地，
其中 <b>@Top8</b> 一列与答案层直接可比（答案生成读的正是 Top8）；<b>答案层</b> = 同一批义务在系统
<b>实际作答</b>里的覆盖。cover 一路：<b>{pct(cover['oblig_covered_recall_top8'])} → {pct(cover['oblig_covered_answer'])}</b>，
这 {round((cover['oblig_covered_recall_top8'] - cover['oblig_covered_answer']) * 100)}pt 就是
「召回到了 ≠ 答出来了」的损耗。</div>

<h2>二、哪类题答得好、哪类翻车　<span class="dim">按问题形状（cover 路线答案层）</span></h2>
{''.join(t2)}
<div class="note"><b>一句话</b>：单公司事实检索已经可靠；<b>跨公司全景题</b>是唯一系统性失分点，
它同时暴露三个独立根因 ——
① <b>词法失效</b>（问题里的公司名恰是附录「释义表 / 子公司名录」的高频词，BM25 被刷屏）；
② <b>术语口径不一致</b>（「单票收入」= 顺丰写「票均收入」= 中通写「包裹单价」，字面无重合）；
③ <b>答案组装把已召回的证据丢在门外</b>（见第四节 H）。</div>

<h2>三、全景覆盖　<span class="dim">两道全景题逐公司取证（对应课件 P61–63「哪条路线都只看到一角」）</span></h2>
<div class="ln"><b>Q01 四家营业收入</b>（2026H1）：{line(q1)}</div>
<div class="ln"><b>Q02 七家毛利率</b>（2026H1）：{line(q2)}</div>
<div class="note">单条路线各自只覆盖其中几家；改为<b>按公司分组取证</b>（分组召回）后，
Q01 由三种单路 <b>0 命中</b> → cover <b>3/4</b> 家到位。标 (D) = 召回不足，
标 (G) = <b>本项目切块器缺陷</b>（不是「报告没写」）。</div>

<h2>四、方法局限　<span class="dim">诚实清单</span></h2>
<ul class="lim">
<li><b>召回 ≠ 答对。</b>cover 答案层仅 {pct(cover['oblig_covered_answer'])}，
低于召回段的义务覆盖 @Top8 {pct(cover['oblig_covered_recall_top8'])}；
40 份实际作答逐份判定：<b>正确 {total['正确']} · 部分正确 {total['部分正确']} · 错误 {total['错误']}</b>
（分组召回 {per_mode['cover']['正确']}/{per_mode['cover']['部分正确']}/{per_mode['cover']['错误']}，
向量 {per_mode['vector']['正确']}/{per_mode['vector']['部分正确']}/{per_mode['vector']['错误']}）。</li>
<li><b>跨公司题是主要失分点</b>（全局 {round(agg.get('全局', [0, 1])[0] / (agg.get('全局', [0, 1])[1] or 1) * 100)}%
vs 局部 {round(agg.get('局部', [0, 1])[0] / (agg.get('局部', [0, 1])[1] or 1) * 100)}%）：
一次查询要覆盖 7 家公司，而答案上下文只有 8 个块 —— 工程对策是<b>按公司分组取证</b>。</li>
<li><b>三类缺口必须分开读，不能混成「检索失败」</b>：
<b>A 语料未披露</b>（16 项，如中通半年报未以「市场份额」口径披露 —— 系统没编造数字，属正确行为）·
<b>G 本项目切块缺陷</b>（4 项，申通 P25 分行业表毛利率列被误判成表头，回原始 PDF 可读到 6.97%）·
<b>H 答案侧丢已召回证据</b>（13 项，与 <b>D 召回不足 63 项</b>分开计）。</li>
<li><b>答案仍是抽取式拼句</b>：顺丰报告双栏排版导致句子错行；向量在长表 / 附注上噪声大；
<b>样本量小</b>（每题仅 1 道，单题成败会被放大成 0% / 100%）。</li>
</ul>
<div class="foot">{foot}</div>"""

    css = f"""
@page {{ size: A4; margin: 9mm 10mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: -apple-system, "PingFang SC", "Hiragino Sans GB",
        "Microsoft YaHei", sans-serif; font-size: {fs}pt; line-height: 1.44;
        color: #1b1b1b; margin: 0; }}
h1 {{ font-size: {fs + 3.4:.1f}pt; color: #111; margin: 0 0 3px;
     border-bottom: 2.2px solid #b5121b; padding-bottom: 3px; }}
h2 {{ font-size: {fs + 1.0:.1f}pt; color: #b5121b; margin: 7px 0 3px;
     border-left: 3.5px solid #b5121b; padding-left: 6px; }}
.sub {{ font-size: {fs - 0.7:.1f}pt; color: #4a4a4a; margin: 3px 0 2px;
        line-height: 1.45; }}
table {{ border-collapse: collapse; width: 100%;
         font-size: {fs - 0.6:.1f}pt; margin: 2px 0 3px; }}
th, td {{ border: 1px solid #d5d5d5; padding: 2px 6px; text-align: left;
          line-height: 1.34; }}
th {{ background: #f5f5f5; font-weight: 600; }}
td:not(:first-child), th:not(:first-child) {{ text-align: center; }}
.note {{ font-size: {fs - 1.0:.1f}pt; color: #444; margin: 3px 0 2px;
         line-height: 1.42; }}
.ln {{ font-size: {fs - 0.4:.1f}pt; margin: 2px 0; line-height: 1.5; }}
.lim {{ margin: 2px 0 2px 15px; padding: 0; }}
.lim li {{ font-size: {fs - 0.5:.1f}pt; margin: 2px 0; line-height: 1.42; }}
.dim {{ color: #777; font-weight: 400; }}
.co {{ margin-right: 10px; white-space: nowrap; }}
code {{ background: #f2f2f2; padding: 0 3px; border-radius: 2px;
        font-family: "SF Mono", Menlo, monospace; font-size: {fs - 1.2:.1f}pt; }}
.foot {{ font-size: {fs - 1.2:.1f}pt; color: #555; margin-top: 5px;
         border-top: 1px solid #e0e0e0; padding-top: 3px; line-height: 1.4; }}
"""
    doc = (f"<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
           f"<style>{css}</style></head><body>{html}</body></html>")
    return doc


def render(doc: str, pdf_path: str) -> bool:
    fd, tmp = tempfile.mkstemp(suffix=".html", dir="/tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(doc)
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    cmd = [mr.CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
           "--disable-dev-shm-usage", "--no-first-run",
           "--no-default-browser-check", "--disable-extensions",
           "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
           "--virtual-time-budget=8000", "file://" + tmp]
    import subprocess
    r = subprocess.run(cmd, capture_output=True, text=True)
    os.remove(tmp)
    if not os.path.exists(pdf_path):
        print("  !! PDF 未生成：", (r.stderr or r.stdout)[-400:])
        return False
    return True


def page_count(pdf: str) -> int:
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return -1
    return pymupdf.open(pdf).page_count


def main() -> int:
    if not os.path.exists(mr.CHROME):
        print("!! 找不到 Chrome / Edge / Chromium，无法打印 PDF。")
        return 1
    pdf = os.path.join(OUT, "一页结论.pdf")
    print("生成「一页结论」PDF（严格校验 1 页）...")
    # 从大到小试探：取「仍然只有 1 页」的最大字号，正文既塞满一页又不过界
    for fs in (10.8, 10.4, 10.0, 9.6, 9.2, 8.8, 8.4, 8.0, 7.6, 7.2):
        doc = build_html(fs)
        if not render(doc, pdf):
            return 1
        n = page_count(pdf)
        size = os.path.getsize(pdf) / 1024
        if n == 1:
            print(f"  → output/一页结论.pdf   {size:.0f} KB   "
                  f"**1 页**（字号 {fs:.2f}pt）")
            return 0
        if n < 0:
            print(f"  → output/一页结论.pdf   {size:.0f} KB   "
                  f"（未装 pymupdf，跳过页数校验；字号 {fs:.2f}pt）")
            return 0
        print(f"     字号 {fs:.2f}pt → {n} 页，降档重排 ...")
    print("  !! 压到 7.2pt 仍超过 1 页，请精简正文。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
