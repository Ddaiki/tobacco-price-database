#!/usr/bin/env python3
"""
財務省「製造たばこの小売定価の認可」PDFを取得・解析して prices.json を更新するスクリプト。
GitHub Actions から週次実行される想定。

通常PDF (kouriteika):    5〜6列テーブル → 価格を新規登録/更新
変更PDF (kouriteikahenkou): 7列テーブル (現行価格|変更価格|変更日) → 変更価格で更新

フォント対応:
- MS-Mincho (Identity-H): fitz が正常デコード
- MS-Mincho-90ms-RKSJ-H (Identity-H): pdfminer が (cid:XXXX) 出力 → ms_mincho_gid.json で変換
"""
import json
import re
import io
import sys
import time
import logging
import unicodedata
from datetime import date, datetime
from urllib.parse import urljoin
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import pdfplumber
import fitz  # PyMuPDF

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_URL = "https://www.mof.go.jp"
INDEX_URL = f"{BASE_URL}/policy/tab_salt/topics/kouriteika.html"
DATA_FILE = Path(__file__).parent.parent / "data" / "prices.json"
GID_MAP_FILE = Path(__file__).parent / "ms_mincho_gid.json"

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

HEADER_TERMS = {"名称", "区分", "定価", "品目", "製造国", "製品の", "小売", "現行", "変更"}

BRAND_OVERRIDES_FILE = Path(__file__).parent / "brand_overrides.json"
KNOWN_BRANDS_FILE    = Path(__file__).parent / "known_brands.json"

# ---------------------------------------------------------------------------
# サブカテゴリ判定定数（パイプたばこ → シーシャ / 西洋パイプ）
# ---------------------------------------------------------------------------

_SHISHA_COUNTRIES  = ["アラブ首長国連邦", "ヨルダン", "トルコ", "エジプト", "ロシア", "インド", "レバノン", "サウジ"]
_WESTERN_COUNTRIES = ["デンマーク", "イギリス", "英国", "アイルランド", "ベルギー", "オランダ", "スイス", "スウェーデン", "ドイツ"]
_SHISHA_BRANDS = [
    "AL FAKHER","ALFAKHER","DOZAJ","DARKSIDE","DARK SIDE","AFZAL",
    "FUMARI","AZURE","TRIFECTA","SOCIAL SMOKE","STARBUZZ","STAR BUZZ",
    "SERBETLI","DEBAJ","NAKHLA","AL WAHA","BUTA","MALAKI","LIRRA",
    "TANGIERS","HAZE","ODUMAN","REVOSHI","SEBERO","ELEMENT","MUSTHAVE",
    "ZODIAC","JIBAR","JIBIAR","LAVOO","CHAOS","DUFT","ARGELINI",
    "SHISHA KARTEL","ADALYA","MUST HAVE","CONSUME","BLUE MIST",
    "TWO APPLE","WHITE FOX","CHABACCO","PARADISE","KRAKEN",
    "MAZAYA","HOOKAFINA","TOKYO SHISHA","JBR","ROYAL SMOKIN",
    "アルファーヘル","ドザジ","ダークサイド","アフザル","フマリ",
    "スターバズ","セルベトリ","デバジ","ナハラ","アルワハ","マラキ","リラ",
    "ロイヤルスモーキン","BANG BANG","BONCHE","SAMURAI BLOND","BLTC",
    "NASH","MEZZA","DEUS",
]
_PIPE_KW  = ["MIXTURE","BLEND","FLAKE","CUT","CAVENDISH","LATAKIA","NAVY",
             "VIRGINIA","BURLEY","ORIENTAL","ENGLISH","SCOTTISH","AROMATIC",
             "シャグ","フレイク","ミクスチャー","ブレンド","オリエント","バージニア"]
_SHISHA_KW = ["HOOKAH","MOLASSES","FLAVORED","ICE","MOJITO","FIZZ"]


def _get_pipe_subcat(p: dict) -> str:
    country  = p.get("country", "")
    name_up  = (p.get("name", "") + " " + p.get("product_type", "")).upper()
    if any(b in name_up for b in (b.upper() for b in _SHISHA_BRANDS)):
        return "シーシャ"
    if any(c in country for c in _SHISHA_COUNTRIES):
        return "シーシャ"
    if any(c in country for c in _WESTERN_COUNTRIES):
        return "西洋パイプ"
    if any(k in name_up for k in _PIPE_KW):
        return "西洋パイプ"
    if any(k in name_up for k in _SHISHA_KW):
        return "シーシャ"
    return "西洋パイプ"


# ---------------------------------------------------------------------------
# ブランド抽出
# ---------------------------------------------------------------------------

