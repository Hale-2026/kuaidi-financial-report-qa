#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py —— 作业3-A 问答页面（Flask）

启动：
  python code/app.py            # 默认 http://127.0.0.1:8000
  python code/app.py --port 8899

接口：
  GET  /               问答页面
  GET  /api/meta       可选公司 / 报告类型 / 索引统计
  POST /api/ask        {"q": "...", "mode": "hybrid|bm25|vector|cover",
                        "topk": 6, "companies": [...], "kinds": [...]}
                       mode=cover 为按公司分组召回（跨公司全景题专用）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module                            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")

app = Flask(__name__, static_folder=None)
ENGINE = None


def engine():
    global ENGINE
    if ENGINE is None:
        ENGINE = import_module("04_ask").Engine()
    return ENGINE


@app.route("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.route("/api/meta")
def meta():
    e = engine()
    return jsonify({
        "companies": sorted({d["company"] for d in e.docs}),
        "segments": sorted({d["segment"] for d in e.docs}),
        "kinds": ["2025年年度报告", "2026年半年度报告"],
        "sections": sorted({d["section"] for d in e.docs}),
        "n_chunks": e.meta["n_chunks"],
        "index": e.meta,
    })


@app.route("/api/ask", methods=["POST"])
def ask():
    p = request.get_json(force=True) or {}
    q = (p.get("q") or "").strip()
    if not q:
        return jsonify({"error": "empty query"}), 400
    mode = p.get("mode") or "hybrid"
    topk = int(p.get("topk") or 6)
    companies = p.get("companies") or None
    kinds = p.get("kinds") or None
    if companies == []:
        companies = None
    if kinds == []:
        kinds = None
    r = engine().answer(q, topk=topk, mode=mode,
                        companies=companies, kinds=kinds)
    return jsonify(r)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    print(f"问答页面: http://{a.host}:{a.port}")
    app.run(host=a.host, port=a.port, debug=False, threaded=True)
