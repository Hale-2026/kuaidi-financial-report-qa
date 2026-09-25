#!/usr/bin/env python3
"""打包作业交付物。

默认打「交付包」——含报告 PDF / 评测台账 / 截图 / 代码 / 下载清单，
把几百 MB 的 PDF 原件与索引留在本地（可由 01_download.py + manifest.json 复现）。

    python code/package.py             # 交付包（约 4 MB）
    python code/package.py --code-only # 只打纯代码仓库包（约 150 KB）
    python code/package.py --with-data # 额外打含原始 PDF 的数据包（约 210 MB，慢）
    python code/package.py --date 20260924   # 指定包名日期（默认今天）

产物落在 dist/ 下。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")

# (包内相对路径, 本地绝对路径) —— 显式列清单，避免把 .part / 日志 / 缓存打进去
LIGHT: list[str] = [
    "README.md",
    "run_all.sh",
    "code/01_download.py",
    "code/02_extract_chunk.py",
    "code/03_build_index.py",
    "code/04_ask.py",
    "code/05_eval.py",
    "code/embedder.py",
    "code/app.py",
    "code/package.py",
    "code/make_report.py",
    "code/bootstrap_deps.sh",
    "code/shot.sh",
    "code/requirements.txt",
    "code/eval_questions.json",
    "web/index.html",
    "output/一页结论.md",
    "output/一页结论.pdf",
    "output/作业3A_报告.pdf",
    "output/eval_table.md",
    "output/eval_runs.json",
    "output/eval_scores.json",
    "output/工作流程说明.md",
    "data/manifest.json",
    "data/chunks/stats.json",
]
GLOB_DIRS = ["shots"]           # 整个目录打进去
DATA_PARTS = ["data/pdfs"]      # --with-data 时才打

INTRO = """# 交付说明

本包为「作业 3-A：快递物流行业财报问答知识库」的交付物。

> **只需提交这一个 zip 包即可**（约 3 MB）。包内含正式报告 PDF、评测台账、
> 页面截图、全部源代码与下载清单；207 MB 的财报 PDF 原件不在包内，但可由
> `data/manifest.json` 记录的交易所来源 URL 一键复现（见第二节）。

## 一、包内有什么

| 目录 / 文件 | 内容 |
|---|---|
| `output/作业3A_报告.pdf` | **正式报告（16 页）**：封面 + 一页结论 + 逐题评测台账，直接可交 |
| `output/一页结论.pdf` | 一页结论单独成册（3 页），对应作业要求的「一页结论」 |
| `output/一页结论.md` | 上述结论的 Markdown 源文件（便于修改） |
| `output/eval_table.md` | 10 道题 × 4 种检索模式的**逐题评测台账**（含每题答案与逐句出处） |
| `output/eval_runs.json` | 每题的**完整召回明细**（召回块、页码、得分），可逐条复查 |
| `output/eval_scores.json` | 命中率汇总指标 |
| `output/工作流程说明.md` | 六段流水线的完整说明（含所有参数取值与故障排查表） |
| `shots/` | 问答页面截图 9 张（含 BM25 / 向量 / 混合 / 分组召回四种模式对比） |
| `code/` | 全部源代码（下载 → 提取切块 → 建索引 → 检索问答 → 评测 → 出报告） |
| `web/index.html` | 问答页面前端 |
| `data/manifest.json` | **34 份财报的下载清单**（来源平台、URL、大小、SHA1、公告日） |
| `data/chunks/stats.json` | 切块统计（18,596 块，其中表格块 8,321） |

## 二、原始数据不在包内（但可一键复现）

34 份财报 PDF 原件共 **207 MB**，索引 99 MB，为控制提交体积未打包。
它们**不是必需提交项**——`data/manifest.json` 记录了每份文件的交易所来源 URL
与 SHA1，跑一条命令即可完整复现：

```bash
# 1) 装依赖（含 jieba / zhconv 这类只有 sdist 的包）
zsh code/bootstrap_deps.sh
# 2) 全流程一键跑完（下载 → 提取 → 建索引 → 评测 → 截图）
zsh run_all.sh
```

