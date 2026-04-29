#!/usr/bin/env python3
"""
財務省「製造たばこの小売定価の認可」PDFを取得・解析して prices.json を更新するスクリプト。
GitHub Actions から週次実行される想定。

PDFはToUnicode CMAPが欠落しているため:
- テーブル構造: pdfplumber (罫線検出)
- ASCII/Latin テキスト: pdfminer CID+29 デコード
- 日本語テキスト: fitz + tesseract OCR (セルクロップ)
- 価格: CIDデコード + 前の行からの引き継ぎ (マージドセル対応)
"""
import json
import re
import io
import sys
import time
import logging
from datetime import date, datetime
from urllib.parse import urljoin
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import pdfplumber
import fitz  # PyMuPDF
import pytesseract
from PIL import Image

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

OCR_DPI = 200
OCR_SCALE = OCR_DPI / 72  # PDF points → pixels


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
# テキストデコード
# ---------------------------------------------------------------------------

def decode_cid_ascii(text: str) -> str:
    """
    pdfminer が出力する (cid:XX) を ASCII 文字に変換。
    このPDFのフォントは single-byte CID + 29 = ASCII コードポイント。
    """
    def _replace(m):
        cid = int(m.group(1))
        if cid < 200:
            ch = chr(cid + 29)
            return ch if 0x20 <= ord(ch) <= 0x7E else " "
        return ""  # 日本語CIDは空白に
    return re.sub(r"\(cid:(\d+)\)", _replace, text or "").strip()


def has_japanese_cids(text: str) -> bool:
    """テキストに日本語CID (>= 200) が含まれるか判定。"""
    return any(int(m.group(1)) >= 200 for m in re.finditer(r"\(cid:(\d+)\)", text or ""))


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
    s = s.strip()
    for kw in CATEGORY_KEYWORDS:
        if kw in s:
            return kw
    return s


# ---------------------------------------------------------------------------
# OCR ユーティリティ
# ---------------------------------------------------------------------------

def _crop_and_ocr(page_img: Image.Image, bbox, psm: int = 7) -> str:
    """pdfplumber の bbox をクロップして OCR。"""
    if bbox is None:
        return ""
    x0, top, x1, bottom = bbox
    px0, py0 = int(x0 * OCR_SCALE), int(top * OCR_SCALE)
    px1, py1 = int(x1 * OCR_SCALE), int(bottom * OCR_SCALE)
    if px1 - px0 < 4 or py1 - py0 < 4:
        return ""

    cell_img = page_img.crop((px0, py0, px1, py1))
    if (py1 - py0) < 30:
        cell_img = cell_img.resize(
            (cell_img.width * 3, cell_img.height * 3), Image.LANCZOS
        )

    cfg = f"--psm {psm} -c preserve_interword_spaces=1"
    text = pytesseract.image_to_string(cell_img, lang="jpn", config=cfg)
    return re.sub(r"\s+", " ", text).strip()


def decode_cell(cid_text: str | None, bbox, page_img: Image.Image, psm: int = 7) -> str:
    """
    セルテキストを取得する:
    - ASCII/Latin → CIDデコード
    - 日本語 → OCR
    - どちらも空 → ""
    """
    raw = cid_text or ""
    if not raw and bbox is None:
        return ""

    # 日本語CIDが含まれる場合はOCR
    if has_japanese_cids(raw):
        return _crop_and_ocr(page_img, bbox, psm)

    # ASCII/Latinのみの場合はCIDデコード
    decoded = decode_cid_ascii(raw)
    if decoded:
        return decoded

    # CIDなしでもbboxがあれば念のためOCR
    if bbox:
        return _crop_and_ocr(page_img, bbox, psm)
    return ""


# ---------------------------------------------------------------------------
# メインパーサー
# ---------------------------------------------------------------------------

HEADER_TERMS = {"名称", "区分", "定価", "品目", "製造国", "製品の", "小売"}


def _is_header(text: str) -> bool:
    return any(t in text for t in HEADER_TERMS)


