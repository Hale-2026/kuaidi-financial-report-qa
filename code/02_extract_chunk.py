#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
02_extract_chunk.py —— 作业3-A 第二步：提取文字、表格按行列还原、切块带元数据

做法：
  1) PyMuPDF 逐页取文本块（保留阅读顺序）
  2) page.find_tables() 检测表格 -> 按行列还原成 Markdown 管道表
  3) 表格区域的原始文字块剔除，改由结构化表格替代（避免同一内容进两遍、且避免串行错位）
  4) 按 y 坐标把「正文段」与「表格块」重新排序，还原页面阅读顺序
  5) 去页眉页脚（跨页高频重复行 + 纯页码行）
  6) 章节识别：第X节（限定出现在页面顶部、且节号单调不减，以排除目录）
                 以及节内 一、二、三 级小标题
  7) 切块：先按章节分段，段内按段落/句末边界累积到 ~700 字，重叠 ~100 字
         表格块整体成块（绝不在表格中间切断）

输出：
  data/text/{basename}.txt          逐页纯文本（含页码标记），便于人工核对
  data/chunks/chunks.jsonl          全部知识块
  data/chunks/stats.json            统计
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
from collections import Counter

import pymupdf
import zhconv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_DIR = os.path.join(ROOT, "data", "pdfs")
TXT_DIR = os.path.join(ROOT, "data", "text")
CHUNK_DIR = os.path.join(ROOT, "data", "chunks")
os.makedirs(TXT_DIR, exist_ok=True)
os.makedirs(CHUNK_DIR, exist_ok=True)

# ------------------------------------------------------------------ 参数
TARGET = 700        # 目标块长（字符）
OVERLAP = 100       # 相邻块重叠
MAX_CHUNK = 1100    # 硬上限

SEC_RE = re.compile(r"^第\s*([一二三四五六七八九十]+)\s*节[\s:：]*(.{0,40})$")
SUB_RE = re.compile(r"^([一二三四五六七八九十]+)\s*、\s*(.{0,40})$")
CN_NUM = "零一二三四五六七八九十"

# 港股年报/中期报告的章节体例与 A 股不同（无「第X节」），用已知标题词表识别
HK_SECTIONS = [
    "财务摘要", "财务资料摘要", "公司资料", "公司信息", "释义",
    "主席报告", "首席执行官报告", "管理层讨论与分析", "管理层讨论及分析",
    "业务回顾", "经营回顾", "行业概览", "重大事项", "董事及高级管理人员",
    "董事及主要行政人员", "董事会报告", "企业管治报告", "企业管治",
    "环境、社会及管治", "环境、社会及管治报告", "审阅报告", "独立核数师报告",
    "独立核数师审阅报告", "综合损益表", "综合财务状况表", "综合现金流量表",
    "综合权益变动表", "财务报表附注", "主要会计政策", "其他资料", "财务资料",
]


def cn2int(s: str) -> int:
    s = s.strip()
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + CN_NUM.index(s[1])
    if "十" in s:
        a, b = s.split("十")
        n = CN_NUM.index(a) * 10
        return n + (CN_NUM.index(b) if b else 0)
    return CN_NUM.index(s)


def cell(v) -> str:
    """单元格文本归一化。

    中文财报表格的单元格内换行绝大多数是**排版折行**，不是语义换行：
      「归属于上市公\\n司股东的净利\\n润（元）」 -> 应当拼回「归属于上市公司股东的净利润（元）」
      「25,084,468,028\\n.72」                 -> 应当拼回「25,084,468,028.72」
    因此这里直接拼接、不插空格；随后再修掉少数被拆开的小数点。
    """
    if v is None:
        return ""
    s = str(v).replace("\r", "")
    s = re.sub(r"[ \t]*\n[ \t]*", "", s)       # 折行直接拼接
    s = re.sub(r"(\d)\s*\.\s*(\d)", r"\1.\2", s)   # 修 028 .72 -> 028.72
    s = re.sub(r"[ \t]{2,}", " ", s).strip()
    return s.replace("|", "/")


