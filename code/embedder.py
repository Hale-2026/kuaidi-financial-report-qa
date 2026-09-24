#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
embedder.py —— 中文向量编码器（BGE-small-zh-v1.5 / ONNX，CPU 推理，无需 torch）

- 分词：tokenizers 直接读 tokenizer.json（不依赖 transformers）
- 池化：BGE 系列用 [CLS] 句向量 + L2 归一化（归一化后内积即余弦相似度）
- 查询侧加 BGE 官方中文检索指令，文档侧不加（v1.5 官方推荐用法）
- 若 onnxruntime 不可用，自动降级为 HashingTF-IDF + SVD 稠密向量（纯 sklearn）
"""
from __future__ import annotations

import os

import numpy as np

QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
MAXLEN = 512


class BGEEmbedder:
    """BGE-small-zh-v1.5 ONNX 编码器"""

    name = "bge-small-zh-v1.5(ONNX)"

    def __init__(self, model_dir: str, threads: int | None = None):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        if threads is None:
            threads = max(2, min(8, (os.cpu_count() or 4)))
        self.tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        self.tok.enable_truncation(max_length=MAXLEN)
        self.tok.enable_padding(pad_id=0, pad_token="[PAD]")
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(
            os.path.join(model_dir, "onnx", "model.onnx"), so,
            providers=["CPUExecutionProvider"])
        self.inames = {i.name for i in self.sess.get_inputs()}
        self.dim = 512

    def _run(self, texts: list[str], batch: int = 16) -> np.ndarray:
        vecs = []
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            encs = self.tok.encode_batch([t.replace("\n", " ")[:1500]
                                          for t in chunk])
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            am = np.array([e.attention_mask for e in encs], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": am}
            if "token_type_ids" in self.inames:
                feed["token_type_ids"] = np.zeros_like(ids)
            out = self.sess.run(None, feed)[0]          # (B, L, H)
            vecs.append(out[:, 0, :])                   # [CLS] 池化
        v = np.vstack(vecs).astype(np.float32)
        n = np.linalg.norm(v, axis=1, keepdims=True)
        return v / np.clip(n, 1e-9, None)

    def encode_docs(self, texts: list[str], batch: int = 16) -> np.ndarray:
        return self._run(texts, batch)

    def encode_query(self, query: str) -> np.ndarray:
        return self._run([QUERY_INSTRUCTION + query], batch=1)[0]


class TfidfSvdEmbedder:
    """兜底方案：字符 n-gram TF-IDF -> TruncatedSVD 稠密向量（LSA）"""

    name = "tfidf-svd(512)"

    def __init__(self, dim: int = 512):
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.pipeline import make_pipeline

        self.dim = dim
        self._pipe = make_pipeline(
            TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3),
                            min_df=2, max_features=200_000,
                            sublinear_tf=True),
            TruncatedSVD(n_components=dim, random_state=42))
        self.fitted = False

    def encode_docs(self, texts: list[str], **_) -> np.ndarray:
        m = self._pipe.fit_transform(texts).astype(np.float32)
        self.fitted = True
        return _l2(m)

    def encode_query(self, query: str) -> np.ndarray:
        m = self._pipe.transform([query]).astype(np.float32)
        return _l2(m)[0]


def _l2(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.clip(n, 1e-9, None)


def build_embedder(model_dir: str):
    try:
        return BGEEmbedder(model_dir)
    except Exception as e:                                        # noqa: BLE001
        print(f"  [warn] BGE-ONNX 不可用（{type(e).__name__}: {e}），"
              f"降级 TF-IDF+SVD")
        return TfidfSvdEmbedder()
