#!/usr/bin/env python3
"""
財務省「製造たばこの小売定価の認可」PDFを取得・解析して prices.json を更新するスクリプト。
GitHub Actions から週次実行される想定。

テキスト抽出: PyMuPDF (fitz) の get_text('blocks') を使用。
半角カタカナは NFKC 正規化で全角に変換。
"""
import json
import re
import sys
import time
import logging
import unicodedata
from datetime import date, datetime
from urllib.parse import urljoin
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import fitz  # PyMuPDF

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_URL = "https://www.mof.go.jp"
INDEX_URL = f"{BASE_URL}/policy/tab_salt/topics/kouriteika.html"
DATA_FILE = Path(__file__).parent.parent / "data" / "prices.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

CATEGORY_KEYWORDS = [
    "加熱式たばこ", "葉巻たばこ", "紙巻たばこ",
    "パイプたばこ", "かぎたばこ", "刻みたばこ",
]

HEADER_TERMS = {"名称", "区分", "定価", "品目", "製造国", "製品の", "小売"}


# ---------------------------------------------------------------------------
# データIO
# ---------------------------------------------------------------------------

def load_data() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"updated_at": "", "products": []}


def save_data(data: dict):
    data["updated_at"] = date.today().isoformat()
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info("Saved %d products to %s", len(data["products"]), DATA_FILE)


def fetch_with_retry(url: str, **kwargs) -> requests.Response:
    delays = [2, 4, 8, 16]
    for i, delay in enumerate(delays):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30, **kwargs)
            if resp.status_code == 200:
                return resp
            log.warning("HTTP %d for %s", resp.status_code, url)
        except requests.RequestException as e:
            log.warning("Request error: %s", e)
        if i < len(delays) - 1:
            log.info("Retry in %ds...", delay)
            time.sleep(delay)
    raise RuntimeError(f"Failed to fetch: {url}")


def fetch_pdf_links() -> list[dict]:
    log.info("Fetching index page: %s", INDEX_URL)
    resp = fetch_with_retry(INDEX_URL)
    soup = BeautifulSoup(resp.text, "html.parser")

    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "kouriteika" not in href or not href.endswith(".pdf"):
            continue
        if not href.startswith("http"):
            href = urljoin(INDEX_URL, href)
        filename = href.split("/")[-1]
        if filename in seen:
            continue
        seen.add(filename)
        m = re.search(r"(\d{8})_kouriteika", filename)
        if not m:
            continue
        approval_date = datetime.strptime(m.group(1), "%Y%m%d").strftime("%Y-%m-%d")
        links.append({"url": href, "filename": filename, "date": approval_date})

    links.sort(key=lambda x: x["date"])
    log.info("Found %d PDF links", len(links))
    return links


# ---------------------------------------------------------------------------
# テキスト正規化
# ---------------------------------------------------------------------------

def normalize_text(s: str) -> str:
    """半角カタカナ・記号を全角に変換し、余分な空白を除去。"""
    return unicodedata.normalize("NFKC", s).strip()


def normalize_price(s: str) -> int | None:
    if not s:
        return None
    m = re.search(r"[\d,]{3,}", s)
    if not m:
        return None
    try:
        v = int(m.group().replace(",", ""))
        return v if 100 <= v <= 99999 else None
    except ValueError:
        return None


def normalize_category(s: str) -> str:
    s = normalize_text(s)
    for kw in CATEGORY_KEYWORDS:
        if kw in s:
            return kw
    return s


def is_header_block(parts: list[str]) -> bool:
    """ヘッダー行かどうか判定。"""
    joined = "".join(parts)
    return any(t in joined for t in HEADER_TERMS)


# ---------------------------------------------------------------------------
# メインパーサー
# ---------------------------------------------------------------------------