def table_to_md(rows: list[list]) -> str:
    """把表格还原成行列对齐的 Markdown 管道表

    额外处理合并单元格造成的「空列」：整列为空的列直接删掉，
    否则一张 5 列表会被还原成 19 列，检索时噪声很大。
    """
    rows = [[cell(c) for c in r] for r in rows]
    rows = [r for r in rows if any(x for x in r)]
    if not rows:
        return ""
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    # 删掉整列为空的列（至少保留 2 列，避免把单列误删成空表）
    keep = [j for j in range(w) if any(r[j] for r in rows)]
    if len(keep) >= 2 and len(keep) < w:
        rows = [[r[j] for j in keep] for r in rows]
    w = len(rows[0])
    head, body = rows[0], rows[1:]
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join(["---"] * w) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def extract_page(page) -> list[tuple[float, str, bool]]:
    """返回 [(y, 文本, 是否表格)]，已按 y 排序"""
    items: list[tuple[float, str, bool]] = []

    # ---- 表格
    tboxes: list[tuple[float, float, float, float]] = []
    try:
        tabs = page.find_tables()
    except Exception:                                             # noqa: BLE001
        tabs = None
    if tabs:
        for t in tabs.tables:
            try:
                md = table_to_md(t.extract())
            except Exception:                                     # noqa: BLE001
                md = ""
            if md:
                y0 = t.bbox[1]
                items.append((y0, "[TABLE]\n" + md, True))
                tboxes.append(t.bbox)

    # ---- 正文块（剔除落在表格框内的）
    for b in page.get_text("blocks", sort=True):
        x0, y0, x1, y1, txt = b[0], b[1], b[2], b[3], b[4]
        if not txt.strip():
            continue
        inside = any(x0 >= tb[0] - 2 and y0 >= tb[1] - 2 and
                     x1 <= tb[2] + 2 and y1 <= tb[3] + 2 for tb in tboxes)
        if inside:
            continue
        items.append((y0, txt.strip(), False))

    items.sort(key=lambda z: (round(z[0], 1), z[2]))
    return items


def clean_lines(pages_lines: list[list[str]]) -> list[list[str]]:
    """去页眉页脚：跨页高频重复行 + 纯页码"""
    cnt = Counter()
    for lines in pages_lines:
        for l in set(lines):
            cnt[l.strip()] += 1
    n = max(1, len(pages_lines))
    freq = {l for l, c in cnt.items() if c >= max(3, n * 0.25) and len(l) < 90}
    out = []
    for lines in pages_lines:
        keep = []
        for l in lines:
            s = l.strip()
            if not s:
                continue
            if s in freq:
                continue
            if re.fullmatch(r"[-—\s]*\d{1,4}[-—\s]*", s):     # 页码
                continue
            if re.fullmatch(r"第\s*\d+\s*页", s):
                continue
            keep.append(s)
        out.append(keep)
    return out


def split_long(text: str, target: int, overlap: int) -> list[str]:
    """按句末/段落边界切分长文本，带重叠"""
    if len(text) <= target:
        return [text] if text.strip() else []
    parts, buf = [], ""
    for seg in re.split(r"(?<=[。！？；\n])", text):
        if not seg:
            continue
        if len(buf) + len(seg) > target and buf:
            parts.append(buf)
            buf = (buf[-overlap:] if overlap and len(buf) > overlap else "") + seg
        else:
            buf += seg
        while len(buf) > MAX_CHUNK:                # 极端长句兜底
            parts.append(buf[:MAX_CHUNK])
            buf = buf[MAX_CHUNK - overlap:]
    if buf.strip():
        parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


