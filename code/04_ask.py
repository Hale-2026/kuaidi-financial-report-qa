#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04_ask.py —— 作业3-A 第三/四步：混合检索 + 带出处的答案生成

检索：
  · BM25（jieba 分词）—— 抓住"单票收入""毛利率"这类精确术语与数字
  · 向量（bge-small-zh）—— 抓住"怎么描述价格战""竞争优势"这类语义问题
  · 融合：RRF（Reciprocal Rank Fusion, k=60），对两路排名取调和，无需调分数权重

答案：
  · 默认「抽取式」：从召回块中按查询词权重 + 数字命中挑句子，拼成答案并逐句挂出处
    —— 不引入 LLM，可复现、可审计，检索失败不会被生成模型"圆过去"
  · 可选「生成式」：在仓库根目录的 .env 里（或直接设环境变量）配置
    OPENAI_BASE_URL / OPENAI_API_KEY / LLM_MODEL
    即自动改用 OpenAI 兼容接口做 grounded 生成（只依据召回块作答 + 强制引用编号）

用法（命令行）：
  python code/04_ask.py "顺丰控股2026年上半年单票收入是多少" --mode hybrid -k 6
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from embedder import build_embedder                             # noqa: E402
from importlib import import_module                              # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX_DIR = os.path.join(ROOT, "data", "index")


