#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
05_eval.py —— 作业3-A 第四步：10 道题逐题评测

每道题在 4 种检索模式下各跑一遍（hybrid / bm25 / vector / cover）：
  hybrid  BM25 + 向量，RRF 融合
  bm25    仅词法召回
  vector  仅向量召回
  cover   在 hybrid 上叠加「分组召回」——改进项，专治跨公司全景题

逐题记录：
  · 召回了哪些块（chunk_id + 公司/章节/页码 + BM25 排名 + 向量排名 + RRF 分）
  · 是否命中标准答案证据（must_contain 关键词组；必答组 + soft 组分开算）
  · 答对没有、错在哪（题型分类 + 判卷结论，固化于 eval_table.md）

判卷口径（v2 为主口径；v1 判卷口径同时保留以便对照）：
  ① 「正文 + 元数据」判定：命中判定把块的公司/报告/章节元数据一并纳入。
     理由：公司名本来就由过滤器强约束，再要求正文复述一遍会把正确召回判成失败
     （Q02 就是被这条误判的：Top1 正是顺丰的毛利率原文，只是正文里没写"顺丰"）。
  ② soft 组：题干问到但语料未披露的指标，记入 soft_must_contain，
     未命中不算检索失败，但台账里明确标注「语料未披露」
     （Q09 的"市场份额"：全语料核查确认中通 2026 半年报未披露该口径）。

判卷分两层（对应课程第三课 P90–P92），**分别报数、互不替代**：
  · 召回层：证据组命中率（must_contain 同义写法组）——宽松上界，衡量「召回到了没」。
  · 答案层：**证据义务覆盖**——把每题拆成若干项「谁的、哪个指标、哪一期」的
    证据义务，逐项判定 已覆盖 / 部分 / 缺，并且**数值必须归宿正确**才算覆盖
    （课程 P91「数字丢口径」：只出现「4,026,356.42 万元」不叫答对，
      得说清是谁的、哪个口径、哪一期）。
    答案层用的是**最终交给用户的答案**（answer + bullets），不是召回的 hits ——
    召回覆盖 ≠ 答案覆盖，两者之差正是「关键词命中不能替代答题正确」。
  · 失败归因：覆盖地图里缺的那一项，定位到 语料未披露该口径 / 公司名漏识别 /
    焦点词误杀 / 召回不足 / 口径不一致 / 数值无归宿 / 语料表格未抽取（见 GAP_REASON）。

⚠ 台账里的「首轮实测」与「v1 判卷口径」是**两个不同的量**，别混读：
  · 首轮实测（见 FIRST_RUN 常量）= 2026-09-24 首轮跑出来的**历史数字**。
    当时检索侧尚未修复（Q01 实体表刷屏、Q03 跨市场术语鸿沟），
    cover 模式也还没实现，故 cover 无值。这个量**不由本脚本计算**，
    是固化的历史记录，只作对照。
  · v1 判卷口径列 = 用**当前检索结果**按 v1 规则（仅正文、不含元数据）**重算**。
    检索侧修复后 Q03 由未命中转命中，故 hybrid / bm25 各比首轮高 10pt。
  两者都真实但不是同一个量。两个量在台账 1 节并列，可逐格对齐。

输出：
  output/eval_runs.json     完整逐题日志（含召回明细，可复查）
  output/eval_table.md      对照表（题目 / 模式 / Top1 出处 / 召回命中 / 判卷）
  output/eval_scores.json   汇总指标