# --------------------------------------------------------------- 单份报告
def process(pdf: str, meta: dict) -> tuple[list[dict], list[str]]:
    is_hk = meta.get("exchange") == "HKEX"
    doc = pymupdf.open(pdf)
    npages = doc.page_count
    page_blocks: list[tuple[int, float, str, bool]] = []
    raw_lines: list[list[str]] = []

    for i in range(npages):
        page = doc[i]
        items = extract_page(page)
        lines = []
        for y, txt, is_tab in items:
            if is_hk:                       # 港交所文件为繁体，统一转简体
                txt = zhconv.convert(txt, "zh-cn")
            if is_tab:
                page_blocks.append((i + 1, y, txt, True))
                lines.append(txt)
            else:
                for ln in txt.split("\n"):
                    if ln.strip():
                        lines.append(ln)
                        page_blocks.append((i + 1, y, ln.strip(), False))
        raw_lines.append(lines)
    doc.close()

    # 去页眉页脚
    cleaned = clean_lines(raw_lines)
    kept = [set(l) for l in cleaned]
    filt: list[tuple[int, float, str, bool]] = []
    seen: Counter = Counter()
    for pno, y, txt, is_tab in page_blocks:
        if is_tab:
            filt.append((pno, y, txt, True))
            continue
        idx = pno - 1
        if idx < len(cleaned) and txt in kept[idx] and seen[(idx, txt)] == 0:
            seen[(idx, txt)] += 1
            filt.append((pno, y, txt, False))

    # ---------------- 分章节 + 切块
    chunks: list[dict] = []
    cur_sec, cur_secno = "正文/未标注章节", 0
    buf: list[str] = []
    buf_pages: list[int] = []
    buf_tab = False
    seq = 0
    sub = ""

    def flush():
        nonlocal buf, buf_pages, buf_tab, seq
        if not buf:
            return
        text = "\n".join(buf).strip()
        if len(text) < 12:
            buf, buf_pages, buf_tab = [], [], False
            return
        if buf_tab:
            pieces = [text]
            pgs = (buf_pages[0], buf_pages[-1])
        else:
            pieces = split_long(text, TARGET, OVERLAP)
            pgs = (buf_pages[0], buf_pages[-1])
        for pc in pieces:
            seq += 1
            chunks.append({
                "chunk_id": f'{meta["code"]}_{meta["kind"]}_{seq:04d}',
                "code": meta["code"], "company": meta["name"],
                "exchange": meta["exchange"], "segment": meta["segment"],
                "kind": meta["kind"], "report_period": meta["kind"],
                "section": cur_sec, "section_no": cur_secno, "subsection": sub,
                "page_start": pgs[0], "page_end": pgs[1],
                "n_chars": len(pc), "has_table": buf_tab,
                "text": pc,
            })
        buf, buf_pages, buf_tab = [], [], False

    last_y = None
    for pno, y, txt, is_tab in filt:
        # 换页时若上一块是表格则先落盘（表格不跨页拼接）
        if last_y is not None and pno != last_y and buf_tab:
            flush()
        last_y = pno

        if not is_tab:
            m = SEC_RE.match(txt)
            if m:
                no = cn2int(m.group(1))
                # 排除目录行（带点线/结尾页码）
                if ("...." not in txt) and no >= cur_secno and y < 260:
                    flush()
                    cur_secno, cur_sec = no, txt.strip()
                    sub = ""
                    continue
            if is_hk and len(txt) <= 30 and not txt.endswith(("。", "，", "：")) \
                    and y < 300:
                norm = re.sub(r"[\s　]+", "", txt)
                for h in sorted(HK_SECTIONS, key=len, reverse=True):
                    if norm == h or norm.startswith(h):
                        flush()
                        cur_secno, cur_sec = cur_secno, txt.strip()
                        sub = ""
                        break
            ms = SUB_RE.match(txt)
            if ms and len(txt) < 45 and not txt.endswith("。") and buf_pages:
                sub = txt.strip()

        if is_tab:
            if buf and not buf_tab:
                flush()
            buf.append(txt)
            buf_pages.append(pno)
            buf_tab = True
            # 表格单独成块
            flush()
        else:
            if buf_tab:
                flush()
            buf.append(txt)
            buf_pages.append(pno)

    flush()

    # 写出逐页纯文本，便于人工核对
    lines_out = []
    for i, lines in enumerate(cleaned, 1):
        lines_out.append(f"\n===== [PAGE {i}] =====")
        lines_out.extend(lines)
    return chunks, lines_out


SHARD_DIR = os.path.join(CHUNK_DIR, "shards")
os.makedirs(SHARD_DIR, exist_ok=True)