def load_dotenv(path: str | None = None) -> None:
    """极简 .env 加载器（零依赖，不引 python-dotenv）。

    「key 只放在 .env 里」这句话要真正成立，前提是**代码会读 .env** ——
    否则照 .env.example 里 `cp .env.example .env` 做完，key 依然不生效。

      · 只补 os.environ 里**没有**的键，真实环境变量始终优先；
      · 支持 # 注释、空行、可选的 `export ` 前缀、单/双引号包裹；
      · .env 已在 .gitignore 中排除，仓库里只留不含真实值的 .env.example；
      · 文件不存在时静默跳过 —— 默认走抽取式答案，功能完整、评测不受影响。
    """
    p = path or os.path.join(ROOT, ".env")
    if not os.path.isfile(p):
        return
    try:
        with open(p, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


load_dotenv()
CHUNK_F = os.path.join(ROOT, "data", "chunks", "chunks.jsonl")
MODEL_DIR = os.path.join(ROOT, "data", "model", "bge-small-zh-v1.5")
RRF_K = 60

# 公司简称表：用于「分组召回」识别问题里点名的公司。
# 只收明确无歧义的简称——「中国外运」的简称不能取「中国」，「中储股份」不能取「中国」，
# 否则任意问题都会被误判成跨公司题。
ALIASES = {
    "顺丰控股": "顺丰", "圆通速递": "圆通", "韵达股份": "韵达",
    "申通快递": "申通", "中通快递": "中通", "京东物流": "京东物流",
    "极兔速递": "极兔", "华贸物流": "华贸", "东航物流": "东航",
    "中储股份": "中储", "中谷物流": "中谷", "长久物流": "长久",
    "密尔克卫": "密尔克卫", "传化智联": "传化", "嘉友国际": "嘉友",
    "铁龙物流": "铁龙", "中国外运": "中外运",
}
COVER_MODES = ("cover", "hybrid+cover")

# 跨市场术语同义表：同一个指标各家披露用词不同，查询里出现任一变体就把
# 同组其它变体一并加进 BM25 词袋（否则「单票收入」永远搜不到中通的"包裹单价"）。
# 依据（已在语料中逐条核实）：
#   中通快递·2026半年报 P13「包裹量增加9.6%及包裹单价增加12.0%」
#   顺丰控股·2026半年报 P…「票均收入(元/票) 14.4 / 14.0 / 3.3%」
TERM_SYNONYMS = [
    ["单票收入", "票均收入", "包裹单价", "每票收入", "单票价格"],
    ["市场份额", "市占率", "市场占有率"],
    ["业务量", "包裹量", "件量", "发件量"],
    ["归母净利润", "归属于上市公司股东的净利润"],
    ["毛利率", "毛利"],
]


def expand_query(query: str) -> list[str]:
    """术语同义扩写：返回查询中出现的同义词组里、查询本身没有的其它写法"""
    extra: list[str] = []
    for grp in TERM_SYNONYMS:
        if any(w in query for w in grp):
            extra += [w for w in grp if w not in query]
    return extra


def focus_terms(query: str, sub_q: str) -> list[str]:
    """挑「这道题到底在问哪个指标」的焦点词。

    两条铁律（都是踩坑换来的）：
      1. 只从指标术语表（领域词典 + 同义表）里取——绝不按 IDF 挑，
         否则会选中「多少」「差距」这类财报里罕见但零信息量的疑问词；
      2. 同义组要整组带出——问到「单票收入」时必须把「票均收入 / 包裹单价 /
         每票收入」一起算作有效焦点词，否则中通那份报告里根本没有「单票收入」
         三个字，硬约束会把它整组的候选全部过滤光。
    """
    pool: list[str] = list(
        getattr(import_module("03_build_index"), "DOMAIN_WORDS", []))
    for grp in TERM_SYNONYMS:
        pool += grp
    hay = f"{query} {sub_q} " + " ".join(expand_query(query))

    out: list[str] = []
    for grp in TERM_SYNONYMS:                   # 命中的同义组 -> 整组带出
        if any(w in hay for w in grp):
            out += [w for w in grp if w in pool and w not in out]
    for w in sorted(pool, key=lambda x: -len(x)):
        if w in hay and w not in out:           # 再补领域词典里的指标词
            out.append(w)
    return out[:6]


# ------------------------------------------------------------------ 工具
def load_docs() -> list[dict]:
    return [json.loads(l) for l in open(CHUNK_F, encoding="utf-8")]


def tokenize(text: str) -> list[str]:
    return import_module("03_build_index").tokenize(text)


NUMQ = re.compile(r"多少|几|金额|收入|利润|毛利|占比|比重|增长率|增速|同比|"
                  r"环比|规模|总额|亿元|万元|比例|率是|达到|排名|第几|市占")
NUM = re.compile(r"\d[\d,]*\.?\d*\s*(?:%|％|亿元|万元|亿|万|元|件|票|吨|人|家|个)"
                 r"|\d[\d,]*\.?\d*")


class Engine:
    def __init__(self, root: str = ROOT):
        self.docs = load_docs()
        idx = __import__("pickle").load(open(os.path.join(IDX_DIR, "bm25.pkl"), "rb"))
        self.bm25, self.tokens = idx["bm25"], idx["tokens"]
        self.bm25_impl = idx["impl"]
        self.V = np.load(os.path.join(IDX_DIR, "emb.npy"))
        self.meta = json.load(open(os.path.join(IDX_DIR, "index_meta.json"),
                                   encoding="utf-8"))
        self.company_set = set(self.meta["companies"])
        self.embedder = build_embedder(MODEL_DIR)

    # ------------------------------------------------------- 分组召回辅助
    def detect_companies(self, query: str) -> list[str]:
        """识别问题里点名的公司（全称或简称），按出现顺序返回"""
        out = []
        for full, alias in ALIASES.items():
            if full not in self.company_set:
                continue
            if full in query or alias in query:
                if full not in out:
                    out.append(full)
        return out

    def strip_companies(self, query: str) -> str:
        """剔除公司名，留下"要问什么"——分组召回时用这个子查询在各家内部检索，
        否则 BM25 会把"公司名罗列几十遍"的释义表/子公司名录当成最相关。"""
        q = query
        for full, alias in ALIASES.items():
            q = q.replace(full, " ").replace(alias, " ")
        q = re.sub(r"\s+", " ", q).strip(" ，,、。？?")
        return q or query

    # -------------------------------------------------------------- 过滤
    def _mask(self, companies=None, kinds=None, sections=None,
              segments=None) -> np.ndarray:
        m = np.ones(len(self.docs), dtype=bool)
        for i, d in enumerate(self.docs):
            if companies and d["company"] not in companies:
                m[i] = False
            if kinds and d["kind"] not in kinds:
                m[i] = False
            if segments and d["segment"] not in segments:
                m[i] = False
            if sections and not any(s in d["section"] for s in sections):
                m[i] = False
        return m

    # ---------------------------------------------------- 分组召回（全景题）
    def _search_cover(self, query: str, topk: int = 8,
                      mode: str = "hybrid", **filters) -> list[dict]:
        """跨公司全景题的专用召回：把"点名的多家公司"拆成 N 个子查询分别检索，
        再按轮转交错合并——保证 Top-K 里每家公司都有位置。

        动机（实测）：单轮全局检索时，问题里出现 4 个公司名，BM25 会把
        「释义表」「子公司名录」这类把公司名罗列几十遍的附录表顶到最前面，
        真正含财务数据的块反而排不进去；Top-5 也几乎被同一家公司占满。
        """
        comps = filters.get("companies") or self.detect_companies(query)
        comps = [c for c in comps if c in self.company_set]
        if len(comps) < 2:
            return self.search(query, topk=topk, mode=mode, **filters)

        sub_q = self.strip_companies(query)
        per = max(1, -(-topk // len(comps)))            # 每家配额 = ceil(topk/N)

        # 组内「核心术语」硬约束：只靠覆盖率会塞进噪声块（实测圆通组内 Top1
        # 命中的是《反恐怖主义法》法规清单、中通组内命中的是财报附注）。
        # 要求每家召回的块必须含这道题的「指标焦点词」，噪声自然被挡掉。
        focus = focus_terms(query, sub_q)
        if not focus:
            focus = []

        groups = []
        for c in comps:
            f = dict(filters)
            f["companies"] = [c]
            cand = self.search(sub_q, topk=60, mode=mode, **f)
            if focus:
                keep = [h for h in cand
                        if any(w in h["text"] for w in focus)]
                g = (keep or cand)[:per]                # 该家确实没有 -> 退回原序
            else:
                g = cand[:per]
            for h in g:
                h["cover_group"] = c
            groups.append(g)

        out, seen = [], set()
        for r in range(per):                            # 轮转交错，而非按公司分块
            for g in groups:
                if r < len(g) and g[r]["chunk_id"] not in seen:
                    seen.add(g[r]["chunk_id"])
                    out.append(g[r])
        if len(out) < topk:                             # 不够再用全局结果补足
            for h in self.search(query, topk=topk * 2, mode=mode, **filters):
                if h["chunk_id"] not in seen:
                    seen.add(h["chunk_id"])
                    out.append(h)
                if len(out) >= topk:
                    break
        return out[:topk]

    # -------------------------------------------------------------- 检索
    def search(self, query: str, topk: int = 8, mode: str = "hybrid",
               **filters) -> list[dict]:
        if mode in COVER_MODES:
            return self._search_cover(query, topk=topk, **filters)
        mask = self._mask(**{k: v for k, v in filters.items()
                             if k in ("companies", "kinds", "sections",
                                      "segments")})
        n = len(self.docs)
        ranks: dict[str, dict] = {}

        if mode in ("hybrid", "bm25"):
            # 查询侧术语同义扩写：跨市场披露用词不一致时靠这一步对齐口径
            qs = self.bm25.get_scores(tokenize(query) + expand_query(query))
            qs = np.where(mask, qs, -1e9)
            order = np.argsort(-qs)
            for r, i in enumerate(order[:200]):
                ranks.setdefault(int(i), {})["bm25"] = (r + 1, float(qs[i]))

        if mode in ("hybrid", "vector"):
            qv = self.embedder.encode_query(query)
            vs = self.V @ qv
            vs = np.where(mask, vs, -1e9)
            order = np.argsort(-vs)
            for r, i in enumerate(order[:200]):
                ranks.setdefault(int(i), {})["vector"] = (r + 1, float(vs[i]))

        hits = []
        for i, rk in ranks.items():
            if mode == "hybrid":
                sc = sum(1.0 / (RRF_K + rk[k][0]) for k in rk)
            elif mode == "bm25":
                sc = rk.get("bm25", (10 ** 6, 0))[1]
            else:
                sc = rk.get("vector", (10 ** 6, 0))[1]
            d = dict(self.docs[i])
            d.update({
                "idx": i, "score": round(float(sc), 6),
                "rank_bm25": rk.get("bm25", (None,))[0],
                "rank_vector": rk.get("vector", (None,))[0],
                "score_bm25": round(rk["bm25"][1], 4) if "bm25" in rk else None,
                "score_vector": round(rk["vector"][1], 4) if "vector" in rk else None,
                "retriever": "+".join(sorted(rk)),
                "cite": f'{d["company"]}·{d["kind"]}·{d["section"]}'
                        f'·P{d["page_start"]}',
            })
            hits.append(d)
        hits.sort(key=lambda x: -x["score"])
        return hits[:topk]

    # -------------------------------------------------------------- 答案
    def answer(self, query: str, topk: int = 6, mode: str = "hybrid",
               **filters) -> dict:
        hits = self.search(query, topk=topk, mode=mode, **filters)
        if not hits:
            return {"query": query, "answer": "未检索到相关内容。",
                    "citations": [], "hits": [], "mode": mode}
        ans = self._gen(query, hits)
        return {"query": query, "mode": mode, "answer": ans["answer"],
                "bullets": ans["bullets"], "citations": ans["citations"],
                "hits": hits, "engine": ans["engine"],
                "bm25_impl": self.bm25_impl}

    def _gen(self, query: str, hits: list[dict]) -> dict:
        if os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_BASE_URL"):
            try:
                return _llm_answer(query, hits)
            except Exception as e:                                # noqa: BLE001
                print(f"[warn] LLM 生成失败({e})，回落抽取式", file=sys.stderr)

        qterms = set(tokenize(query))
        num_q = bool(NUMQ.search(query))
        cands = []
        for ci, h in enumerate(hits):
            body = h["text"]
            is_tab = h["has_table"]
            units = ([l for l in body.split("\n") if l.strip()]
                     if is_tab else
                     [s for s in re.split(r"(?<=[。；！？])", body) if s.strip()])
            for si, s in enumerate(units):
                s2 = s.strip()
                if len(s2) < 6:
                    continue
                t = set(tokenize(s2))
                if not t:
                    continue
                overlap = len(qterms & t)
                if overlap == 0 and not (num_q and NUM.search(s2)):
                    continue
                # 句内命中密度 + 数字奖励 + 靠前奖励
                dens = overlap / (len(t) ** 0.5)
                score = dens + (0.45 if (num_q and NUM.search(s2)) else 0)
                score += 0.25 / (1 + si) + 0.30 / (1 + ci)
                if h["has_table"]:
                    score += 0.15
                cands.append((score, ci, s2, bool(NUM.search(s2))))

        cands.sort(key=lambda x: -x[0])
        picked, seen = [], set()

        # 跨公司题：答案也要按公司轮转，不能按全局分数排——
        # 否则同一家公司的高分句会把 6 个位置占满，用户看不到其它公司。
        # （实测：Q01 修这句之前，答案里圆通给的是"营业收入386.21亿"，
        #   韵达却给成了"业务量122.57亿票"、顺丰给成了"营业成本1345.9亿"。）
        companies_in = {h["company"] for h in hits}
        if len(companies_in) > 1:
            focus = focus_terms(query, query)       # 这道题问的是哪个指标
            buckets: dict[str, list] = {}
            for c in cands:
                buckets.setdefault(hits[c[1]]["company"], []).append(c)
            lst = []
            for b in buckets.values():
                # 桶内优先取含指标词的句子：否则"业务量122.57亿票"这类
                # 同样带数字、但答非所问的句子会把"营业收入"证据顶掉
                pref = [c for c in b if any(w in c[2] for w in focus)]
                lst.append(pref + [c for c in b if c not in pref])
            picked_round: list = []
            for r in range(6):
                for b in lst:
                    if r < len(b) and len(picked_round) < 6:
                        sc, ci, s, hn = b[r]
                        key = re.sub(r"\W", "", s)[:24]
                        if key not in seen:
                            seen.add(key)
                            picked_round.append((sc, ci, s, hn))
                if len(picked_round) >= 6:
                    break
            picked = picked_round
        else:
            for sc, ci, s, has_num in cands:
                key = re.sub(r"\W", "", s)[:24]
                if key in seen:
                    continue
                seen.add(key)
                picked.append((sc, ci, s, has_num))
                if len(picked) >= 6:
                    break

        if not picked:
            picked = [(1.0, 0, hits[0]["text"][:220].replace("\n", " "), False)]

        used = sorted({ci for _, ci, _, _ in picked})
        remap = {ci: k + 1 for k, ci in enumerate(used)}
        bullets = []
        for sc, ci, s, has_num in picked:
            s_show = s if len(s) <= 300 else s[:300] + "…"
            bullets.append({"text": s_show, "n": remap[ci],
                            "cite": hits[ci]["cite"], "has_number": has_num,
                            "score": round(sc, 4)})

        top = hits[0]
        head = (f'依据 {len(used)} 个知识块（{s_show_companies(used, hits)}）'
                f'，最相关来源：{top["cite"]}。')
        if num_q:
            num_found = [b for b in bullets if b["has_number"]]
            if num_found:
                head += f' 检出 {len(num_found)} 条含数字的关键证据，见下方标注。'
            else:
                head += ' ⚠ 未检出明确数字，该问题可能需看完整表格。'
        citations = [{"n": remap[ci], "cite": hits[ci]["cite"],
                      "company": hits[ci]["company"], "kind": hits[ci]["kind"],
                      "section": hits[ci]["section"],
                      "page": hits[ci]["page_start"],
                      "chunk_id": hits[ci]["chunk_id"],
                      "has_table": hits[ci]["has_table"]} for ci in used]
        return {"answer": head, "bullets": bullets, "citations": citations,
                "engine": "extractive"}


def s_show_companies(idxs, hits) -> str:
    cs, out = set(), []
    for i in idxs:
        c = hits[i]["company"]
        if c not in cs:
            cs.add(c)
            out.append(c)
    return "、".join(out)


def _llm_answer(query: str, hits: list[dict]) -> dict:
    """OpenAI 兼容接口的 grounded 生成（仅在配置了环境变量时启用）"""
    import requests
    ctx = "\n\n".join(f'[{i + 1}] {h["cite"]}\n{h["text"][:1400]}'
                      for i, h in enumerate(hits))
    prompt = (f'只依据下面资料回答问题，每个结论后用 [编号] 标注出处；'
              f'资料中没有的就说"资料未披露"，不要编造数字。\n\n'
              f'【资料】\n{ctx}\n\n【问题】{query}')
    r = requests.post(
        os.environ["OPENAI_BASE_URL"].rstrip("/") + "/chat/completions",
        headers={"Authorization": f'Bearer {os.environ["OPENAI_API_KEY"]}'},
        json={"model": os.environ.get("LLM_MODEL", "gpt-4o-mini"),
              "temperature": 0.1,
              "messages": [{"role": "system",
                            "content": "你是严谨的财务报告分析助手。"},
                           {"role": "user", "content": prompt}]},
        timeout=120)
    r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"]
    citations = [{"n": i + 1, "cite": h["cite"], "company": h["company"],
                  "kind": h["kind"], "section": h["section"],
                  "page": h["page_start"], "chunk_id": h["chunk_id"],
                  "has_table": h["has_table"]} for i, h in enumerate(hits)]
    return {"answer": txt, "bullets": [], "citations": citations,
            "engine": "llm:" + os.environ.get("LLM_MODEL", "gpt-4o-mini")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-k", "--topk", type=int, default=6)
    ap.add_argument("--mode", default="hybrid",
                    choices=["hybrid", "bm25", "vector", "cover", "hybrid+cover"],
                    help="hybrid = BM25 + 向量 RRF 融合（默认）；bm25 / vector = 单路检索；"
                         "cover = 按公司分组召回（跨公司全景题专用，见 README 第 5 节）")
    ap.add_argument("--company", action="append")
    ap.add_argument("--kind", action="append")
    a = ap.parse_args()

    eng = Engine()
    r = eng.answer(a.query, topk=a.topk, mode=a.mode,
                   companies=a.company, kinds=a.kind)
    print(f'\n问题：{a.query}\n模式：{r["mode"]}   生成：{r["engine"]}\n')
    print("答案：", r["answer"], "\n")
    for b in r["bullets"]:
        print(f'  [{b["n"]}] {b["text"]}')
    print("\n出处：")
    for c in r["citations"]:
        print(f'  [{c["n"]}] {c["cite"]}  ({c["chunk_id"]})')
    print("\n召回明细：")
    for i, h in enumerate(r["hits"], 1):
        print(f'  {i}. score={h["score"]:.5f}  bm25#{h["rank_bm25"]} '
              f'vec#{h["rank_vector"]}  {h["cite"]}  [{h["chunk_id"]}]')


if __name__ == "__main__":
    sys.exit(main())