def _load_brand_overrides() -> dict[str, str]:
    overrides: dict[str, str] = {}
    # brand_overrides.json（手動補正）
    if BRAND_OVERRIDES_FILE.exists():
        with open(BRAND_OVERRIDES_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        overrides.update({k: v for k, v in raw.items() if not k.startswith("_")})
    # known_brands.json の brand_normalization（自動生成）で追加補正
    if KNOWN_BRANDS_FILE.exists():
        with open(KNOWN_BRANDS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        norm = raw.get("brand_normalization", {})
        overrides.update({k: v for k, v in norm.items() if not k.startswith("_")})
    return overrides


def _load_known_brands() -> list[str]:
    """既知の・区切りブランド名を長い順に返す（前方一致で使用）。
    known_brands.json の multi_segment_brand_names キーを使用。"""
    if KNOWN_BRANDS_FILE.exists():
        with open(KNOWN_BRANDS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        multi = raw.get("multi_segment_brand_names", {})
        brands = [k for k in multi.keys() if not k.startswith("_")]
        return sorted(brands, key=len, reverse=True)
    return []


_BRAND_OVERRIDES: dict[str, str] = _load_brand_overrides()
_KNOWN_BRANDS: list[str] = _load_known_brands()
_BRAND_SKIP_WORDS = {"hookah", "tobacco", "cigars", "cigar", "pipe", "shisha",
                     "premium", "classic", "original", "special",
                     # 日本語カタカナ一般名詞
                     "パイプ", "パイプたばこ", "パイプタバコ", "シャグ", "シーシャ",
                     "フレイク", "タバコ", "たばこ"}


def extract_brand(name: str) -> str:
    """商品名からブランド名を抽出する"""
    name = name.replace("\n", " ").strip()

    # カンマ前がブランド+製品（例: "Azure hookah tobacco, Black Line..."）
    if "," in name:
        name = name.split(",")[0].strip()

    # ・(中黒) 区切りの日本語名: known_brands と前方一致し最長のものを採用、
    # 一致しない場合は最初のセグメントをブランドとする
    if "・" in name:
        for known in _KNOWN_BRANDS:  # 長い順にソート済み
            if name.startswith(known):
                candidate = known
                return _BRAND_OVERRIDES.get(candidate, candidate)
        candidate = name.split("・")[0].strip()
        return _BRAND_OVERRIDES.get(candidate, candidate)

    parts = name.split()
    if not parts:
        return name
    if len(parts) == 1:
        return parts[0]

    # 先頭の全大文字単語の連続を抽出（AL FAKHER, EP CARRILLO 等）
    caps_prefix = []
    for p in parts:
        if p.isupper() and len(p) >= 2:
            caps_prefix.append(p)
        else:
            break

    if len(caps_prefix) >= 2:
        # 2語以上の全大文字 → 2語をブランドとする
        candidate = " ".join(caps_prefix[:2])
    elif len(caps_prefix) == 1 and len(caps_prefix[0]) >= 3:
        # 1語全大文字（SEBERO, DOZAJ 等） → その語のみ
        candidate = caps_prefix[0]
    else:
        # 混在ケース: 2語目が数字・一般名詞なら1語のみ
        second = parts[1]
        if re.match(r"^[\d\(（【]", second) or second.lower() in _BRAND_SKIP_WORDS:
            candidate = parts[0]
        else:
            candidate = f"{parts[0]} {parts[1]}"

    # 手動補正を適用
    return _BRAND_OVERRIDES.get(candidate, candidate)


# ---------------------------------------------------------------------------
# 商品データ付加情報の計算
# ---------------------------------------------------------------------------

def enrich_products(products: list[dict]) -> None:
    """全商品に subcategory・brand フィールドを付加（in-place）"""
    for p in products:
        # subcategory: パイプたばこのみ設定、他はNone
        if p.get("category") == "パイプたばこ":
            p["subcategory"] = _get_pipe_subcat(p)
        else:
            p["subcategory"] = None
        # brand
        p["brand"] = extract_brand(p.get("name", ""))


# ---------------------------------------------------------------------------
# GlyphID → Unicode マップ (MS-Mincho-90ms-RKSJ-H 用)
# ---------------------------------------------------------------------------

def load_gid_map() -> dict[int, int]:
    if GID_MAP_FILE.exists():
        with open(GID_MAP_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        return {int(k): v for k, v in raw.items()}
    return {}

GID2UNICODE: dict[int, int] = load_gid_map()


def decode_cids(text: str | None) -> str:
    """
    pdfminer が出力する (cid:XX) を Unicode に変換。
    - CID < 200: CID + 29 = ASCII
    - CID >= 200: ms_mincho_gid.json の GlyphID→Unicode で変換
    """
    if not text:
        return ""

    def _replace(m: re.Match) -> str:
        cid = int(m.group(1))
        if cid < 200:
            ch = chr(cid + 29)
            return ch if 0x20 <= ord(ch) <= 0x7E else ""
        if cid in GID2UNICODE:
            return chr(GID2UNICODE[cid])
        return ""

    result = re.sub(r"\(cid:(\d+)\)", _replace, text)
    return unicodedata.normalize("NFKC", result).strip()


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
    enrich_products(data["products"])  # subcategory・brand を全商品に付加
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
        is_henkou = "henkou" in filename
        links.append({
            "url": href,
            "filename": filename,
            "date": approval_date,
            "is_henkou": is_henkou,
        })

    links.sort(key=lambda x: x["date"])
    log.info("Found %d PDF links (%d 変更)",
             len(links), sum(1 for l in links if l["is_henkou"]))
    return links


# ---------------------------------------------------------------------------
# テキスト正規化
# ---------------------------------------------------------------------------

def norm(s: str | None) -> str:
    return unicodedata.normalize("NFKC", (s or "").strip())


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


def combine_brand_variant(brand: str, variant: str) -> str:
    """
    銘柄名と品名（バリアント）を結合。
    variant が '・' で始まる場合は直接結合 ('ブランド・バリアント')、
    そうでなければスペース結合 ('Brand Flavor')。
    """
    brand = norm(brand)
    variant = norm(variant)
    if not variant:
        return brand
    if variant.startswith("・"):
        return brand + variant
    return (brand + " " + variant).strip()


def normalize_category(s: str) -> str:
    s = norm(s)
    for kw in CATEGORY_KEYWORDS:
        if kw in s:
            return kw
    return s


def parse_reiwa_date(s: str, fallback: str) -> str:
    """
    令和年月日 '7.5.1' → '2025-05-01'
    令和N年 = 2018 + N
    """
    s = norm(s)
    m = re.match(r"(\d+)[.年](\d+)[.月](\d+)", s)
    if not m:
        return fallback
    era_year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    year = 2018 + era_year
    return f"{year:04d}-{month:02d}-{day:02d}"


def is_header_row(cells: list[str]) -> bool:
    joined = "".join(cells)
    return any(t in joined for t in HEADER_TERMS)


def has_garbled_font(pdf_bytes: bytes) -> bool:
    """PDFが MS-Mincho-90ms-RKSJ-H フォントを使用しているか確認。"""
    try:
        pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in pdf:
            for font in page.get_fonts():
                if "90ms-RKSJ-H" in (font[3] or ""):
                    pdf.close()
                    return True
        pdf.close()
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# パーサー: pdfplumber + CIDデコード (全フォント対応)
# ---------------------------------------------------------------------------

def parse_pdf_table(pdf_bytes: bytes, approval_date: str, is_henkou: bool) -> list[dict]:
    """
    pdfplumber でテーブルを検出し、CIDデコードでテキストを取得。
    通常PDF 5列: [区分, 銘柄名, 製品区分, 製造国, 価格]           (旧フォーマット)
    通常PDF 6列: [区分, 銘柄名, 品名, 製品区分, 製造国, 価格]      (新フォーマット 2026~)
    変更PDF 7列: [区分, 銘柄名, 製品区分, 製造国, 現行価格, 変更価格, 変更日]

    セル結合で区分・銘柄名が空の行は直前の値を引き継ぐ。
    """
    products = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.find_tables(
                {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
            )
            for table in tables:
                rows = table.extract()
                if not rows:
                    continue

                last_category = ""
                last_name = ""
                last_country = ""

                for row in rows:
                    # CIDデコード
                    cells = [decode_cids(c) for c in row]

                    # 空行・ヘッダー行をスキップ
                    if not any(cells):
                        continue
                    if is_header_row(cells):
                        continue

                    n = len(cells)

                    # セル結合対応: 区分・名称・製造国は先に抽出してキャリーフォワードを更新
                    raw_category = normalize_category(cells[0]) if n >= 1 else ""
                    raw_name = norm(cells[1]) if n >= 2 else ""
                    # 列フォーマット別に製造国の位置を特定
                    # 通常5列=cells[3], 通常6列=cells[4], 変更7列=cells[3], 変更8列=cells[4]
                    if n == 6 or (is_henkou and n == 8):
                        raw_country = norm(cells[4])
                    elif n == 5 or (is_henkou and n == 7):
                        raw_country = norm(cells[3])
                    else:
                        raw_country = ""

                    if raw_category in CATEGORY_KEYWORDS:
                        last_category = raw_category
                    if raw_name:
                        last_name = raw_name
                    if raw_country:
                        last_country = raw_country

                    if is_henkou and n == 8:
                        # 変更PDF新フォーマット: [区分, 銘柄名, 品名, 製品区分, 製造国, 現行価格, 変更価格, 変更日]
                        # 銘柄名(cells[1]) + 品名(cells[2]) を結合して一意な名称にする
                        category = raw_category or last_category
                        if category not in CATEGORY_KEYWORDS:
                            continue
                        brand = raw_name or last_name
                        if not brand:
                            continue
                        name = combine_brand_variant(brand, norm(cells[2]))
                        product_type = norm(re.sub(r"\s+", " ", cells[3]))
                        country = raw_country or last_country
                        price = normalize_price(cells[6])  # 変更小売定価を使用
                        if price is None:
                            continue
                        change_date = parse_reiwa_date(cells[7], approval_date)

                    elif is_henkou and n == 7:
                        # 変更PDF旧フォーマット: [区分, 銘柄名, 製品区分, 製造国, 現行価格, 変更価格, 変更日]
                        category = raw_category or last_category
                        if category not in CATEGORY_KEYWORDS:
                            continue
                        name = raw_name or last_name
                        if not name:
                            continue
                        product_type = norm(re.sub(r"\s+", " ", cells[2]))
                        country = raw_country or last_country
                        price = normalize_price(cells[5])  # 変更小売定価を使用
                        if price is None:
                            continue
                        change_date = parse_reiwa_date(cells[6], approval_date)

                    elif not is_henkou and n == 6:
                        # 通常PDF新フォーマット: [区分, 銘柄名, 品名, 製品区分, 製造国, 価格]
                        # 銘柄名(cells[1]) + 品名(cells[2]) を結合して一意な名称にする
                        category = raw_category or last_category
                        if category not in CATEGORY_KEYWORDS:
                            continue
                        brand = raw_name or last_name
                        if not brand:
                            continue
                        name = combine_brand_variant(brand, norm(cells[2]))
                        product_type = norm(re.sub(r"\s+", " ", cells[3]))
                        country = raw_country or last_country
                        price = normalize_price(cells[5])
                        if price is None:
                            continue
                        change_date = approval_date

                    elif not is_henkou and n == 5:
                        # 通常PDF旧フォーマット: [区分, 銘柄名, 製品区分, 製造国, 価格]
                        category = raw_category or last_category
                        if category not in CATEGORY_KEYWORDS:
                            continue
                        name = raw_name or last_name
                        if not name:
                            continue
                        product_type = norm(re.sub(r"\s+", " ", cells[2]))
                        country = raw_country or last_country
                        price = normalize_price(cells[4])
                        if price is None:
                            continue
                        change_date = approval_date

                    else:
                        continue

                    products.append({
                        "category": category,
                        "name": name,
                        "product_type": product_type,
                        "country": country,
                        "price": price,
                        "date": change_date,
                    })

    return products


# ---------------------------------------------------------------------------
# DB マージ
# ---------------------------------------------------------------------------

def make_product_key(p: dict) -> str:
    return f"{p.get('category','')}|{p.get('name','')}|{p.get('product_type','')}"


def merge_into_db(data: dict, new_products: list[dict], pdf_filename: str) -> int:
    existing = {make_product_key(p): p for p in data["products"]}

    # 銘柄名+カテゴリ フォールバック用インデックス (既存DBのみ; ループ中は更新しない)
    # 用途: 変更PDFで product_type の表記が微妙に異なる場合のマッチング
    # ※ 同一PDFに同じ銘柄の複数バリアントが含まれる場合の誤マージを防ぐため
    #   ループ中に新規追加した製品は existing_by_name に入れない。
    existing_by_name: dict[str, dict] = {}
    for p in data["products"]:
        name_key = f"{p.get('category','')}|{p.get('name','')}"
        if name_key not in existing_by_name:
            existing_by_name[name_key] = p

    changed = 0

    for np in new_products:
        key = make_product_key(np)
        name_key = f"{np.get('category','')}|{np.get('name','')}"

        # 1. 完全キー（区分|銘柄名|製品区分）でマッチ
        ep = existing.get(key)

        # 2. 名前キーフォールバック: product_type が互換的な場合のみ使用
        #    (旧フォーマット→新フォーマットで product_type の重量記述が変わった場合に対応)
        if ep is None:
            ep_by_name = existing_by_name.get(name_key)
            if ep_by_name is not None:
                ep_pt = ep_by_name.get("product_type", "")
                np_pt = np.get("product_type", "")
                # 一方が空か、一方が他方に含まれる場合のみフォールバックを使用
                if not ep_pt or not np_pt or ep_pt in np_pt or np_pt in ep_pt:
                    ep = ep_by_name

        if ep is not None:
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
            new_entry = {
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
            existing[key] = new_entry
            # existing_by_name は更新しない (同一PDF内の別バリアントとの誤マージ防止)
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
        log.info("処理中: %s (%s)%s",
                 link["filename"], link["date"],
                 " [変更]" if link["is_henkou"] else "")
        try:
            resp = fetch_with_retry(link["url"])
            products = parse_pdf_table(
                resp.content, link["date"], link["is_henkou"]
            )
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
