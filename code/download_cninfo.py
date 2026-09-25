#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从巨潮资讯网下载 A 股快递物流公司年报/半年报 —— 早期原型，保留备查

⚠️ 交付流程用的下载脚本是 code/01_download.py，不是本文件。
   01_download.py 覆盖 【深交所官网 + 巨潮资讯网 + 港交所披露易】三源、
   17 家公司 × 2 类报告 = 34 份，并产出 data/manifest.json
   （逐份记录 来源 / URL / 大小 / SHA1 / 公告日期），由 run_all.sh 第 ① 步调用。

   本脚本是它的前身原型，与后续流程不衔接，具体差异：
     · 公司清单停留在早期 11 家（含已于 2026 年私有化退市的德邦股份 603056）
     · 只走巨潮单一数据源，未接入深交所官网与港交所披露易
     · 输出到 reports/（01_download.py 输出到 data/pdfs/）
     · 不写 manifest，无法与 02_extract_chunk.py 及后续步骤对接
     · 仓库内无任何脚本引用它
   保留仅为呈现完整开发过程，直接跑它不会产出可用的交付数据。
"""
import requests, time, os, json, sys

BASE = "http://www.cninfo.com.cn"
# 输出到「本脚本上一级目录/reports」，不写死本机路径
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
os.makedirs(OUT, exist_ok=True)

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "*/*",
})

# (code, name, column, plate)
COMPANIES = [
    ("002352", "顺丰控股", "szse", "sz"),
    ("600233", "圆通速递", "sse", "sh"),
    ("002120", "韵达股份", "szse", "sz"),
    ("002468", "申通快递", "szse", "sz"),
    ("603056", "德邦股份", "sse", "sh"),
    ("601598", "中国外运", "sse", "sh"),
    ("601156", "东航物流", "sse", "sh"),
    ("603128", "华贸物流", "sse", "sh"),
    ("600787", "中储股份", "sse", "sh"),
    ("600057", "厦门象屿", "sse", "sh"),
    ("002010", "传化智联", "szse", "sz"),
]

def get_orgid(code, name):
    r = S.post(f"{BASE}/new/information/topSearch/query",
               data={"keyWord": code, "maxNum": 10}, timeout=20)
    for it in r.json():
        if it.get("code") == code:
            return it["orgId"]
    raise RuntimeError(f"orgId not found for {name}")

def query_ann(code, orgid, column, plate, secid, se_date):
    data = {
        "pageNum": 1, "pageSize": 30, "column": column, "tabName": "fulltext",
        "plate": plate, "stock": f"{code},{orgid}", "searchkey": "",
        "secid": "", "category": secid, "trade": "", "seDate": se_date,
        "sortName": "", "sortType": "", "isHLtitle": "true",
    }
    r = S.post(f"{BASE}/new/hisAnnouncement/query", data=data, timeout=30)
    return r.json().get("announcements") or []

def download(url, path):
    if os.path.exists(path) and os.path.getsize(path) > 100_000:
        print(f"  skip {os.path.basename(path)}")
        return True
    r = S.get(f"{BASE}{url}" if url.startswith("/") else url, timeout=300, stream=True)
    r.raise_for_status()
    with open(path, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
    return os.path.getsize(path) > 100_000

def main():
    manifest = []
    # 2025年报(2026年发布) + 2026半年报(2026年发布)
    targets = [
        ("年报", "category_ndbg_szsh", "2025年年度报告", "2026-01-01~2026-09-23"),
        ("半年报", "category_bndbg_szsh", "2026年半年度报告", "2026-01-01~2026-09-23"),
    ]
    for code, name, column, plate in COMPANIES:
        orgid = get_orgid(code, name)
        print(f"{name} {code} orgid={orgid}")
        for kind, cat, kw, se in targets:
            anns = query_ann(code, orgid, column, plate, cat, se)
            pick = None
            for a in anns:
                title = a["announcementTitle"].replace("<em>", "").replace("</em>", "")
                if kw in title and "英文" not in title and "摘要" not in title and "已取消" not in title:
                    pick = (title, a["adjunctUrl"])
                    break
            if not pick:
                print(f"  !! 未找到 {kind}: {[a['announcementTitle'] for a in anns][:5]}")
                continue
            title, url = pick
            safe = f"{code}_{name}_{kw}.pdf"
            path = os.path.join(OUT, safe)
            ok = download(url, path)
            print(f"  {'OK' if ok else 'FAIL'} {safe} ({os.path.getsize(path)//1024 if os.path.exists(path) else 0} KB)")
            manifest.append({"code": code, "name": name, "market": "A", "kind": kind,
                             "title": title, "file": safe, "ok": ok})
            time.sleep(1.5)
    with open(os.path.join(OUT, "manifest_a.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"done {len(manifest)} entries")

if __name__ == "__main__":
    main()
