#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
06_answer_audit.py —— 逐题答案判定书（对齐课程第三课 P61-63 / P90-92）

读：
  output/eval_runs.json       ← 05_eval.py 的实际运行结果（系统真实输出）
  code/eval_questions.json    ← 10 题标准答案与证据义务（只作打分尺子）
出：
  output/逐题实际答复存档.md    —— 系统实际输出原文（10 题 × 4 路线）
  output/逐题答案判定书.md      —— 逐题判定 + 理由 + 证据覆盖 + 页码证据
  output/作业3A_逐题答案判定书.pdf

判定规则（可复核，写在正文 §0）：
  正确     = 该题全部证据义务都在答复里被正确覆盖
  部分正确 = 覆盖 1 项以上但不满
  错误     = 一项都没覆盖
  「覆盖」= 标准答案的数值出现在答复中，且归属到正确的公司/口径/报告期
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output")
RUNS = os.path.join(OUT, "eval_runs.json")
QS = os.path.join(ROOT, "code", "eval_questions.json")

MODES = ["cover", "hybrid", "bm25", "vector"]
MODE_LABEL = {
    "cover": "分组召回 cover",
    "hybrid": "混合 RRF",
    "bm25": "BM25",
    "vector": "向量",
}
GAP_NAME = {
    "A": "语料未披露该口径",
    "B": "公司名漏识别",
    "C": "焦点词误杀",
    "D": "召回不足",
    "E": "口径不一致",
    "F": "数值无归宿",
    "G": "语料表格未抽取（本项目缺陷）",
    "H": "答案未取到已召回的证据（答案侧）",
}
MARK = {"已覆盖": "✅ 已覆盖", "部分": "⚠️ 部分", "缺": "❌ 缺"}
MARK_SHORT = {"正确": "✅", "部分正确": "🟡", "错误": "❌"}
MODE_LABEL_SHORT = {"cover": "分组召回", "hybrid": "混合 RRF", "bm25": "BM25", "vector": "向量"}


# ---------- 小工具 ----------
def esc(s: str) -> str:
    """markdown 表格/正文转义"""
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()


def page_list(s: str) -> str:
    """从 src 文本里抽 P 页码，去重保序"""
    seen: list[str] = []
    for p in re.findall(r"P(\d+)", s or ""):
        if p not in seen:
            seen.append(p)
    return "·".join("P" + p for p in seen) if seen else "—"


def verdict(cov: int, tot: int) -> str:
    if tot <= 0:
        return "—"
    if cov >= tot:
        return "正确"
    if cov == 0:
        return "错误"
    return "部分正确"


def vshort(cov: int, tot: int) -> str:
    v = verdict(cov, tot)
    return {"正确": "✅ 正确", "部分正确": "🟡 部分正确", "错误": "❌ 错误"}.get(v, v)


def std_values(qobj: dict) -> dict[str, str]:
    """义务 id -> 人类可读值（取 values[0]）"""
    m = {}
    for ob in qobj.get("obligations", []):
        vals = [v for v in ob.get("values", []) if v]
        m[ob["id"]] = vals[0] if vals else "—"
    return m