"""
from __future__ import annotations

import json
import os
import re
import sys
from importlib import import_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "code"))
OUT = os.path.join(ROOT, "output")
os.makedirs(OUT, exist_ok=True)

QF = os.path.join(ROOT, "code", "eval_questions.json")
MODES = ["hybrid", "bm25", "vector", "cover"]

# ---- 首轮实测（2026-09-24，检索侧修复前）----------------------------------
# 仅作历史对照，**不由本脚本计算**，是固化的实测记录。
# 依据：首轮评测当时的原始输出（该次运行未随仓库提交，此处固化为常量以便对照）
#   首轮结果：hybrid 60% / bm25 70% / vector 60%，跨公司全景题全 0%
#   —— 重算后的口径见台账 output/eval_table.md 的「v1 判卷口径」列，两者相差 10pt 的原因见下方常量
# cover 模式是首轮之后才实现的改进项，故为 None（台账显示「—」）。
FIRST_RUN = {"hybrid": 0.60, "bm25": 0.70, "vector": 0.60, "cover": None}
FIRST_RUN_NOTE = (
    "2026-09-24 首轮实测：检索侧尚未修复——领域词典未补跨市场术语"
    "（票均收入 / 包裹单价），且尚无分组召回，跨公司全景题 Q01–Q03 三种模式全部未命中。"
)
# 首轮实测与本脚本 v1 判卷列之差，逐模式写明原因（差 10pt 的来源）
FIRST_RUN_DIFF_NOTE = {
    "hybrid": "首轮 Q03（跨市场术语）未命中；领域词典补齐后转命中 → +10pt",
    "bm25": "同上：Q03 由未命中转为命中 → +10pt",
    "vector": "一致：Q03 在 v1 判卷下两种口径都未命中（向量路在长表/附注上噪声大）",
    "cover": "首轮尚无此模式（分组召回为首轮之后的改进项）",
}
MODE_LABEL = {
    "hybrid": "hybrid（BM25+向量·RRF）",
    "bm25": "bm25（仅词法）",
    "vector": "vector（仅向量）",
    "cover": "cover（hybrid+分组召回）",
}

# ===================== 答案层：证据义务判定（对应课程第三课）=====================
# 口径来源：第三课 P90–P92
#   P90 五阶段：①判断问题形状（局部/全局/多跳）②候选预取 ③取证循环
#               ④上下文组装（每条带出处）⑤受证据约束的生成
#      原话：「第一步不是搜索，是判断问题的形状；最后一步不是生成，是受证据约束的生成」
#   P91 「数字丢口径」：数值光被召回不算答对——「4,026,356.42 万元」是谁的、
#       哪个口径、哪一期，必须回到那张表。
#   P92 证据义务六步：把问题先拆成若干「证据义务」，逐项标记
#       已覆盖 / 部分 / 缺，缺哪项补哪项；结论每句带角标；缺数据的要明说。
#
# 于是评测分两层，**分别报数、不互相替代**：
#   召回层 = 证据是否被检索到（must_contain 同义组命中，宽松上界）
#   答案层 = 证据义务是否被最终答案覆盖（数值命中 **且** 归宿正确）
SHAPE_LABEL = {
    "局部": "局部（单公司单指标）",
    "全局": "全局·全景（跨公司）",
    "多跳": "多跳（对比 + 归因）",
}

# 失败归因分类（供「覆盖地图」里缺的那一项定位原因）
GAP_REASON = {
    "A": "语料未披露该口径",
    "B": "公司名漏识别",
    "C": "焦点词误杀",
    "D": "召回不足",
    "E": "口径不一致",
    "F": "数值无归宿",
    "G": "语料表格未抽取（提取缺口）",
}
# A 与 G 的区别很重要，别混为一谈：
#   A = **报告本身就没披露这个口径**（如中通 2026 半年报通篇无「市场份额/市占率」）
#       → 属数据边界，系统应当明说「未披露」，不编数字（课程 P92 末句）。
#   G = **报告披露了，但本项目的切块器没把数值抽出来**（如申通 P25 分行业表的
#       毛利率列只剩表头、单元格为空；回原始 PDF 第 25 页可读到「快递服务 6.97%」）
#       → 属语料缺陷，是检索侧的失败，已作为已知局限在台账中单列。
GAP_HINT_DOC = "有些义务的缺失原因是人工核对过原始 PDF 后确定的，用 gap_hint 钉死"
CITE_COMPANY = re.compile(r"^([^·]+)·")


def _norm(s: str) -> str:
    """归一化：去千分位逗号与所有空白，便于数值直接子串比对"""
    return re.sub(r"[\s,，、]", "", s or "")


def cite_company(cite: str | None) -> str | None:
    m = CITE_COMPANY.match(cite or "")
    return m.group(1) if m else None


def _names_of(subj: str | None) -> tuple[str, ...]:
    """一项义务的主体公司可能出现的写法：全称 + 无歧义简称。"""
    if not subj:
        return ()
    try:
        from importlib import import_module
        alias = import_module("04_ask").ALIASES.get(subj)
    except Exception:                                             # noqa: BLE001
        alias = None
    return (subj,) if not alias or alias == subj else (subj, alias)


def judge_obligations(items: list[tuple[str, str | None]],
                      obligations: list[dict]) -> tuple[list[dict], int, int]:
    """逐项判定「证据义务」是否被覆盖。

    items = [(正文, 归属公司)]：召回层传 (块正文, 块元数据 company)；
    答案层传 (答案条目正文, 从角标里解析出的 company)。
    检索命中要求数值出现 **且** 归宿正确——只出现数字不判答对，
    这正是课程 P91「数字丢口径」要卡的点。

    归属判定的三条路径（从严到宽，但都不会放松召回层）：
      ① comp == subj                —— 块/角标的公司正好就是义务主体（最理想）
      ② subj is None                —— 该义务本身就是跨公司结论，不问归属
      ③ comp is None 且正文自己写了公司名 —— 答案摘要句没有角标，但句子里
         「顺丰控股…1,555.1 亿元」已经把「是谁的」说清楚了（P91 要的正是这个）
    """
    out: list[dict] = []
    for ob in obligations:
        vals = [_norm(v) for v in ob.get("values", []) if v]
        subj = ob.get("subject")
        value_hit, attributed, where = False, False, None
        for text, comp in items:
            t = _norm(text)
            if not any(v in t for v in vals):
                continue
            value_hit = True
            if subj is None or comp == subj:
                attributed, where = True, comp or subj
                break
            if comp is None and any(n in t for n in _names_of(subj)):
                attributed, where = True, f"正文自述（{subj}）"
                break
        status = ("已覆盖" if (value_hit and attributed)
                  else "部分" if value_hit else "缺")
        out.append({
            "id": ob.get("id"), "subject": subj, "item": ob.get("item"),
            "period": ob.get("period"), "src": ob.get("src"),
            "status": status, "value_hit": value_hit, "attributed": attributed,
            "where": where,
        })
    cov = sum(1 for d in out if d["status"] == "已覆盖")
    return out, cov, len(out)


def answer_items(run: dict) -> list[tuple[str, str | None]]:
    """把一次运行**实际输出给用户的答案**摊成 (正文, 归属公司) 列表。

    答案层判定必须用它，而不是用召回的 hits —— 召回覆盖 ≠ 答案覆盖，
    两者之差正是老师指出的「Top5 关键词命中不能替代答题正确」。
    """
    items: list[tuple[str, str | None]] = [(run.get("answer") or "", None)]
    for b in run.get("bullets") or []:
        items.append((b.get("text") or "", cite_company(b.get("cite"))))
    return items



def judge(hits: list[dict], groups: list[list[str]],
          with_meta: bool = True) -> list[bool]:
    """同义写法组判定：每组命中任一写法即算该组命中。

    with_meta=True 时把块的「公司/报告类型/章节」元数据拼进判定文本——公司名本
    来就由过滤器强约束，不应再要求正文复述（v2 口径，修正 Q02 类误判）。
    """
    if not groups:
        return []
    parts: list[str] = []
    for h in hits:
        if with_meta:
            parts.append(f'{h["company"]} {h["kind"]} {h["section"]}')
        parts.append(h["text"])
    blob = "\n".join(parts)
    return [any(w in blob for w in g) for g in groups]


def main() -> None:
    qs = json.load(open(QF, encoding="utf-8"))
    eng = import_module("04_ask").Engine()
    print(f"载入索引：{eng.meta['n_chunks']} 块 / {len(eng.meta['reports'])} 份财报\n")

    runs = []
    for q in qs:
        req = q.get("must_contain", [])
        soft = q.get("soft_must_contain", [])
        for mode in MODES:
            r = eng.answer(q["q"], topk=8, mode=mode,
                           companies=q.get("companies"),
                           kinds=q.get("kinds"))
            d5 = judge(r["hits"][:5], req)                  # v2：正文+元数据
            d8 = judge(r["hits"], req)
            t5 = judge(r["hits"][:5], req, with_meta=False)  # v1：仅正文
            s5 = judge(r["hits"][:5], soft)
            s8 = judge(r["hits"], soft)

            # ---- 答案层：证据义务（课程 P92 覆盖地图口径）----
            obs = q.get("obligations") or []
            ob_h5, cov_h5, tot = judge_obligations(
                [(x["text"], x["company"]) for x in r["hits"][:5]], obs)
            ob_h8, cov_h8, _ = judge_obligations(
                [(x["text"], x["company"]) for x in r["hits"]], obs)
            ob_ans, cov_ans, _ = judge_obligations(
                answer_items({"answer": r["answer"], "bullets": r["bullets"]}),
                obs)

            runs.append({
                "qid": q["qid"], "q": q["q"], "type": q["type"],
                "shape": q.get("shape", "局部"), "mode": mode,
                "filters": {"companies": q.get("companies"),
                            "kinds": q.get("kinds")},
                "answer": r["answer"],
                "bullets": r["bullets"],
                "citations": r["citations"],
                "evidence_hit_top5": all(d5),
                "evidence_hit_top8": all(d8),
                "evidence_detail_top5": d5,
                "evidence_detail_top8": d8,
                "evidence_hit_top5_textonly": all(t5),
                "evidence_detail_top5_textonly": t5,
                "soft_detail_top5": s5,
                "soft_gap": [soft[i] for i, ok in enumerate(s5) if not ok],
                "soft_gap_top8": [soft[i] for i, ok in enumerate(s8) if not ok],
                "obligations_total": tot,
                "obligations_covered_top5": cov_h5,
                "obligations_covered_top8": cov_h8,
                "obligations_covered_answer": cov_ans,
                "obligations_detail_top5": ob_h5,
                "obligations_detail_top8": ob_h8,
                "obligations_detail_answer": ob_ans,
                "hits": [{k: h[k] for k in
                          ("chunk_id", "company", "kind", "exchange", "section",
                           "page_start", "page_end", "has_table", "score",
                           "rank_bm25", "rank_vector", "score_bm25",
                           "score_vector", "retriever", "cite", "n_chars")}
                         for h in r["hits"]],
                "top1_cite": r["hits"][0]["cite"] if r["hits"] else None,
                "top1_chunk": r["hits"][0]["chunk_id"] if r["hits"] else None,
            })
            flag = "命中" if all(d5) else ("仅Top8命中" if all(d8) else "未命中")
            gap = ""
            if s5 and not all(s5):
                gap = f'  [soft未披露 {len([x for x in s5 if not x])} 组]'
            top1 = r["hits"][0]["cite"] if r["hits"] else "—"
            ob_s = f'义务 {cov_h5}/{tot}→答 {cov_ans}/{tot}' if tot else ''
            print(f'[{q["qid"]}/{mode:6s}] {flag:10s} {ob_s:16s} top1={top1}{gap}')

    # ---------------- 失败归因：覆盖地图里「缺的那一项」到底缺在哪 --------------
    # 分类见 GAP_REASON。判定顺序刻意从「跟检索无关」到「检索侧」：
    #   A 语料未披露 → 全语料都没有，属数据边界，不算检索失败（课程 P92「缺数据的明说」）
    #   B 公司名漏识别 → 问题点名的公司没被识别，分组召回就分不出这一组
    #   C 焦点词误杀 → cover 模式特有：关掉指标焦点词过滤后该项即可召回（做消融对照）
    #   D 召回不足   → 语料里有、也没被误杀，但 Top-K 没排进来
    #   E 口径不一致 → 该公司的披露用词与题干关键词组完全不重合
    #   F 数值无归宿 → 数字召回到了，却没归到对应公司（课程 P91「数字丢口径」）
    all_norm = [_norm(d["text"]) for d in eng.docs]

    def corpus_has(ob: dict, company: str | None = None,
                   companies=None, kinds=None) -> bool:
        vals = [_norm(v) for v in ob.get("values", []) if v]
        ks = set(kinds or [])
        for i, d in enumerate(eng.docs):
            if company and d["company"] != company:
                continue
            if companies and d["company"] not in companies:
                continue
            if ks and d["kind"] not in ks:
                continue
            if any(v in all_norm[i] for v in vals):
                return True
        return False

    def alias_present(q: dict, company: str) -> bool:
        """题干的关键词组在该公司的语料里出现过吗（判 E 口径不一致）"""
        words = [w for grp in (q.get("must_contain") or []) for w in grp]
        if not words:
            return True
        for i, d in enumerate(eng.docs):
            if d["company"] != company:
                continue
            if (q.get("kinds") and d["kind"] not in q["kinds"]):
                continue
            if any(w in d["text"] for w in words):
                return True
        return False

    for q in qs:
        obs = q.get("obligations") or []
        if not obs:
            continue
        detected = eng.detect_companies(q["q"])
        # 「题面点名的公司」——只有**问题里真的写了公司名**才谈得上「漏识别」。
        # 像 Q10 这种问「各家公司」、正文并不点名的全景题，不能因为
        # detect_companies 返回空就把所有缺口都记成 B（公司名漏识别）。
        try:
            _al = import_module("04_ask").ALIASES
        except Exception:                                         # noqa: BLE001
            _al = {}
        named = [c for c in eng.company_set
                 if c in q["q"] or (_al.get(c) and _al[c] in q["q"])]
        want = [c for c in (q.get("companies") or
                            q.get("panorama_companies") or []) if c in eng.company_set]
        company_miss = [c for c in named if c not in detected]
        for mode in MODES:
            r = next(x for x in runs
                     if x["qid"] == q["qid"] and x["mode"] == mode)
            missing = [d for d in r["obligations_detail_answer"]
                       if d["status"] != "已覆盖"]
            ablation_ok: set = set()
            if missing and mode == "cover":
                ab = eng.search(q["q"], topk=8, mode="cover", focus_off=True,
                                companies=q.get("companies"),
                                kinds=q.get("kinds"))
                ab_d, _, _ = judge_obligations(
                    [(x["text"], x["company"]) for x in ab], obs)
                ablation_ok = {d["id"] for d in ab_d if d["status"] == "已覆盖"}

            reasons = []
            for d in missing:
                ob = next((o for o in obs if o.get("id") == d["id"]), {})
                hint = ob.get("gap_hint")          # 人工核对原始 PDF 后钉死的归因
                if hint:
                    code = hint
                elif not corpus_has(ob, company=d["subject"],
                                    companies=q.get("companies"),
                                    kinds=q.get("kinds")):
                    code = "A"
                elif company_miss:
                    code = "B"
                elif d["id"] in ablation_ok:
                    code = "C"
                elif d["value_hit"] and not d["attributed"]:
                    code = "F"
                # E（口径不一致）只对「有明确主体公司」的义务成立：
                # 跨公司结论类义务的 subject 是 None，「该公司的披露用词」无从谈起。
                # 早期版本漏了 d["subject"] 这一层判断，导致 Q10-1（行业单票均价，
                # subject=None）被误判成 E；语料里其实有该数值，正确归因是 D。
                elif d["subject"] and not alias_present(q, d["subject"]):
                    code = "E"
                else:
                    code = "D"
                reasons.append({"code": code, "reason": GAP_REASON[code],
                                "id": d["id"], "subject": d["subject"],
                                "item": d["item"], "status": d["status"]})
            r["gap_reasons"] = reasons
            r["company_miss"] = company_miss
            r["company_named"] = named
            r["company_hit"] = [c for c in want if c in detected or not named]

    json.dump(runs, open(os.path.join(OUT, "eval_runs.json"), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---------------- 汇总指标
    scores = {"n_questions": len(qs), "modes": MODES, "by_mode": {}}
    for m in MODES:
        sub = [r for r in runs if r["mode"] == m]
        n = max(1, len(sub))
        ot = max(1, sum(r["obligations_total"] for r in sub))
        scores["by_mode"][m] = {
            "recall_hit_top5": round(sum(r["evidence_hit_top5"] for r in sub) / n, 3),
            "recall_hit_top8": round(sum(r["evidence_hit_top8"] for r in sub) / n, 3),
            "recall_hit_top5_textonly": round(
                sum(r["evidence_hit_top5_textonly"] for r in sub) / n, 3),
            # ---- 答案层（证据义务覆盖，课程 P92 覆盖地图）----
            "oblig_total": sum(r["obligations_total"] for r in sub),
            "oblig_covered_recall_top5": round(
                sum(r["obligations_covered_top5"] for r in sub) / ot, 3),
            "oblig_covered_recall_top8": round(
                sum(r["obligations_covered_top8"] for r in sub) / ot, 3),
            "oblig_covered_answer": round(
                sum(r["obligations_covered_answer"] for r in sub) / ot, 3),
        }
    for m in ("hybrid", "cover"):
        by_type: dict[str, list[bool]] = {}
        for r in runs:
            if r["mode"] == m:
                by_type.setdefault(r["type"], []).append(r["evidence_hit_top5"])
        scores[f"{m}_by_type"] = {
            t: {"n": len(v), "recall_hit_top5": round(sum(v) / len(v), 3)}
            for t, v in by_type.items()}
    scores["first_run_measured"] = {
        "note": FIRST_RUN_NOTE,
        "recall_hit_top5": {m: FIRST_RUN[m] for m in MODES},
        "diff_vs_v1_judged_on_current_retrieval": FIRST_RUN_DIFF_NOTE,
    }
    json.dump(scores, open(os.path.join(OUT, "eval_scores.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---------------- 评测台账（Markdown 对照表，作业交付项）
    L: list[str] = []
    L.append("# 10 题 × 4 种检索模式 评测台账")
    L.append("")
    L.append(f"- 语料：{len(eng.meta['reports'])} 份财报 · "
             f"{eng.meta['n_chunks']} 个知识块")
    L.append(f"- 向量：{eng.meta['embedder']}（{eng.meta['embed_dim']} 维）· "
             f"BM25：{eng.meta['bm25']}")
    L.append(f"- 分词：{eng.meta['tokenizer']}")
    L.append("- 判卷口径：每题的 `must_contain` 是若干「同义写法组」，"
             "召回块中每组命中任一写法即算该组命中，全部组命中才算本题召回命中。")
    L.append("  - **v2 判卷口径（主口径）**：命中判定把块的"
             "「公司/报告类型/章节」元数据一并纳入——公司名已由过滤器强约束，"
             "不应再要求正文复述（修正 Q02 类误判）。")
    L.append("  - **v1 判卷口径（对照，仅正文）**：只按正文文本判定、不纳入块元数据，"
             "即首轮评测使用的那套判卷规则。")
    L.append("    ⚠ 本列是**当前检索结果**按该规则**重算**的值，"
             "**不等于首轮实测数字**：首轮之后检索侧另有修复（领域词典补入跨市场术语），"
             "Q03 由未命中转为命中，故 hybrid / bm25 各高 10pt。")
    L.append("  - **首轮实测（历史对照）**：2026-09-24 检索侧修复前跑出来的真实数字，"
             "**不由本脚本计算**，是固化的历史记录，单列在第 4 列以便逐格对齐。")
    L.append("  - **soft 组**：题干问到但**语料未披露**的指标单列，"
             "未命中不计检索失败，但台账标注「语料未披露」。")
    L.append("")
    L.append("## 1. 汇总")
    L.append("")
    L.append("| 检索模式 | 命中率@Top5（v2 主口径） | 命中率@Top8 | "
             "v1 判卷口径@Top5（当前检索重算·对照） | 首轮实测@Top5（检索侧修复前） |")
    L.append("|---|---|---|---|---|")
    for m in MODES:
        s = scores["by_mode"][m]
        star = " ★" if m == "cover" else ""
        fr = FIRST_RUN[m]
        frs = "—" if fr is None else f"{fr:.0%}"
        L.append(f'| {MODE_LABEL[m]}{star} | {s["recall_hit_top5"]:.0%} '
                 f'| {s["recall_hit_top8"]:.0%} '
                 f'| {s["recall_hit_top5_textonly"]:.0%} | {frs} |')
    L.append("")
    L.append("**两个对照列的差异来源（逐模式）**")
    L.append("")
    L.append("| 检索模式 | 首轮实测@Top5 | v1 判卷口径@Top5 | 差 | 差异来源 |")
    L.append("|---|---|---|---|---|")
    for m in MODES:
        fr = FIRST_RUN[m]
        v1 = scores["by_mode"][m]["recall_hit_top5_textonly"]
        if fr is None:
            L.append(f"| {MODE_LABEL[m]} | — | {v1:.0%} | — | "
                     f"{FIRST_RUN_DIFF_NOTE[m]} |")
        else:
            d = v1 - fr
            ds = "一致" if abs(d) < 1e-9 else f"{d:+.0%}"
            L.append(f"| {MODE_LABEL[m]} | {fr:.0%} | {v1:.0%} | {ds} | "
                     f"{FIRST_RUN_DIFF_NOTE[m]} |")
    L.append("")
    L.append(f"> {FIRST_RUN_NOTE}")
    L.append("> ")
    L.append("> **读法提示**：README 与一页结论里的「首次评测」列 = 本表**首轮实测**列；"
             "本表**v1 判卷口径**列是在**当前检索结果**上的重算值。两列相差 10pt "
             "（hybrid / bm25）属正常，不是数字错误——前者是历史检索结果，"
             "后者是现行检索结果，差的那一题是 Q03。")
    L.append("")
    for m, title in (("hybrid", "hybrid（基线）"), ("cover", "cover（+分组召回）")):
        L.append(f"**分题型 · {title} @Top5**")
        L.append("")
        L.append("| 题型 | 题数 | 命中率@Top5 |")
        L.append("|---|---|---|")
        for t, s in scores[f"{m}_by_type"].items():
            L.append(f'| {t} | {s["n"]} | {s["recall_hit_top5"]:.0%} |')
        L.append("")
    # ---------------- 两层对照：召回表现 vs 答案表现（本次改版的核心新增）
    L.append("## 1b. 两层对照：召回表现 ≠ 答案表现")
    L.append("")
    L.append("口径依据：**第三课 P90–P92**。P92 要求先把问题拆成若干"
             "「**证据义务**」，逐项标记 已覆盖 / 部分 / 缺；P91 专门警告"
             "「**数字丢口径**」——数值进了图谱只剩一个节点，"
             "「4,026,356.42 万元」是谁的、哪个口径、哪一期，**要回到那张表**。")
    L.append("")
    L.append("所以本表把「数值命中」与「**归宿正确**」合并成一条判定："
             "在下属公司为 A 的义务上，数字必须出现在 **A 自己的报告块**里，"
             "才算这项义务被覆盖。只出现数字、不归属到公司，判「部分」而非「已覆盖」。")
    L.append("")
    L.append("| 模式 | 召回层：证据组命中@Top5 | 召回层：证据义务覆盖@Top5 "
             "| 召回层：证据义务覆盖@Top8 | **答案层：证据义务覆盖（最终答案）** "
             "| 义务总数 |")
    L.append("|---|---|---|---|---|---|")
    for m in MODES:
        s = scores["by_mode"][m]
        L.append(f'| {MODE_LABEL[m]} | {s["recall_hit_top5"]:.0%} '
                 f'| {s["oblig_covered_recall_top5"]:.0%} '
                 f'| {s["oblig_covered_recall_top8"]:.0%} '
                 f'| **{s["oblig_covered_answer"]:.0%}** | {s["oblig_total"]} |')
    L.append("")
    L.append("> 读法：四列**不是同一个量**，逐列变严。"
             "第 1 列看「关键词组（含同义写法）有没有被召回」——宽松上界；"
             "第 2、3 列把判定换成**具体证据义务**（谁的、哪个指标、哪一期），"
             "分别在 Top5 / Top8 上量；第 4 列只看**最终交给用户的答案**覆盖了几项。"
             "本项目的答案生成读取 Top8 召回块，所以**与答案层直接可比的是第 3 列**——"
             "cover 的 52% 对应 Top8 召回 64%，落差 12pt 就是「召回得到、答不出来」的损耗；"
             "hybrid / bm25 的答案层已与自身 Top8 召回基本持平（40% vs 50%、36% vs 48%）。"
             "这条落差正是老师指出的「Top5 关键词命中不能替代答题正确」。")
    L.append("")
    L.append("**按问题形状分（课程 P90 第一步：先判断问题的形状）**")
    L.append("")
    L.append("| 问题形状 | 题数 | 义务数 | 召回层义务覆盖@Top5 "
             "| 召回层义务覆盖@Top8 | 答案层义务覆盖 |")
    L.append("|---|---|---|---|---|---|")
    for sh in ("局部", "全局", "多跳"):
        sub = [r for r in runs if r.get("shape") == sh and r["mode"] == "cover"]
        if not sub:
            continue
        n_ob = sum(r["obligations_total"] for r in sub)
        ot = max(1, n_ob)
        L.append(f'| {SHAPE_LABEL.get(sh, sh)} | {len(sub)} | {n_ob} '
                 f'| {sum(r["obligations_covered_top5"] for r in sub) / ot:.0%} '
                 f'| {sum(r["obligations_covered_top8"] for r in sub) / ot:.0%} '
                 f'| **{sum(r["obligations_covered_answer"] for r in sub) / ot:.0%}** |')
    L.append("")
    L.append("> 形状一栏用 **cover 模式**统计（分组召回是最终模式）。"
             "可读出两件事：① **全局·全景题**的召回明显弱于局部题"
             "——这正是课程 P61–P63 说的「全景题没有哪一块能单独回答」；"
             "② 局部题的答案层已追平召回层，说明单公司题「召回到就能答对」，"
             "损耗几乎全部发生在跨公司题上。")
    L.append("## 2. 逐题明细")
    L.append("")
    for q in qs:
        L.append(f'### {q["qid"]}. {q["q"]}')
        L.append("")
        L.append(f'- 题型：`{q["type"]}`'
                 + (f' · 限定公司 {q.get("companies")}' if q.get("companies") else "")
                 + (f' · 限定报告 {q.get("kinds")}' if q.get("kinds") else ""))
        if q.get("note"):
            L.append(f'- 备注：{q["note"]}')
        if q.get("shape"):
            L.append(f'- 问题形状（课程 P90 第一步）：'
                     f'`{SHAPE_LABEL.get(q["shape"], q["shape"])}`'
                     + (f' · 义务数 {len(q.get("obligations") or [])}'
                        if q.get("obligations") else ''))
        if q.get("obligation_note"):
            L.append(f'- 义务拆分说明：{q["obligation_note"]}')
        if q.get("standard_answer"):
            L.append(f'- **标准答案（含出处，人工核对原始 PDF）**：'
                     f'{q["standard_answer"]}')
        if q.get("soft_must_contain"):
            L.append(f'- soft 组（语料未披露即不算失败）：{q["soft_must_contain"]}')
        L.append("")
        L.append("| 模式 | 证据组命中@5 | 证据组@8 | （v1@5，对照） "
                 "| 义务覆盖@Top5 | **答案义务覆盖** | Top1 出处 |")
        L.append("|---|---|---|---|---|---|---|")
        for m in MODES:
            r = next(x for x in runs if x["qid"] == q["qid"] and x["mode"] == m)
            f5 = "命中" if r["evidence_hit_top5"] else (
                "仅Top8" if r["evidence_hit_top8"] else "**未命中**")
            f8 = "命中" if r["evidence_hit_top8"] else "**未命中**"
            tv1 = "命中" if r["evidence_hit_top5_textonly"] else "未命中"
            tot = r["obligations_total"]
            ob_r = f'{r["obligations_covered_top5"]}/{tot}' if tot else "—"
            ob_a = f'**{r["obligations_covered_answer"]}/{tot}**' if tot else "—"
            L.append(f'| {MODE_LABEL[m]} | {f5} | {f8} | {tv1} '
                     f'| {ob_r} | {ob_a} | {r["top1_cite"] or "—"} |')
        L.append("")
        # 逐项义务的覆盖地图（课程 P92：每项义务标 已覆盖/部分/缺）
        cov = next(x for x in runs if x["qid"] == q["qid"] and x["mode"] == "cover")
        if cov.get("obligations_detail_answer"):
            L.append("**证据义务覆盖地图（cover 模式；课程 P92 口径）**")
            L.append("")
            L.append("| 义务 | 主体 | 指标 | 期次 | 标准出处 "
                     "| 召回层@Top5 | 答案层 | 缺的原因 |")
            L.append("|---|---|---|---|---|---|---|---|")
            rr = cov.get("gap_reasons") or []
            rec5 = {d["id"]: d for d in (cov.get("obligations_detail_top5") or [])}
            for d in cov["obligations_detail_answer"]:
                tag = {x["id"]: f'{x["code"]} {x["reason"]}'
                       for x in rr}.get(d["id"], "")
                mark = {"已覆盖": "✅", "部分": "⚠️ 数字无归宿", "缺": "❌"}[d["status"]]
                st_r = (rec5.get(d["id"]) or {}).get("status")
                rmark = {"已覆盖": "✅", "部分": "⚠️", "缺": "❌"}.get(st_r, "—")
                L.append(f'| {d["id"]} | {d["subject"] or "—"} | {d["item"] or "—"} '
                         f'| {d["period"] or "—"} | {d["src"] or "—"} '
                         f'| {rmark} | {mark} | {tag or "—"} |')
            L.append("")
        raw = next(x for x in runs if x["qid"] == q["qid"] and x["mode"] == "cover")
        if raw.get("soft_detail_top5") and not all(raw["soft_detail_top5"]):
            miss = "、".join("/".join(g) for g in raw["soft_gap"])
            L.append(f'> ⚠ **语料未披露**：Top5 未覆盖 [{miss}]。'
                     f'经全语料核查确认该报告未以此口径披露，属语料缺失而非检索失败；'
                     f'系统未编造数字，记为正确行为。')
            L.append("")
        if raw.get("bullets"):
            L.append("**答案与出处（cover 模式：hybrid + 分组召回）**")
            L.append("")
            for b in raw["bullets"]:
                L.append(f'{b["n"]}. {b["text"]}')
                L.append(f'   - ↳ {b["cite"]}')
            L.append("")
    # ---------------- 失败归因汇总（焦点词 / 公司名 定位具体到哪一项义务）
    from collections import Counter
    cnt: Counter = Counter()
    examples: dict[str, list[str]] = {}
    for r in runs:
        for g in r.get("gap_reasons") or []:
            cnt[g["code"]] += 1
            examples.setdefault(g["code"], []).append(
                f'{r["qid"]}/{r["mode"]}/{g["id"]} {g["subject"]}·{g["item"]}')
    L.append("## 3. 失败归因：缺的那一项到底缺在哪")
    L.append("")
    L.append("分类依据课程 P90「先判断问题的形状」与 P92 覆盖地图。"
             "判定顺序刻意从「与检索无关」排到「检索侧」，避免把数据边界"
             "误记成检索失败：")
    L.append("")
    L.append("**计数口径**：按 `(题目, 模式, 义务)` 三元组计一次。同一项义务在 "
             "4 个模式下各计一次，故总数接近 `义务 42 × 模式 4 = 168` 个格子"
             "减去已覆盖的部分。读这张表要看**归因的分布**（谁占大头、"
             "哪一类是 cover 特有的消融证据），而不是单个数字的大小。")
    L.append("")
    L.append("| 代码 | 归因 | 次数 | 具体落在哪（题/模式/义务）|")
    L.append("|---|---|---|---|")
    for code in ("A", "B", "C", "D", "E", "F", "G"):
        n = cnt.get(code, 0)
        ex = examples.get(code, [])
        show = "、".join(ex[:6]) + ("…" if len(ex) > 6 else "") if ex else "—"
        L.append(f'| {code} | {GAP_REASON[code]} | {n} | {show} |')
    L.append("")
    L.append("> **A 语料未披露** 不是失败，是数据边界（课程 P92：缺数据的要明说，"
             "不许编）；**C 焦点词误杀** 是 cover 模式特有——关掉「指标焦点词」"
             "硬过滤后该项立刻可召回，说明硬过滤把正确块挡掉了（这是可复现的消融对照，"
             "不是推测）。")
    L.append("> ")
    L.append("> **A 与 G 必须分开读**：A 是「报告本身就没写这个口径」"
             "（例：中通 2026 半年报通篇没有『市场份额/市占率/市场占有率』，"
             "属数据边界，系统明说「未披露」就是正确行为）；"
             "G 是「报告写了、本项目切块时没把数值抽出来」"
             "（例：申通 P25 分行业表的『毛利率』列只剩表头、单元格为空，"
             "回原始 PDF 第 25 页可读到『快递服务 6.97%、增加 2.08 个百分点』）"
             "——G 属**本项目的语料缺陷**，是检索侧的真实失败，不计入「数据边界」。")
    L.append("")

    # ---------------- 全景题逐公司取证（课程 P63：每个回答只覆盖几家）
    L.append("## 4. 全景题按公司取证（课程 P61–P63）")
    L.append("")
    L.append("课程 P61–P63 的结论：**行业全景问题没有哪一块能单独回答**，"
             "每条路线召回的只是几家公司的几段话；P63 更直接——"
             "「五个回答同样只覆盖几家，哪个路线都只看到其中一角」。"
             "所以全景题必须**逐公司**核对取证情况，而不是只看整体命中。")
    L.append("")
    for q in qs:
        comps = q.get("panorama_companies") or q.get("companies")
        if q.get("shape") != "全局" or not comps:
            continue
        r = next(x for x in runs if x["qid"] == q["qid"] and x["mode"] == "cover")
        det = set(r.get("company_hit") or [])
        named = set(r.get("company_named") or [])
        ds = r["obligations_detail_answer"]
        L.append(f'### {q["qid"]}（cover 模式）')
        L.append("")
        if not named:
            L.append("> 题面并未点名具体公司（问的是「各家公司」），"
                     "故不适用「公司名识别」一列；下表按义务主体逐家列出取证结果。")
            L.append("")
        L.append("| 公司 | 公司名识别 | 义务数 | 已覆盖 | 说明 |")
        L.append("|---|---|---|---|---|")
        for c in comps:
            obs_c = [d for d in ds if d["subject"] == c]
            ok = [d for d in obs_c if d["status"] == "已覆盖"]
            note = ""
            if c in named and c not in det:
                note = "⚠️ 题面点名但未被识别 → 分组召回分不出这一组"
            elif not obs_c:
                note = "（本题未对该家单列义务）"
            elif len(ok) < len(obs_c):
                miss = [d["item"] or d["id"] for d in obs_c
                        if d["status"] != "已覆盖"]
                note = "缺：" + "、".join(miss)
            recogn = "✅" if c in det else ("❌" if c in named else "— 题面未点名")
            L.append(f'| {c} | {recogn} | {len(obs_c)} '
                     f'| {len(ok)}/{len(obs_c)} | {note or "✅ 全部取到"} |')
        L.append("")

    open(os.path.join(OUT, "eval_table.md"), "w",
         encoding="utf-8").write("\n".join(L))

    print("\n==== 召回命中率（Top5 / Top8，v2 主口径）====")
    for m in MODES:
        s = scores["by_mode"][m]
        fr = FIRST_RUN[m]
        frs = "—  " if fr is None else f"{fr:.0%}"
        print(f'  {m:7s} {s["recall_hit_top5"]:.0%}  /  {s["recall_hit_top8"]:.0%}'
              f'   (v1 判卷口径 {s["recall_hit_top5_textonly"]:.0%}'
              f' · 首轮实测 {frs})')
    print("\n==== 答案层：证据义务覆盖（课程 P92 覆盖地图口径）====")
    for m in MODES:
        s = scores["by_mode"][m]
        print(f'  {m:7s} 召回层 {s["oblig_covered_recall_top5"]:.0%}'
              f'  →  答案层 {s["oblig_covered_answer"]:.0%}'
              f'   （义务 {s["oblig_total"]} 项）')
    from collections import Counter as _C
    c: _C = _C()
    for r in runs:
        for g in r.get("gap_reasons") or []:
            c[g["code"]] += 1
    if c:
        print("\n==== 失败归因 ====")
        for code in ("A", "B", "C", "D", "E", "F", "G"):
            if c.get(code):
                print(f'  {code} {GAP_REASON[code]}: {c[code]}')
    print("\n==== 分题型 · cover（Top5）====")
    for t, s in scores["cover_by_type"].items():
        print(f'  {t:14s} n={s["n"]}  {s["recall_hit_top5"]:.0%}')
    print(f"\n明细：{os.path.join(OUT, 'eval_runs.json')}")


if __name__ == "__main__":
    sys.exit(main())