如需连同 PDF 原件一起提交，在本目录执行：

```bash
python code/package.py --with-data      # 额外产出 dist/作业3A_数据原件.zip
```

## 三、数据来源（作业要求「从交易所网站下载」）

| 市场 | 来源 | 说明 |
|---|---|---|
| 深市 | 深交所官网 `szse.cn` | 交易所官方公告接口 |
| 沪市 | 巨潮资讯网 `cninfo.com.cn` | 证监会指定法定信息披露平台 |
| 港股 | 港交所披露易 `hkexnews.hk` | 港交所官方披露平台 |

样本：**17 家公司 × 2 类报告 = 34 份**（2025 年年度报告 + 2026 年半年度报告）。
每家公司的来源 URL 与 SHA1 见 `data/manifest.json`，可逐份回溯核验。

## 四、评测核心结论

| 检索模式 | 首次评测 | 修正判卷口径后 | 加分组召回后 |
|---|---|---|---|
| BM25（仅词法） | 70% | 90% | — |
| 向量（仅语义） | 60% | 80% | — |
| hybrid（RRF 融合） | 60% | 90% | — |
| **cover（hybrid + 分组召回）** | —（当时无此模式） | — | **100%** |

> ⚠ 这是**关键词组证据命中率**（宽松上界），应读作"证据全部召回"，
> 不等价于"10 题全部答对"。详见 `output/一页结论.md` 的诚实清单。
>
> 「首次评测」= 2026-09-24 检索侧修复前的实测。注意 `输出/台账/eval_table.md`
> 里那列「v1 判卷口径@Top5」（hybrid 70% / bm25 80%）是**当前检索按旧判卷规则重算**，
> 与本列差 10pt 属正常（差的是 Q03，词典补入跨市场术语后转命中）；两者是不同的量，
> 台账第 1 节已并列成两列可逐格对照。
"""


CODE_INTRO = """# 代码仓库说明

「作业 3-A：快递物流行业财报问答知识库」的**源代码仓库**。

## 目录结构

```
code/                     流水线脚本
  ├── 01_download.py      ① 多源下载年报/半年报全文（深交所 / 巨潮 / 港交所）
  ├── 02_extract_chunk.py ② 提取文字 + 表格按行列还原 + 切块（带 公司/章节/页码）
  ├── 03_build_index.py   ③ 建 BM25 + 向量双索引（jieba 缺失自动降级，不中断）
  ├── 04_ask.py           ④ 混合检索（RRF + 分组召回）+ 抽取式带出处答案
  ├── 05_eval.py          ⑤ 10 题 × 4 种检索模式逐题评测
  ├── embedder.py         BGE-small-zh-v1.5 ONNX 编码器（CPU，不依赖 torch）
  ├── app.py              Flask 问答页面后端
  ├── make_report.py      Markdown → PDF 报告（Chrome 无头打印）
  ├── package.py          打交付包
  ├── bootstrap_deps.sh   依赖自检 + 补装（含 jieba/zhconv 的 sdist 兜底）
  ├── shot.sh             Chrome 无头截图
  ├── download_cninfo.py  早期下载原型（正式流程请用 01_download.py）
  ├── eval_questions.json 评测题目与标准答案证据
  └── requirements.txt    依赖清单
web/index.html            问答页面前端
data/manifest.json        34 份报告的来源 URL / 大小 / SHA1 / 公告日
data/chunks/stats.json    切块统计
run_all.sh                一键跑完 7 步（幂等、断点续跑）
README.md                 完整说明：设计决策、评测结果、已知限制
```

## 怎么跑

```bash
zsh code/bootstrap_deps.sh    # 装依赖
zsh run_all.sh                # 下载 → 提取 → 建索引 → 评测 → 截图 → 出报告 → 打包
```

数据（34 份财报 PDF / 切块 / 索引 / 向量模型）体积大，不在本仓库内；
首次运行 `run_all.sh` 会自动下载与重建，来源 URL 与 SHA1 见 `data/manifest.json`
（本仓库已附该清单，每份报告都可逐条回溯到交易所原文）。

## 依赖