# ---------- 正文 ----------
def head(rows: list[dict], Q: dict[str, dict]) -> str:
    L = []
    L.append("# 作业 3-A · 逐题答案判定书")
    L.append("")
    L.append("> 10 道题 × 4 条检索路线 = **40 份系统实际作答**，逐份判定 正确 / 部分正确 / 错误，"
             "并逐项记录证据覆盖与出处（公司 · 报告期 · 页码）。")
    L.append("")
    L.append("## 0. 阅读须知：这份文档记录的是「系统实际输出」，不是标准答案")
    L.append("")
    L.append("课件第三课 P61–63、P90–92 的要求是**分开评价证据覆盖与答案表现**，"
             "并**保存真实答复、逐题给出判定理由**。本判定书即为此而做。")
    L.append("")
    L.append("### 0.1 数据从哪来")
    L.append("")
    L.append("- **系统实际作答**：`code/04_ask.py` 跑出的结果，由 `code/05_eval.py` 落盘到 "
             "`output/eval_runs.json`（40 条记录）。本判定书由 `code/06_answer_audit.py` 读该文件渲染，"
             "**不修改任何一个字**。")
    L.append("- **网页实测**：同一套检索引擎 + 同一套答案组装，"
             "页面截图见 `shots/`（9 张问答页）。")
    L.append("- **标准答案**（`code/eval_questions.json`）：**只当打分尺子用**，"
             "回答里的每一条都已在原始 PDF 上核对到页码。没有拿标准答案冒充系统作答。")
    L.append("")
    L.append("### 0.2 两层分开评（这两个数字完全不同，混读会得出相反结论）")
    L.append("")
    L.append("| 层 | 量的是什么 | 口径 |")
    L.append("|---|---|---|")
    L.append("| **证据覆盖层**（召回侧）| 这道题的证据义务里，有多少条**在召回结果里能找到**对应证据 | 宽松上界 |")
    L.append("| **答案表现层**（作答侧）| 系统**实际答复里**有多少条义务被正确作答（数值 + 归宿都要对） | 严格口径 |")
    L.append("")
    L.append("### 0.3 逐题判定规则")
    L.append("")
    L.append("| 判定 | 定义 |")
    L.append("|---|---|")
    L.append("| **正确** | 该题全部证据义务都在答复里被正确覆盖 |")
    L.append("| **部分正确** | 覆盖 1 项以上、但未覆盖全部 |")
    L.append("| **错误** | 一项都没覆盖 |")
    L.append("")
    L.append("> 「覆盖」= 标准答案的数值出现在答复中，**并且**归属到正确的公司 / 口径 / 报告期。"
             "只有数值没归宿（不知是谁的）算 ⚠️ 部分，不计入已覆盖。")
    L.append("")
    L.append("### 0.4 判定矩阵总览")
    L.append("")
    L.append("| 题（形状） | 义务 | " + " | ".join(MODE_LABEL_SHORT[m] for m in MODES) + " |")
    L.append("|" + "---|" * (2 + len(MODES)))
    cnt = {m: {"正确": 0, "部分正确": 0, "错误": 0} for m in MODES}
    for qid in sorted(Q, key=lambda x: int(re.sub(r"\D", "", x))):
        qobj = Q[qid]
        tot = len(qobj.get("obligations", []))
        cells = []
        for m in MODES:
            r = next((x for x in rows if x["qid"] == qid and x["mode"] == m), None)
            if not r:
                cells.append("—")
                continue
            cov = r.get("obligations_covered_answer", 0)
            v = verdict(cov, tot)
            cnt[m][v] = cnt[m].get(v, 0) + 1
            cells.append(f"{MARK_SHORT.get(v,'')} {cov}/{tot}")
        L.append(f"| **{qid}**（{qobj.get('shape','')}） | {tot} | " + " | ".join(cells) + " |")
    L.append("| **合计** | — | " + " | ".join(
        f"✅{cnt[m]['正确']} 🟡{cnt[m]['部分正确']} ❌{cnt[m]['错误']}" for m in MODES) + " |")
    L.append("")
    L.append("> 图例：**✅ 正确 · 🟡 部分正确 · ❌ 错误**；分子 = 已被正确作答的义务数，"
             "分母 = 该题义务总数。合计行为该路线下 正确 / 部分正确 / 错误 的作答份数。")
    L.append("")
    tot_ok = sum(cnt[m].get("正确", 0) for m in MODES)
    tot_par = sum(cnt[m].get("部分正确", 0) for m in MODES)
    tot_err = sum(cnt[m].get("错误", 0) for m in MODES)
    L.append(f"> 40 份作答中：**正确 {tot_ok} 份 · 部分正确 {tot_par} 份 · 错误 {tot_err} 份**。"
             "局部题（单公司单指标）普遍答得准；缺口集中在跨公司全景题 —— "
             "四条路线往往各自只覆盖其中几家，都只看到一角（课件 P61–63）。"
             "完整缺口清单见 §3，两类全景题的逐公司取证见 §2。")
    L.append("")
    return "\n".join(L)


