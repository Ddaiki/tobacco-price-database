#!/usr/bin/env python3
"""
財務省「製造たばこの小売定価の認可」PDFを取得・解析して prices.json を更新するスクリプト。
GitHub Actions から週次実行される想定。
"""
import json
import re
import io
import sys
import time
import logging
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

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
    """リトライ付きHTTPリクエスト（指数バックオフ）。"""
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
    """財務省ページからPDFリンク一覧を取得する。"""
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
            href = BASE_URL + href
        filename = href.split("/")[-1]
        if filename in seen:
            continue
        seen.add(filename)

        # ファイル名から日付を抽出: 20240807_kouriteika.pdf
        m = re.search(r"(\d{8})_kouriteika", filename)
        if not m:
            continue
        approval_date = datetime.strptime(m.group(1), "%Y%m%d").strftime("%Y-%m-%d")
        links.append({"url": href, "filename": filename, "date": approval_date})

    links.sort(key=lambda x: x["date"])
    log.info("Found %d PDF links", len(links))
    return links


def normalize_price(s: str) -> int | None:
    """価格文字列を整数に変換する。例: '1,240円' → 1240"""
    if not s:
        return None
    m = re.search(r"[\d,]+", s)
    if not m:
        return None
    try:
        return int(m.group().replace(",", ""))
    except ValueError:
        return None


def normalize_category(s: str) -> str:
    """区分の表記ゆれを正規化する。"""
    s = s.strip()
    for kw in CATEGORY_KEYWORDS:
        if kw in s:
            return kw
    return s


def parse_pdf_table(pdf_bytes: bytes, approval_date: str) -> list[dict]:
    """pdfplumber でPDFの表を解析し、製品リストを返す。"""
    if pdfplumber is None:
        raise ImportError("pdfplumber is not installed")

    products = []
    current_category = ""

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables(
                table_settings={
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                }
            )
            if not tables:
                # テーブル検出できない場合はテキストから試みる
                tables = page.extract_tables()

            for table in tables:
                for row in table:
                    if not row:
                        continue
                    cells = [str(c or "").strip() for c in row]

                    # 区分の更新（セルが種類キーワードを含む場合）
                    for kw in CATEGORY_KEYWORDS:
                        if kw in cells[0]:
                            current_category = kw
                            break

                    # ヘッダー行をスキップ
                    if "名" in cells[0] or "区分" in cells[0]:
                        continue

                    # 最低限の列数チェック
                    if len(cells) < 4:
                        continue

                    # 区分・名称・製品区分・国・価格 の位置を推定
                    # PDFの構成に応じてインデックスを調整
                    cat_cell = cells[0] if cells[0] else current_category
                    name_cell = cells[1] if len(cells) > 1 else ""
                    type_cell = cells[2] if len(cells) > 2 else ""
                    # 価格は右端付近
                    price_cell = ""
                    country_cell = ""
                    for j in range(3, len(cells)):
                        price = normalize_price(cells[j])
                        if price is not None and price > 0:
                            price_cell = cells[j]
                            if j > 3:
                                country_cell = cells[j - 1]
                            break

                    if not name_cell or not price_cell:
                        continue
                    price = normalize_price(price_cell)
                    if price is None:
                        continue

                    cat = normalize_category(cat_cell) or current_category
                    if not cat:
                        continue

                    products.append({
                        "category": cat,
                        "name": name_cell,
                        "product_type": type_cell,
                        "country": country_cell,
                        "price": price,
                        "date": approval_date,
                    })

    return products


def make_product_key(p: dict) -> str:
    return f"{p.get('category','')}|{p.get('name','')}|{p.get('product_type','')}"


def merge_into_db(data: dict, new_products: list[dict], pdf_filename: str) -> int:
    """
    新規製品データをDBにマージする。
    - 同一製品キーが存在すれば price_history に追記
    - 存在しなければ新規追加
    戻り値: 追加・更新した件数
    """
    existing = {make_product_key(p): p for p in data["products"]}
    changed = 0

    for np in new_products:
        key = make_product_key(np)
        if key in existing:
            ep = existing[key]
            # 同日同価格は重複追加しない
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
    """既処理のPDFファイル名セットを返す。"""
    processed = set()
    for p in data["products"]:
        for h in p.get("price_history", []):
            if h.get("pdf"):
                processed.add(h["pdf"])
    return processed


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

    new_pdfs = [l for l in pdf_links if l["filename"] not in processed]
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
