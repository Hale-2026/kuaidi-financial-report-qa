#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_build_index.py —— 作业3-A 第三步：建向量索引 + BM25 索引

两路索引：
  A. BM25（词法召回）：jieba 分词（含金融/快递领域自定义词典）+ BM25Okapi
  B. 向量（语义召回）：bge-small-zh-v1.5 ONNX 句向量，L2 归一化后内积=余弦

落盘：
  data/index/bm25.pkl      BM25 对象 + 分词后语料
  data/index/emb.npy       (N, 512) float32 归一化向量
  data/index/index_meta.json
"""
from __future__ import annotations

import json
import os
import pickle
import re
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from embedder import build_embedder                             # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNK_F = os.path.join(ROOT, "data", "chunks", "chunks.jsonl")
MODEL_DIR = os.path.join(ROOT, "data", "model", "bge-small-zh-v1.5")
IDX_DIR = os.path.join(ROOT, "data", "index")
os.makedirs(IDX_DIR, exist_ok=True)

# 快递/财务领域词典，提升分词质量
DOMAIN_WORDS = [
    "单票收入", "单票成本", "票均收入", "包裹单价", "每票收入", "单票价格",
    "业务量", "件量", "市占率", "市场份额", "市场占有率", "毛利率",
    "净利率", "扣非净利润", "归母净利润", "营业收入", "营业成本", "期间费用",
    "销售费用", "管理费用", "研发费用", "财务费用", "经营活动现金流量净额",
    "资产负债率", "加权平均净资产收益率", "基本每股收益", "稀释每股收益",
    "中转", "分拨中心", "加盟商", "直营", "末端网点", "驿站", "智能柜",
    "时效件", "经济件", "特惠专配", "快运", "冷链", "同城", "即时配送",
    "仓配一体", "航空货运", "全货机", "外包", "自有运力", "干线运输",
    "单公斤成本", "客户集中度", "应收账款周转率", "存货周转率", "总资产周转率",
    "快递服务收入", "供应链及国际业务", "跨境电商", "国际快递", "跨境物流",
    "每股经营现金流", "研发投入", "资本开支", "在建工程", "固定资产",
]

STOP = set("的 了 和 与 及 或 在 是 有 为 对 等 中 上 下 之 其 该 本 我 们 他 这 那 "
           "the a an of to in for and or is are was were be been with by at on "
           "公司 报告 年度 期内 余额 期末 年初 本期 上期 项目 单位 元 万元 千元 "
           "一 二 三 四 五 六 七 八 九 十 不 也 都 会 可 以 将 于 由 从 到 按".split())


# --------------------------------------------------------------- 分词
# jieba 是「可选依赖」：装了就用地道分词 + 领域词典；没装则降级为
# 内置 CJK 二元切分（bigram）+ 领域词表命中，保证建索引这一步永不中断。
_JIEBA = None
_JIEBA_DONE = False

_TOKEN_RE = re.compile(r"[0-9][0-9,\.]*%?|[A-Za-z][A-Za-z0-9\.\-]*|[\u4e00-\u9fff]+")


def _load_jieba():
    """惰性加载 jieba 并注入领域词典；加载失败返回 None（降级，不抛错）"""
    global _JIEBA, _JIEBA_DONE
    if not _JIEBA_DONE:
        _JIEBA_DONE = True
        try:
            import jieba
            for w in DOMAIN_WORDS:
                jieba.add_word(w)
            _JIEBA = jieba
        except Exception as e:                                    # noqa: BLE001
            print(f"  [warn] jieba 不可用（{type(e).__name__}: {e}）")
            print("         → 降级为内置 CJK 二元切分；若要最佳分词质量：")
            print("           zsh code/bootstrap_deps.sh")
    return _JIEBA


def _ngram_cut(text: str):
    """无 jieba 时的兜底切分：中文按二字组切、英数按整词切，
    并把命中的领域词整词补进去（保住"单票收入"这类术语的精确匹配）。"""
    for m in _TOKEN_RE.finditer(text):
        s = m.group(0)
        if s[0].isascii():
            yield s.lower()
            continue
        if len(s) == 1:
            yield s
            continue
        for i in range(len(s) - 1):
            yield s[i:i + 2]
    for w in DOMAIN_WORDS:
        if w in text:
            yield w


def tokenize(text: str) -> list[str]:
    jb = _load_jieba()
    raw = jb.cut(text) if jb is not None else _ngram_cut(text)
    toks = []
    for w in raw:
        w = w.strip().lower()
        if not w or w in STOP:
            continue
        if len(w) == 1 and not w.isdigit() and not ("a" <= w <= "z"):
            continue
        if w.isdigit() and len(w) > 8:
            continue
        toks.append(w)
    return toks


def main() -> None:
    jb = _load_jieba()
    tok_impl = (f"jieba(领域词典 {len(DOMAIN_WORDS)} 词)" if jb is not None
                else "内置CJK二元切分(未装jieba)")

    docs = [json.loads(l) for l in open(CHUNK_F, encoding="utf-8")]
    print(f"载入 {len(docs)} 个知识块")

    t0 = time.time()
    print("→ 分词建 BM25 ...")
    toks = [tokenize(d["text"]) for d in docs]
    print(f"   平均 {np.mean([len(t) for t in toks]):.0f} 词/块")

    bm25_obj = None
    bm25_impl = "BM25Okapi"
    try:
        from rank_bm25 import BM25Okapi
        bm25_obj = BM25Okapi(toks, k1=1.5, b=0.75)
    except Exception as e:                                        # noqa: BLE001
        print(f"   rank_bm25 不可用({e})，改用自实现 BM25")
        bm25_obj = _BM25(toks)
        bm25_impl = "self-implemented BM25(k1=1.5,b=0.75)"

    with open(os.path.join(IDX_DIR, "bm25.pkl"), "wb") as f:
        pickle.dump({"bm25": bm25_obj, "tokens": toks,
                     "impl": bm25_impl}, f)

    print("→ 编码向量（BGE-small-zh ONNX）...")
    emb = build_embedder(MODEL_DIR)
    texts = [f'{d["company"]} {d["kind"]} {d["section"]} {d["text"]}'
             for d in docs]

    # 分片编码 + 断点续跑：CPU 上编码几万个 700 字块耗时很长，
    # 一旦中断可以从已完成的分片接着跑（part_%04d.npy）
    PART = 500
    nparts = (len(texts) + PART - 1) // PART
    fp_file = os.path.join(IDX_DIR, "parts_fingerprint.json")
    fp = {"n_chunks": len(texts), "part": PART}
    if os.path.exists(fp_file):
        old = json.load(open(fp_file, encoding="utf-8"))
        if old != fp:                    # 语料变了：旧分片作废，全部重算
            print(f"   语料已变化（{old} -> {fp}），重算全部分片")
            for p in range(1000):
                pf = os.path.join(IDX_DIR, f"part_{p:04d}.npy")
                if os.path.exists(pf):
                    np.save(pf, np.zeros((0, 512), dtype=np.float32))
                else:
                    break
    json.dump(fp, open(fp_file, "w", encoding="utf-8"))

    parts: list[np.ndarray] = []
    for p in range(nparts):
        pf = os.path.join(IDX_DIR, f"part_{p:04d}.npy")
        if os.path.exists(pf):
            arr = np.load(pf)
            if arr.shape[0] == len(texts[p * PART:(p + 1) * PART]):
                parts.append(arr)
                continue
        vec = emb.encode_docs(texts[p * PART:(p + 1) * PART], batch=32)
        np.save(pf, vec)
        parts.append(vec)
        print(f'   编码 {p + 1}/{nparts} 分片  '
              f'({min((p + 1) * PART, len(texts))}/{len(texts)} 块)  '
              f'{time.time() - t0:.0f}s', flush=True)
    V = np.vstack(parts).astype(np.float32)
    np.save(os.path.join(IDX_DIR, "emb.npy"), V)
    print(f"   向量矩阵 {V.shape}  用时 {time.time() - t0:.0f}s")

    # 检索质量自检：拿"顺丰单票收入"试一下
    q = "顺丰控股的单票收入是多少"
    qv = emb.encode_query(q)
    sim = V @ qv
    top = np.argsort(-sim)[:3]
    print("   自检 query:", q)
    for i in top:
        print(f"     {sim[i]:.3f}  {docs[i]['company']} | {docs[i]['section']}"
              f" | P{docs[i]['page_start']}")

    n = len(docs)
    meta = {
        "n_chunks": n,
        "companies": sorted({d["company"] for d in docs}),
        "reports": sorted({f'{d["company"]}·{d["kind"]}' for d in docs}),
        "embedder": getattr(emb, "name", type(emb).__name__),
        "embed_dim": int(V.shape[1]),
        "bm25": bm25_impl,
        "tokenizer": tok_impl,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    json.dump(meta, open(os.path.join(IDX_DIR, "index_meta.json"), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n==== 索引完成 ====")
    print(json.dumps({k: v for k, v in meta.items() if k != "reports"},
                     ensure_ascii=False, indent=1))


class _BM25:
    """rank_bm25 不可用时的自实现 BM25（与 Okapi 同公式）"""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5,
                 b: float = 0.75):
        self.corpus = corpus
        self.k1, self.b = k1, b
        from collections import Counter
        self.tf = [Counter(d) for d in corpus]
        self.len = np.array([len(d) for d in corpus], dtype=np.float32)
        self.avg = float(self.len.mean()) if len(corpus) else 1.0
        df = Counter()
        for d in corpus:
            df.update(set(d))
        N = len(corpus)
        self.idf = {w: float(np.log(1 + (N - c + 0.5) / (c + 0.5)))
                    for w, c in df.items()}

    def get_scores(self, query: list[str]) -> np.ndarray:
        s = np.zeros(len(self.corpus), dtype=np.float32)
        for w in query:
            if w not in self.idf:
                continue
            idf = self.idf[w]
            for i, tf in enumerate(self.tf):
                f = tf.get(w, 0)
                if not f:
                    continue
                s[i] += idf * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return s


if __name__ == "__main__":
    sys.exit(main())