def parse_pdf_table(pdf_bytes: bytes, approval_date: str) -> list[dict]:
    """
    fitz の get_text('blocks') で各行をブロックとして取得し製品リストを返す。
    各データブロックは 6 要素: [区分, 銘柄名, 製品区分, 品目, 製造国, 価格]
    """
    products = []

    fitz_pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page in fitz_pdf:
        blocks = page.get_text("blocks")
        for block in blocks:
            text = block[4]
            parts = [normalize_text(p) for p in text.strip().split("\n") if p.strip()]

            if len(parts) < 5:
                continue
            if is_header_block(parts):
                continue

            # カテゴリ確認
            category = normalize_category(parts[0])
            if category not in CATEGORY_KEYWORDS:
                continue

            name = parts[1]
            if not name:
                continue

            # 製品区分: parts[2] と parts[3] を結合 (e.g. "50.0g-箱")
            if len(parts) >= 6:
                product_type = parts[2] + "-" + parts[3] if parts[3] else parts[2]
                country = parts[4]
                price_raw = parts[5]
            else:
                # 5要素の場合: 区分|銘柄名|製品区分|製造国|価格
                product_type = parts[2]
                country = parts[3]
                price_raw = parts[4]

            price = normalize_price(price_raw)
            if price is None:
                continue

            products.append({
                "category": category,
                "name": name,
                "product_type": product_type,
                "country": country,
                "price": price,
                "date": approval_date,
            })

    fitz_pdf.close()
    return products


# ---------------------------------------------------------------------------
# DB マージ
# ---------------------------------------------------------------------------

def make_product_key(p: dict) -> str:
    return f"{p.get('category','')}|{p.get('name','')}|{p.get('product_type','')}"


def merge_into_db(data: dict, new_products: list[dict], pdf_filename: str) -> int:
    existing = {make_product_key(p): p for p in data["products"]}
    changed = 0

    for np in new_products:
        key = make_product_key(np)
        if key in existing:
            ep = existing[key]
            already = any(
                h["date"] == np["date"] and h["price"] == np["price"]
                for h in ep.get("price_history", [])
            )
            if not already:
                ep.setdefault("price_history", []).append({
                    "price": np["price"],
                    "date": np["date"],
                    "pdf": pdf_filename,
                })
                ep["price_history"].sort(key=lambda h: h["date"], reverse=True)
                ep["current_price"] = ep["price_history"][0]["price"]
                ep["last_changed"] = ep["price_history"][0]["date"]
                changed += 1
        else:
            existing[key] = {
                "category": np["category"],
                "name": np["name"],
                "product_type": np["product_type"],
                "country": np["country"],
                "current_price": np["price"],
                "last_changed": np["date"],
                "discontinued": False,
                "price_history": [{
                    "price": np["price"],
                    "date": np["date"],
                    "pdf": pdf_filename,
                }],
            }
            changed += 1

    data["products"] = list(existing.values())
    return changed


def get_processed_pdfs(data: dict) -> set[str]:
    processed = set()
    for p in data["products"]:
        for h in p.get("price_history", []):
            if h.get("pdf"):
                processed.add(h["pdf"])
    return processed


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main():
    log.info("=== たばこ小売定価 更新スクリプト開始 ===")
    data = load_data()
    processed = get_processed_pdfs(data)
    log.info("既処理PDF件数: %d", len(processed))

    try:
        pdf_links = fetch_pdf_links()
    except RuntimeError as e:
        log.error("インデックスページ取得失敗: %s", e)
        sys.exit(1)

    new_pdfs = [lnk for lnk in pdf_links if lnk["filename"] not in processed]
    log.info("未処理PDF件数: %d", len(new_pdfs))

    total_changed = 0
    for link in new_pdfs:
        log.info("処理中: %s (%s)", link["filename"], link["date"])
        try:
            resp = fetch_with_retry(link["url"])
            products = parse_pdf_table(resp.content, link["date"])
            log.info("  抽出件数: %d", len(products))
            if products:
                changed = merge_into_db(data, products, link["filename"])
                total_changed += changed
                log.info("  DB更新件数: %d", changed)
        except Exception as e:
            log.error("  エラー (スキップ): %s", e)

    if total_changed > 0 or not data["products"]:
        save_data(data)
        log.info("完了: %d件の変更", total_changed)
    else:
        log.info("新規データなし。更新をスキップします。")

    log.info("=== 完了 ===")


if __name__ == "__main__":
    main()