def per_question(rows: list[dict], Q: dict[str, dict]) -> str:
    L = []
    L.append("## 1. 逐题详录")
    L.append("")
    for qid in sorted(Q, key=lambda x: int(re.sub(r"\D", "", x))):
        qobj = Q[qid]
        tot = len(qobj.get("obligations", []))
        vals = std_values(qobj)
        L.append(f"### {qid}　{qobj['q']}")
        L.append("")
        L.append(f"- **形状**：{qobj.get('shape','')}　**类型**：{qobj.get('type','')}")
        L.append(f"- **证据义务**：{tot} 项")
        L.append("")
        L.append("**标准答案（打分尺子，非系统作答）**")
        L.append("")
        L.append("> " + esc(qobj.get("standard_answer", "")))
        L.append("")
        # 标准答案出处
        srcs = []
        for ob in qobj.get("obligations", []):
            srcs.append(f"　- `{ob['id']}` {ob.get('subject','')}·{ob.get('item','')}　"
                        f"值 {vals.get(ob['id'],'—')}　出处 {page_list(ob.get('src',''))}")
        if srcs:
            L.append("**逐项义务与出处**")
            L.append("")
            L.extend(srcs)
            L.append("")
        # 四路线
        for m in MODES:
            r = next((x for x in rows if x["qid"] == qid and x["mode"] == m), None)
            if not r:
                continue
            cov = r.get("obligations_covered_answer", 0)
            v = verdict(cov, tot)
            L.append(f"#### {qid} · {MODE_LABEL[m]}　→　**{vshort(cov, tot)}**（答案层覆盖 {cov}/{tot}）")
            L.append("")
            L.append("**系统实际答复（原文）**")
            L.append("")
            L.append("> " + esc(r.get("answer", "")))
            L.append("")
            bl = r.get("bullets") or []
            if bl:
                L.append(f"系统同时给出 {len(bl)} 条带出处的证据（网页上以卡片形式展示）：")
                L.append("")
                for i, b in enumerate(sorted(bl, key=lambda x: -(x.get("score") or 0)), 1):
                    txt = esc(b.get("text", ""))[:150]
                    L.append(f"{i}. `{b.get('cite','')}` —— {txt}")
                L.append("")
            # 判定理由
            det = r.get("obligations_detail_answer") or []
            gaps = {g["id"]: g for g in (r.get("gap_reasons") or [])}
            ok = [d for d in det if d["status"] == "已覆盖"]
            par = [d for d in det if d["status"] == "部分"]
            mis = [d for d in det if d["status"] == "缺"]
            L.append("**判定理由**")
            L.append("")
            seg = [f"本题 {tot} 项义务，答复覆盖 **{cov}** 项，判定 **{v}**。"]
            if ok:
                seg.append("已答对：" + "、".join(
                    f"{d.get('subject','')} {d.get('item','')} = {vals.get(d['id'],'—')}" for d in ok) + "。")
            if par:
                seg.append("数值出现但**未归属**（不知是谁的数）：" + "、".join(
                    f"{d.get('subject','')} {d.get('item','')}（值 {vals.get(d['id'],'—')}）" for d in par) + "。")
            if mis:
                seg.append("**缺失**：" + "、".join(
                    f"{d.get('subject','')} {d.get('item','')}（应作答 {vals.get(d['id'],'—')}）" for d in mis) + "。")
                codes = [gaps[d["id"]]["code"] for d in mis if d["id"] in gaps]
                if codes:
                    from collections import Counter
                    c = Counter(codes)
                    seg.append("缺失项归因：" + "、".join(
                        f"{k}（{GAP_NAME.get(k,'')}）×{n}" for k, n in c.most_common()) + "。")
            L.append(" ".join(seg))
            L.append("")
            # 覆盖明细表
            L.append("**证据覆盖逐项**")
            L.append("")
            L.append("| 义务 | 应作答项 | 标准数值 | 系统状态 | 出处（页码） |")
            L.append("|---|---|---|---|---|")
            for d in det:
                L.append(f"| {d['id']} | {d.get('subject','')}·{d.get('item','')} | "
                         f"{vals.get(d['id'],'—')} | {MARK.get(d['status'], d['status'])} | "
                         f"{page_list(d.get('src',''))} |")
            L.append("")
            # 引用证据（页码）
            L.append("**答复引用的证据（公司 · 报告期 · 章节 · 页码）**")
            L.append("")
            L.append("| # | 公司 | 报告期 | 章节 | 页码 | 块 ID |")
            L.append("|---|---|---|---|---|---|")
            for c in (r.get("citations") or []):
                L.append(f"| {c.get('n')} | {c.get('company')} | {c.get('kind')} | "
                         f"{c.get('section')} | P{c.get('page')} | `{c.get('chunk_id')}` |")
            L.append("")
            L.append("---")
            L.append("")
    return "\n".join(L)


PANORAMA = ["Q01", "Q02"]