def parse_pdf_table(pdf_bytes: bytes, approval_date: str) -> list[dict]:
    """
    pdfplumber で表の罫線を検出 → セル単位でテキストを取得 → 製品リストを返す。
    価格はマージドセルに対応するため「最後に見た価格を継続使用」方式を採用。
    """
    products = []

    # fitz で全ページを画像化
    fitz_pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    mat = fitz.Matrix(OCR_SCALE, OCR_SCALE)
    page_images: list[Image.Image] = []
    for fpage in fitz_pdf:
        pix = fpage.get_pixmap(matrix=mat)
        page_images.append(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
    fitz_pdf.close()

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            if page_idx >= len(page_images):
                break
            page_img = page_images[page_idx]

            tables = page.find_tables(
                {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
            )
            if not tables:
                continue

            for table in tables:
                rows = table.rows
                if not rows:
                    continue
                num_cols = len(rows[0].cells)
                if num_cols < 4:
                    continue

                # 列インデックスを決定 (6列標準: 区分|ブランド|銘柄名|製品区分|製造国|価格)
                if num_cols >= 6:
                    CI = {"cat": 0, "brand": 1, "name": 2, "type": 3, "ctry": 4, "price": 5}
                elif num_cols == 5:
                    CI = {"cat": 0, "brand": -1, "name": 1, "type": 2, "ctry": 3, "price": 4}
                else:
                    CI = {"cat": 0, "brand": -1, "name": 1, "type": -1, "ctry": -1, "price": num_cols - 1}

                extracted = table.extract()
                current_category = ""
                current_price: int | None = None

                for row_idx, row in enumerate(rows):
                    bboxes = row.cells
                    plumber = extracted[row_idx] if row_idx < len(extracted) else []

                    def cell_raw(key):
                        idx = CI.get(key, -1)
                        if idx < 0 or idx >= len(plumber):
                            return None
                        return plumber[idx]

                    def cell_bbox(key):
                        idx = CI.get(key, -1)
                        if idx < 0 or idx >= len(bboxes):
                            return None
                        return bboxes[idx]

                    # ─── 価格更新 (マージドセル対応: 見つかったら以降の行で継続) ───
                    price_raw = decode_cid_ascii(cell_raw("price") or "")
                    new_price = normalize_price(price_raw)
                    if new_price is not None:
                        current_price = new_price

                    # ─── カテゴリ更新 ───
                    cat_raw = cell_raw("cat")
                    if cat_raw is not None or cell_bbox("cat") is not None:
                        cat_text = decode_cell(cat_raw, cell_bbox("cat"), page_img, psm=6)
                        cat_norm = normalize_category(cat_text)
                        if cat_norm in CATEGORY_KEYWORDS:
                            current_category = cat_norm

                    # ─── 銘柄名取得 (col2 優先、なければ col1 brand) ───
                    name_raw = cell_raw("name")
                    name_bbox = cell_bbox("name")
                    name = ""
                    if name_raw is not None or name_bbox is not None:
                        name = decode_cell(name_raw, name_bbox, page_img, psm=7)

                    if not name:
                        brand_raw = cell_raw("brand")
                        brand_bbox = cell_bbox("brand")
                        if brand_raw is not None or brand_bbox is not None:
                            name = decode_cell(brand_raw, brand_bbox, page_img, psm=7)

                    if not name or _is_header(name):
                        continue
                    if current_price is None or not current_category:
                        continue

                    # ─── 製品区分・製造国 ───
                    type_raw = cell_raw("type")
                    type_bbox = cell_bbox("type")
                    product_type = decode_cell(type_raw, type_bbox, page_img, psm=7) if (type_raw or type_bbox) else ""

                    ctry_raw = cell_raw("ctry")
                    ctry_bbox = cell_bbox("ctry")
                    country = decode_cell(ctry_raw, ctry_bbox, page_img, psm=7) if (ctry_raw or ctry_bbox) else ""

                    products.append({
                        "category": current_category,
                        "name": name,
                        "product_type": product_type,
                        "country": country,
                        "price": current_price,
                        "date": approval_date,
                    })

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