- **必需**：`pymupdf` `numpy` `onnxruntime` `tokenizers` `requests` `bs4`
  `lxml` `tqdm` `flask` `zhconv`
- **可选**（缺失有降级方案）：`jieba`（分词 → 内置二元切分）· `rank_bm25`
  （→ 自实现 BM25）· `scikit-learn` · `openpyxl`
"""


def zipdir(zf: zipfile.ZipFile, sub: str, skip: tuple[str, ...] = (),
           skip_files: tuple[str, ...] = ()) -> int:
    n = 0
    base = os.path.join(ROOT, sub)
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__" and d not in skip]
        for f in files:
            if f.startswith(".") or f.endswith(".log") or f in skip_files:
                continue
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, ROOT)
            zf.write(full, rel)
            n += 1
    return n


def build(name: str, extra_dirs: list[str], intro: str) -> str:
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, name)
    if os.path.exists(out):
        os.remove(out)
    n, miss = 0, []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr("交付说明.md", intro)
        n += 1
        for rel in LIGHT:
            full = os.path.join(ROOT, rel)
            if os.path.exists(full):
                zf.write(full, rel)
                n += 1
            else:
                miss.append(rel)
        for g in GLOB_DIRS:
            n += zipdir(zf, g)
        for g in extra_dirs:
            n += zipdir(zf, g)
    mb = os.path.getsize(out) / 1048576
    print(f"  → {os.path.relpath(out, ROOT)}   {n} 个文件   {mb:.1f} MB")
    if miss:
        print(f"  !! 缺失（已跳过）: {', '.join(miss)}")
    return out


def build_code(name: str) -> str:
    """纯代码仓库包 —— 只含源码，不含报告/数据/截图。

    目录结构与本地仓库一致（code/ + web/ + README + run_all.sh + .gitignore），
    解压即是可运行的代码仓库。
    """
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, name)
    if os.path.exists(out):
        os.remove(out)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr("代码仓库说明.md", CODE_INTRO)
        n += 1
        for rel in ("README.md", "run_all.sh", ".gitignore", ".env.example"):
            full = os.path.join(ROOT, rel)
            if os.path.exists(full):
                zf.write(full, rel)
                n += 1
        n += zipdir(zf, "code", skip=("legacy",),
                    skip_files=("push_github.sh",))   # legacy/ 与推送工具都非作业代码
        n += zipdir(zf, "web")
        # 与 git 仓库内容对齐：下载清单与切块统计也是「代码可复现的凭证」，一并附上
        for rel in ("data/manifest.json", "data/chunks/stats.json"):
            full = os.path.join(ROOT, rel)
            if os.path.exists(full):
                zf.write(full, rel)
                n += 1
    mb = os.path.getsize(out) / 1048576
    print(f"  → {os.path.relpath(out, ROOT)}   {n} 个文件   {mb:.2f} MB")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-data", action="store_true",
                    help="额外打一份含原始 PDF 的数据包")
    ap.add_argument("--code-only", action="store_true",
                    help="只打纯代码仓库包（不打交付包）")
    ap.add_argument("--date", default=None,
                    help="包名日期 YYYYMMDD（默认今天）")
    a = ap.parse_args()

    day = a.date or dt.date.today().strftime("%Y%m%d")
    print("打包 ...")
    if a.code_only:
        code = build_code(f"作业3A_代码仓库_{day}.zip")
        print(f"\n代码仓库包：{os.path.relpath(code, ROOT)}")
        return 0

    light = build(f"作业3A_交付包_{day}.zip", [], INTRO)
    build_code(f"作业3A_代码仓库_{day}.zip")
    if a.with_data:
        data_intro = ("# 数据原件（34 份财报 PDF）\n\n"
                      "配合轻量交付包使用。来源 URL 与 SHA1 见 "
                      "`data/manifest.json`。\n")
        build(f"作业3A_数据原件_{day}.zip", DATA_PARTS, data_intro)

    total = sum(os.path.getsize(os.path.join(DIST, f))
                for f in os.listdir(DIST)) / 1048576
    print(f"\ndist/ 合计 {total:.1f} MB")
    print(f"交付包：{os.path.relpath(light, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
