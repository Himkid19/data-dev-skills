#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
词根平台客户端（自包含模块，本 skill 可整体独立成仓迁移）。

POST 分页拉取词根字典。适配任何返回 {"totalItems": N, "content": [{chName, enName, abbr, category}]}
结构的词根平台；字段名不同可自行改 fetch_all_roots() 里的取值。
本模块只依赖 Python 标准库 + skill 根目录 .env 的 DDM_* 配置：
  DDM_API_URL    接口地址（必填，例如 https://ddm.example.com/api/roots）
  DDM_PORTAL_URL 平台门户地址（可选，用于生成 Origin/Referer；默认取 DDM_API_URL 的 scheme://host）
  DDM_USERNAME   词根平台账号（请求头 username）
  DDM_COOKIE     词根平台登录态 Cookie（浏览器登录后复制，含会话凭证，禁止提交 git）
  DDM_VERIFY_SSL 是否校验证书（默认 1；自签证书环境设 0，等价 curl --insecure）

两种用法：
  作为模块（roots.py sync 调用）：
      import ddm_client
      items = ddm_client.fetch_all_roots()   # [{zh, en, abbr, cat}, ...]

  独立运行（不依赖 MySQL，验证连通性 / 导出调试快照）：
      python ddm_client.py                        # 拉取并打印条数摘要（词根唯一数据源是 MySQL，不落库）
      python ddm_client.py --out /tmp/roots.md    # 额外导出 Markdown 快照到指定路径
"""
import argparse
import datetime
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(BASE)

PAGE_SIZE = 100
SLEEP_SEC = 0.5  # 分页轮询间隔，避免对词根服务端造成压力


def _get(key, default=""):
    """配置读取：环境变量优先，其次 skill 根目录 .env（轻量解析，避免引依赖）。"""
    val = os.environ.get(key)
    if val:
        return val
    env_file = os.path.join(SKILL, ".env")
    if os.path.exists(env_file):
        for line in open(env_file, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                return v
    return default


def _headers(api_url):
    username = _get("DDM_USERNAME", "unknown")
    cookie = _get("DDM_COOKIE")
    if not cookie:
        sys.exit("[ddm] .env 缺少 DDM_COOKIE（词根平台登录态），无法拉取词根。"
                 "请在浏览器登录词根平台后复制 Cookie 到 .env 的 DDM_COOKIE。")
    portal = _get("DDM_PORTAL_URL") or "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(api_url))
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": portal,
        "Referer": portal.rstrip("/") + "/",
        "User-Agent": "Mozilla/5.0 master-data-modeling ddm_client",
        "locale": "zh-CN",
        "username": username,
        "Cookie": cookie,
    }


def _ssl_context():
    """默认校验证书；自签证书场景显式设置 DDM_VERIFY_SSL=0 关闭（等价 curl --insecure）。"""
    if _get("DDM_VERIFY_SSL", "1").strip().lower() in ("0", "false", "no", "off"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


def _fetch_page(api_url, headers, page):
    data = json.dumps({"currentPage": page, "keyword": "", "pageSize": PAGE_SIZE,
                       "orderBy": "timestamp", "sort": "desc", "categories": ""}).encode("utf-8")
    req = urllib.request.Request(api_url, data=data, headers=headers, method="POST")
    ctx = _ssl_context()
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)
        return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"[ddm] HTTP {e.code}: {e.reason}——常见原因是 Cookie 过期，请刷新 .env 的 DDM_COOKIE")
    except Exception as e:
        sys.exit(f"[ddm] 请求失败：{e}")


def fetch_all_roots(verbose=True):
    """分页拉取全部词根，返回 [{zh, en, abbr, cat}, ...]（不含空行，保留转义）。"""
    api_url = _get("DDM_API_URL")
    if not api_url:
        sys.exit("[ddm] 缺少 DDM_API_URL。请在 .env 里配置词根平台接口地址"
                 "（默认请求体为 currentPage/pageSize/keyword，返回 content/totalItems）。")
    headers = _headers(api_url)
    first = _fetch_page(api_url, headers, 0)
    total = first.get("totalItems", 0)
    items = list(first.get("content", []))
    if verbose:
        print(f"[ddm] 第 1 页 {len(items)} 条，共 {total} 条")
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    for page in range(1, total_pages):
        time.sleep(SLEEP_SEC)
        page_items = _fetch_page(api_url, headers, page).get("content", [])
        items.extend(page_items)
        if verbose:
            print(f"[ddm] 第 {page + 1} 页 +{len(page_items)}，累计 {len(items)}")
    out = []
    for it in items:
        zh = (it.get("chName") or "").strip().replace("|", "\\|")
        abbr = (it.get("abbr") or "").strip().replace("|", "\\|")
        if not zh or not abbr:
            continue
        out.append({"zh": zh, "en": (it.get("enName") or "").strip().replace("|", "\\|"),
                    "abbr": abbr, "cat": (it.get("category") or "").strip().replace("|", "\\|")})
    return out


def to_markdown(items, source_note="词根平台（ddm_client.py 拉取）"):
    lines = [
        "# 词根字典 (Naming Standard)",
        "",
        f"> 来源：{source_note}",
        f"> 共 {len(items)} 条词根",
        f"> 获取时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| 中文名 | 英文名 | 缩写 | 分类 |",
        "|--------|--------|------|------|",
    ]
    for r in items:
        lines.append(f"| {r['zh']} | {r['en']} | {r['abbr']} | {r['cat']} |")
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser(description="词根拉取（独立运行，不依赖 MySQL）")
    p.add_argument("--out", help="可选：额外导出 Markdown 快照到指定路径（词根唯一数据源是 MySQL，默认不落盘）")
    args = p.parse_args()
    items = fetch_all_roots()
    print(f"[ddm] 拉取成功，共 {len(items)} 条词根。入库请用：python roots.py sync --apply")
    if args.out:
        out = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(to_markdown(items))
        print(f"[ddm] 已写 {len(items)} 条词根快照 → {out}")


if __name__ == "__main__":
    main()
