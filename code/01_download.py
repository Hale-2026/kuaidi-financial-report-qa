#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
01_download.py —— 作业3-A 第一步：从交易所 / 法定披露平台下载年报、半年报全文

行业：快递物流
公司：14 家 A 股（快递主干 5 家 + 物流延伸 9 家）

下载源（双源，互为交叉验证）：
  1) 深交所官方公告接口  http://www.szse.cn/api/disc/announcement/annList   —— 深市公司
  2) 巨潮资讯网          http://www.cninfo.com.cn/new/hisAnnouncement/query  —— 证监会指定的
                         法定信息披露平台（深交所旗下深圳证券信息有限公司运营），沪深全覆盖

  注：上交所 query.sse.com.cn 接口在本机网络环境下返回空结果（沙箱出网策略），
      故沪市公司统一走巨潮（该平台为沪市公司法定披露渠道，公告原文与上交所一致）。

输出：
  data/pdfs/{代码}_{简称}_{报告类型}.PDF
  data/manifest.json  —— 记录 来源 / URL / 大小 / SHA1 / 公告日期
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime

import requests
from tqdm import tqdm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_DIR = os.path.join(ROOT, "data", "pdfs")
MANIFEST = os.path.join(ROOT, "data", "manifest.json")
os.makedirs(PDF_DIR, exist_ok=True)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---------------------------------------------------------------- 公司清单
# (股票代码, 公司简称, 交易所, 行业细分)
COMPANIES = [
    ("002352", "顺丰控股", "SZSE", "快递"),
    ("600233", "圆通速递", "SSE",  "快递"),
    ("002120", "韵达股份", "SZSE", "快递"),
    ("002468", "申通快递", "SZSE", "快递"),
    # 603056 德邦股份已于 2026 年被京东物流私有化退市（巨潮 delisted=true），换成长久物流
    ("603569", "长久物流", "SSE",  "整车物流"),
    ("601156", "东航物流", "SSE",  "航空货运"),
    ("603128", "华贸物流", "SSE",  "国际货代"),
    ("601598", "中国外运", "SSE",  "综合物流"),
    ("600787", "中储股份", "SSE",  "仓储物流"),
    ("603565", "中谷物流", "SSE",  "内贸集装箱"),
    ("603713", "密尔克卫", "SSE",  "化工物流"),
    ("002010", "传化智联", "SZSE", "公路港物流"),
    ("603871", "嘉友国际", "SSE",  "跨境陆港物流"),
    ("600125", "铁龙物流", "SSE",  "铁路物流"),
    # ---- 港股快递主干（港交所披露易）----
    ("02057", "中通快递", "HKEX", "快递"),
    ("02618", "京东物流", "HKEX", "快递/一体化供应链"),
    ("01519", "极兔速递", "HKEX", "快递"),
]

# 港股：港交所披露易 stockId（由 prefix.do 接口查得）
HK_STOCKID = {"02057": 1000057949, "02618": 1000095726, "01519": 1000205283}

# 报告类型 -> (关键词, 巨潮 category, 公告日期窗口, A股标题正则, 港股标题正则)
# 统一 kind 标签保证跨市场元数据一致：港股「年報」-> 2025年年度报告，
# 港股「中期報告」-> 2026年半年度报告（会计期间同为 1-6 月，可直接对比）
TARGETS = [
    ("2025年年度报告", "category_ndbg_szsh", ("2026-01-01", "2026-06-30"),
     re.compile(r"2025\s*年?年度报告"),
     re.compile(r"(2025|二零二五)\s*年?(年報|年度報告)")),
    ("2026年半年度报告", "category_bndbg_szsh", ("2026-07-01", "2026-09-30"),
     re.compile(r"2026\s*年?半年度报告"),
     re.compile(r"(2026|二零二六)\s*年?(中期報告|半年度報告)")),
]

EXCLUDE = re.compile(r"摘要|英文|English|已取消|取消|更正|补充|问询|回复|意见|公告$|提示性|"
                     r"業績公告|业绩公告|ESG|環境|环境|社會|社会|企業管治|企业管治|"
                     r"月報表|月报表|翌日披露|通函|通函")


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*",
                      "Accept-Language": "zh-CN,zh;q=0.9"})
    return s