def panorama(rows: list[dict], Q: dict[str, dict]) -> str:
    L = []
    L.append("## 2. 两道全景题：逐公司列出「已找到」与「缺失」的证据")
    L.append("")
    L.append("课件 P61–63 指出：跨公司全景题里，**每条路线都只覆盖其中几家，都只看到一角**。"
             "所以这里把 Q01、Q02 的每一家公司单独摊开，明确哪家找到了、哪家没找到、为什么没找到。")
    L.append("")
    for qid in PANORAMA:
        qobj = Q[qid]
        tot = len(qobj.get("obligations", []))
        vals = std_values(qobj)
        L.append(f"### {qid}　{qobj['q']}")
        L.append("")
        L.append(f"共 {tot} 项义务（含结论项）。下表按义务逐项列出四条路线的应答情况：")
        L.append("")
        L.append("| 义务 | 公司 · 指标 | 标准数值 | 源出处 | cover | hybrid | bm25 | vector |")
        L.append("|---|---|---|---|---|---|---|---|")
        for ob in qobj.get("obligations", []):
            oid = ob["id"]
            cells = []
            for m in MODES:
                r = next((x for x in rows if x["qid"] == qid and x["mode"] == m), None)
                if not r:
                    cells.append("—")
                    continue
                d = next((x for x in (r.get("obligations_detail_answer") or []) if x["id"] == oid), None)
                cells.append(MARK.get(d["status"], "—") if d else "—")
            L.append(f"| {oid} | {ob.get('subject','')}·{ob.get('item','')} | {vals.get(oid,'—')} | "
                     f"{page_list(ob.get('src',''))} | " + " | ".join(cells) + " |")
        L.append("")
        # 逐公司小结
        comps: dict[str, list] = {}
        for ob in qobj.get("obligations", []):
            comps.setdefault(ob.get("subject", "—"), []).append(ob)
        L.append(f"**逐公司小结（以最优路线 cover 为准）**")
        L.append("")
        r_cover = next((x for x in rows if x["qid"] == qid and x["mode"] == "cover"), None)
        det = {d["id"]: d for d in ((r_cover or {}).get("obligations_detail_answer") or [])}
        gaps = {g["id"]: g for g in ((r_cover or {}).get("gap_reasons") or [])}
        for comp, obs in comps.items():
            found = [o for o in obs if det.get(o["id"], {}).get("status") == "已覆盖"]
            miss = [o for o in obs if det.get(o["id"], {}).get("status") != "已覆盖"]
            if not miss:
                L.append(f"- **{comp}**：✅ 已找到 —— " + "、".join(
                    f"{o['item']} = {vals.get(o['id'],'—')}" for o in obs))
            elif not found:
                why = "；".join(f"{gaps[o['id']]['code']}（{GAP_NAME.get(gaps[o['id']]['code'],'')}）"
                                if o["id"] in gaps else "未分类" for o in miss)
                L.append(f"- **{comp}**：❌ 缺失 —— 应作答 " + "、".join(
                    f"{o['item']}（{vals.get(o['id'],'—')}）" for o in miss) + f"｜归因：{why}")
            else:
                L.append(f"- **{comp}**：🟡 部分 —— 已找到 " + "、".join(
                    f"{o['item']} = {vals.get(o['id'],'—')}" for o in found) + "；缺失 " + "、".join(
                    f"{o['item']}（{vals.get(o['id'],'—')}）" for o in miss))
        L.append("")
    return "\n".join(L)


def misses(rows: list[dict], Q: dict[str, dict]) -> str:
    L = []
    L.append("## 3. 如实记录的错漏（不做美化）")
    L.append("")
    L.append("课件 P92：**缺数据的明说，不许编**。以下为系统实际答不出 / 答不全的条目，"
             "按题汇总 4 条路线的缺失次数。")
    L.append("")
    L.append("| 题 | 义务 | 应作答项 | 标准数值 | 缺失路线数 | 主要归因 |")
    L.append("|---|---|---|---|---|---|")
    from collections import Counter
    total_miss = 0
    for qid in sorted(Q, key=lambda x: int(re.sub(r"\D", "", x))):
        qobj = Q[qid]
        vals = std_values(qobj)
        for ob in qobj.get("obligations", []):
            oid = ob["id"]
            miss_modes, codes = [], []
            for m in MODES:
                r = next((x for x in rows if x["qid"] == qid and x["mode"] == m), None)
                if not r:
                    continue
                d = next((x for x in (r.get("obligations_detail_answer") or []) if x["id"] == oid), None)
                if d and d["status"] != "已覆盖":
                    miss_modes.append(m)
                    for g in (r.get("gap_reasons") or []):
                        if g["id"] == oid:
                            codes.append(g["code"])
            if miss_modes:
                total_miss += 1
                c = Counter(codes)
                top = "、".join(f"{k}（{GAP_NAME.get(k,'')}）" for k, _ in c.most_common(2)) or "—"
                L.append(f"| {qid} | {oid} | {ob.get('subject','')}·{ob.get('item','')} | "
                         f"{vals.get(oid,'—')} | {len(miss_modes)}/4 | {top} |")
    L.append("")
    L.append(f"> 42 项义务中，**{total_miss} 项**在至少一条路线上未被作答；"
             "其余为四条路线全部答对。")
    L.append("")
    return "\n".join(L)