def _worker(args):
    m, pdf_dir, txt_dir = args
    base = os.path.splitext(m["file"])[0]
    t0 = time.time()
    chunks, lines_out = process(os.path.join(pdf_dir, m["file"]), m)
    with open(os.path.join(txt_dir, base + ".txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines_out))
    # 每份报告单独存一个分片：断点续跑时以「分片是否存在」为准，
    # 这样旧版本生成的 .txt 不会被误当成已完成（分片不存在 -> 重跑）
    json.dump(chunks, open(os.path.join(SHARD_DIR, base + ".json"), "w",
                           encoding="utf-8"), ensure_ascii=False)
    tabs = sum(1 for c in chunks if c["has_table"])
    return (f'[{m["code"]} {m["name"]}] {m["kind"]} -> {len(chunks)} 块'
            f'（表格块 {tabs}）{time.time() - t0:.0f}s', chunks)


def main() -> None:
    import concurrent.futures as cf

    man = json.load(open(os.path.join(ROOT, "data", "manifest.json"),
                         encoding="utf-8"))
    ok = [m for m in man if m.get("ok")]
    # 断点续跑：已有分片的报告直接复用
    todo = [m for m in ok
            if not os.path.exists(os.path.join(
                SHARD_DIR, os.path.splitext(m["file"])[0] + ".json"))]

    # PDF 原件缺席时提前给明确指引，别让它崩在 ProcessPool 的 traceback 里。
    # 触发场景：干净 clone（仓库自带 manifest.json 作来源凭证，但不含 207 MB PDF）
    # 时若跳过第 1 步直接跑这里，报错会是一大段并发栈，看不懂。
    absent = [m for m in todo
              if not os.path.exists(os.path.join(PDF_DIR, m["file"]))]
    if absent:
        print(f"!! data/pdfs/ 下缺 {len(absent)}/{len(todo)} 份报告的 PDF 原件，无法提取。")
        print(f"   例如：{absent[0]['file']}")
        print("   先跑第 1 步下载：python code/01_download.py")
        print("   （或直接 zsh run_all.sh，会按顺序自动跑完整流程）")
        sys.exit(1)
    if len(todo) < len(ok):
        print(f"断点续跑：跳过已完成 {len(ok) - len(todo)} 份，"
              f"本次处理 {len(todo)} 份")
    jobs = [(m, PDF_DIR, TXT_DIR) for m in todo]
    # 并发数默认 3：pymupdf 处理几十 MB 的年报时单进程内存占用很高，
    # 并发过高会把整机拖进内存压力（实测 6 并发会导致系统卡死）
    maxw = max(1, int(os.environ.get("MAXW", "3")))
    if jobs:
        print(f"并发进程数：{maxw}（可用 MAXW 环境变量调整）")
        with cf.ProcessPoolExecutor(max_workers=maxw) as ex:
            for i, (msg, _) in enumerate(ex.map(_worker, jobs), 1):
                print(f"{i:2d}/{len(jobs)}  {msg}", flush=True)

    all_chunks: list[dict] = []
    for fn in sorted(os.listdir(SHARD_DIR)):
        if fn.endswith(".json"):
            all_chunks += json.load(open(os.path.join(SHARD_DIR, fn),
                                         encoding="utf-8"))

    all_chunks.sort(key=lambda c: (c["code"], c["kind"], c["page_start"],
                                   c["chunk_id"]))
    with open(os.path.join(CHUNK_DIR, "chunks.jsonl"), "w",
              encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    ln = [c["n_chars"] for c in all_chunks]
    stats = {
        "reports": len(ok),
        "chunks": len(all_chunks),
        "table_chunks": sum(1 for c in all_chunks if c["has_table"]),
        "companies": len({c["code"] for c in all_chunks}),
        "avg_chars": round(statistics.mean(ln), 1) if ln else 0,
        "median_chars": statistics.median(ln) if ln else 0,
        "min_chars": min(ln) if ln else 0,
        "max_chars": max(ln) if ln else 0,
        "by_section": dict(Counter(c["section"] for c in all_chunks)
                           .most_common()),
    }
    json.dump(stats, open(os.path.join(CHUNK_DIR, "stats.json"), "w",
                          encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n==== 切块完成 ====")
    print(json.dumps({k: v for k, v in stats.items() if k != "by_section"},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