def sha1_file(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def fetch_pdf(s: requests.Session, url: str, path: str) -> bool:
    """带断点续传的下载；返回是否成功（>200KB 视为完整财报）"""
    if os.path.exists(path) and os.path.getsize(path) > 200_000:
        return True
    tmp = path + ".part"
    for attempt in range(4):
        try:
            with s.get(url, timeout=600, stream=True) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0)
                with open(tmp, "wb") as f, tqdm(
                        total=total, unit="B", unit_scale=True, ncols=70,
                        desc=os.path.basename(path)[:28], leave=False) as bar:
                    for blk in r.iter_content(1 << 16):
                        f.write(blk)
                        bar.update(len(blk))
            if os.path.getsize(tmp) > 200_000:
                os.replace(tmp, path)
                return True
        except Exception as e:                                    # noqa: BLE001
            print(f"    retry {attempt + 1}/4: {type(e).__name__} {e}")
            time.sleep(2 + 2 * attempt)
    if os.path.exists(tmp):
        os.remove(tmp)
    return False


# ------------------------------------------------------- 源 1：深交所官方接口
def szse_list(s: requests.Session, code: str, d0: str, d1: str) -> list[dict]:
    r = s.post("http://www.szse.cn/api/disc/announcement/annList",
               json={"seDate": [d0, d1], "stock": [code],
                     "channelCode": ["fixed_disc"],
                     "pageSize": 50, "pageNum": 1},
               headers={"Referer":
                        "http://www.szse.cn/disclosure/listed/fixed/index.html",
                        "Content-Type": "application/json"},
               timeout=30)
    r.raise_for_status()
    out = []
    for it in (r.json().get("data") or []):
        out.append({"title": it.get("title", ""),
                    "date": (it.get("publishTime") or "")[:10],
                    "url": "http://disc.szse.cn/download" + it["attachPath"],
                    "src": "SZSE官网"})
    return out


# ------------------------------------------------------- 源 2：巨潮资讯网
def cninfo_orgid(s: requests.Session, code: str) -> str:
    r = s.post("http://www.cninfo.com.cn/new/information/topSearch/query",
               data={"keyWord": code, "maxNum": 10},
               headers={"X-Requested-With": "XMLHttpRequest",
                        "Referer": "http://www.cninfo.com.cn/"}, timeout=20)
    for it in r.json():
        if it.get("code") == code:
            return it["orgId"]
    raise RuntimeError(f"巨潮查不到 orgId: {code}")


def cninfo_list(s: requests.Session, code: str, orgid: str, cat: str,
                d0: str, d1: str) -> list[dict]:
    r = s.post("http://www.cninfo.com.cn/new/hisAnnouncement/query",
               data={"pageNum": 1, "pageSize": 60,
                     "column": "szse" if code[0] == "0" or code[0] == "3"
                     else "sse",
                     "tabName": "fulltext",
                     "plate": "sz" if code[0] in "023" else "sh",
                     "stock": f"{code},{orgid}", "searchkey": "", "secid": "",
                     "category": cat, "trade": "", "seDate": f"{d0}~{d1}",
                     "sortName": "", "sortType": "", "isHLtitle": "true"},
               headers={"X-Requested-With": "XMLHttpRequest",
                        "Referer": "http://www.cninfo.com.cn/"}, timeout=30)
    r.raise_for_status()
    out = []
    for a in (r.json().get("announcements") or []):
        t = (a["announcementTitle"] or "").replace("<em>", "").replace("</em>", "")
        out.append({"title": t,
                    "date": datetime.fromtimestamp(
                        a["announcementTime"] / 1000).strftime("%Y-%m-%d"),
                    "url": "http://static.cninfo.com.cn/" + a["adjunctUrl"],
                    "src": "巨潮资讯网(法定披露平台)"})
    return out


def hkex_list(s: requests.Session, code: str, d0: str, d1: str) -> list[dict]:
    """港交所披露易公告检索（titleSearchServlet）"""
    sid = HK_STOCKID[code]
    r = s.get("https://www1.hkexnews.hk/search/titleSearchServlet.do",
              params={"sortDir": 0, "sortByOptions": "DateTime", "category": 0,
                      "market": "SEHK", "stockId": sid, "documentType": -1,
                      "fromDate": d0.replace("-", ""),
                      "toDate": d1.replace("-", ""), "title": "",
                      "searchType": 1, "t1code": -2, "t2Gcode": -2, "t2code": -2,
                      "rowRange": 100, "lang": "ZH"},
              headers={"Referer":
                       "https://www1.hkexnews.hk/search/titlesearch.xhtml"},
              timeout=40)
    r.raise_for_status()
    rows = json.loads(r.json().get("result", "[]"))
    out = []
    for it in rows:
        dt = it.get("DATE_TIME", "")            # 23/09/2026 12:07
        try:
            iso = datetime.strptime(dt.split()[0], "%d/%m/%Y").strftime("%Y-%m-%d")
        except Exception:                                         # noqa: BLE001
            iso = ""
        out.append({"title": it.get("TITLE", ""), "date": iso,
                    "url": "https://www1.hkexnews.hk" + it["FILE_LINK"],
                    "src": "港交所披露易"})
    return out