def archive(rows: list[dict], Q: dict[str, dict]) -> str:
    L = []
    L.append("# 附录 · 10 题实际答复存档（系统原文）")
    L.append("")
    L.append("> 全部 40 份系统实际作答的**原文存档**，供核对。判定与理由见正文《逐题答案判定书》。")
    L.append("> 机器可读版本：`output/eval_runs.json`。")
    L.append("")
    for qid in sorted(Q, key=lambda x: int(re.sub(r"\D", "", x))):
        qobj = Q[qid]
        L.append(f"## {qid}　{qobj['q']}")
        L.append("")
        L.append(f"- 形状：{qobj.get('shape','')}　类型：{qobj.get('type','')}　"
                 f"义务数：{len(qobj.get('obligations', []))}")
        L.append("")
        for m in MODES:
            r = next((x for x in rows if x["qid"] == qid and x["mode"] == m), None)
            if not r:
                continue
            L.append(f"### {qid} / {MODE_LABEL[m]}")
            L.append("")
            L.append("**答复摘要**")
            L.append("")
            L.append("> " + esc(r.get("answer", "")))
            L.append("")
            bl = r.get("bullets") or []
            if bl:
                L.append(f"**证据条目（{len(bl)} 条，均带出处）**")
                L.append("")
                for i, b in enumerate(sorted(bl, key=lambda x: -(x.get("score") or 0)), 1):
                    L.append(f"{i}. `{b.get('cite','')}`　{esc(b.get('text',''))[:180]}")
                L.append("")
    return "\n".join(L)


def main() -> int:
    if not os.path.exists(RUNS):
        print("!! 找不到 output/eval_runs.json —— 先跑 code/05_eval.py")
        return 1
    rows = json.load(open(RUNS, encoding="utf-8"))
    qlist = json.load(open(QS, encoding="utf-8"))
    Q = {q["qid"]: q for q in qlist}

    body = "\n".join([
        head(rows, Q),
        per_question(rows, Q),
        panorama(rows, Q),
        misses(rows, Q),
    ])
    open(os.path.join(OUT, "逐题答案判定书.md"), "w", encoding="utf-8").write(body)
    arc = archive(rows, Q)
    open(os.path.join(OUT, "逐题实际答复存档.md"), "w", encoding="utf-8").write(arc)

    # 终端打印判定矩阵，便于核对
    print("逐题判定矩阵（答案层：正确 / 部分正确 / 错误）")
    print("题    " + "  ".join(f"{MODE_LABEL[m]:>14s}" for m in MODES))
    for qid in sorted(Q, key=lambda x: int(re.sub(r"\D", "", x))):
        tot = len(Q[qid].get("obligations", []))
        cells = []
        for m in MODES:
            r = next((x for x in rows if x["qid"] == qid and x["mode"] == m), None)
            cov = r.get("obligations_covered_answer", 0) if r else 0
            cells.append(f"{verdict(cov, tot)} {cov}/{tot}".rjust(14))
        print(f"{qid}  " + "  ".join(cells))

    # 尝试合成 PDF（复用 make_report 的 md→html→Chrome 链路）
    if "--no-pdf" in sys.argv:
        print("(--no-pdf：跳过 PDF 合成，只出 Markdown)")
        return 0
    sys.path.insert(0, os.path.join(ROOT, "code"))
    try:
        import make_report as mr
        if not os.path.exists(mr.CHROME):
            print("(未找到 Chrome，跳过 PDF 合成；Markdown 已生成)")
            return 0
        pdf = os.path.join(OUT, "作业3A_逐题答案判定书.pdf")
        html = (mr.md2html(os.path.join(OUT, "逐题答案判定书.md"), None)
                + '<div class="brk">'
                + mr.md2html(os.path.join(OUT, "逐题实际答复存档.md"), None) + "</div>")
        if mr.html_to_pdf(html, pdf):
            print(f"  → output/作业3A_逐题答案判定书.pdf   "
                  f"{os.path.getsize(pdf) / 1024:.0f} KB")
    except Exception as e:  # noqa: BLE001
        print("(PDF 合成失败：%s；Markdown 已生成)" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