def pick(cands: list[dict], pattern: re.Pattern) -> dict | None:
    """从候选公告里挑正报全文：标题命中且排除摘要/英文/更正等"""
    ok = [c for c in cands
          if pattern.search(c["title"]) and not EXCLUDE.search(c["title"])]
    if not ok:
        return None
    return sorted(ok, key=lambda x: x["date"], reverse=True)[0]


def main() -> None:
    s = new_session()
    manifest: list[dict] = []
    if os.path.exists(MANIFEST):
        manifest = json.load(open(MANIFEST, encoding="utf-8"))
    done = {(m["code"], m["kind"]) for m in manifest if m.get("ok")}

    print(f"共 {len(COMPANIES)} 家公司 × {len(TARGETS)} 类报告\n")
    orgids: dict[str, str] = {}

    for code, name, exch, seg in COMPANIES:
        print(f"[{code} {name}] {exch} / {seg}")
        for kind, cat, (d0, d1), pat_a, pat_hk in TARGETS:
            pat = pat_hk if exch == "HKEX" else pat_a
            if (code, kind) in done:
                print(f"   skip {kind}（已存在）")
                continue
            cands: list[dict] = []
            if exch == "HKEX":
                for attempt in range(3):
                    try:
                        cands = hkex_list(s, code, d0, d1)
                        break
                    except Exception as e:                        # noqa: BLE001
                        print(f"   港交所接口第{attempt + 1}次异常({e})")
                        time.sleep(3)
            elif exch == "SZSE":
                for attempt in range(2):
                    try:
                        cands = szse_list(s, code, d0, d1)
                        break
                    except Exception as e:                        # noqa: BLE001
                        print(f"   SZSE接口异常({e})，回落巨潮")
                        time.sleep(2)
            if not cands:
                for attempt in range(4):
                    try:
                        if code not in orgids:
                            orgids[code] = cninfo_orgid(s, code)
                        cands = cninfo_list(s, code, orgids[code], cat, d0, d1)
                        break
                    except Exception as e:                        # noqa: BLE001
                        print(f"   巨潮第{attempt + 1}次异常({e})")
                        time.sleep(4 + 3 * attempt)
            hit = pick(cands, pat)
            if not hit:
                print(f"   !! 未找到 {kind}（候选 {len(cands)} 条）")
                manifest.append({"code": code, "name": name, "exchange": exch,
                                 "segment": seg, "kind": kind, "ok": False})
                continue
            safe = f"{code}_{name}_{kind}.PDF"
            path = os.path.join(PDF_DIR, safe)
            ok = fetch_pdf(s, hit["url"], path)
            size = os.path.getsize(path) if os.path.exists(path) else 0
            print(f"   {'OK ' if ok else 'FAIL'} {safe}  "
                  f"{size / 1048576:.1f}MB  {hit['src']}")
            manifest.append({
                "code": code, "name": name, "exchange": exch, "segment": seg,
                "kind": kind, "ok": ok, "file": safe if ok else None,
                "size_mb": round(size / 1048576, 2), "source": hit["src"],
                "announce_date": hit["date"], "title": hit["title"],
                "url": hit["url"],
                "sha1": sha1_file(path) if ok else None,
                "downloaded_at": datetime.now().isoformat(timespec="seconds"),
            })
            time.sleep(1.2)
        print()

    # 清掉已不在清单里的公司（如已退市的德邦）留下的历史条目
    valid = {c[0] for c in COMPANIES}
    manifest = [m for m in manifest if m["code"] in valid]
    manifest.sort(key=lambda m: (m["code"], m["kind"]))

    json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    okn = sum(1 for m in manifest if m.get("ok"))
    mb = sum(m.get("size_mb", 0) for m in manifest if m.get("ok"))
    print(f"==== 完成：{okn} 份 / 目标 {len(COMPANIES) * len(TARGETS)} 份，"
          f"合计 {mb:.1f} MB ====")
    print(f"清单：{MANIFEST}")


if __name__ == "__main__":
    sys.exit(main())
