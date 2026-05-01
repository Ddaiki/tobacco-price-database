#!/usr/bin/env python3
"""
価格更新後にサブスクライバーへ通知メールを送るスクリプト。
GitHub Actions の update-prices.yml から prices.json 更新後に実行される。

必要な環境変数:
  SUPABASE_URL               Supabase プロジェクト URL
  SUPABASE_SERVICE_ROLE_KEY  Supabase サービスロールキー
  RESEND_API_KEY             Resend API キー
  SITE_URL                   サイト URL（メール内リンク用）
"""
import json
import os
import subprocess
import logging
import requests
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
RESEND_KEY   = os.environ["RESEND_API_KEY"]
SITE_URL     = os.environ.get("SITE_URL", "").rstrip("/")

DATA_FILE = Path(__file__).parent.parent / "data" / "prices.json"
DATA_FILE_GIT = "tobacco-prices/data/prices.json"

# パイプたばこのサブカテゴリ判定（index.html と同じロジック）
SHISHA_COUNTRIES  = ["アラブ首長国連邦", "ヨルダン", "トルコ", "エジプト", "ロシア", "インド", "レバノン", "サウジ"]
WESTERN_COUNTRIES = ["デンマーク", "イギリス", "英国", "アイルランド", "ベルギー", "オランダ", "スイス", "スウェーデン", "ドイツ"]
SHISHA_BRANDS = [
    "AL FAKHER", "ALFAKHER", "DOZAJ", "DARKSIDE", "DARK SIDE", "AFZAL",
    "FUMARI", "AZURE", "TRIFECTA", "SOCIAL SMOKE", "STARBUZZ", "STAR BUZZ",
    "SERBETLI", "DEBAJ", "NAKHLA", "AL WAHA", "BUTA", "MALAKI", "LIRRA",
    "TANGIERS", "HAZE", "ODUMAN", "REVOSHI", "SEBERO", "ELEMENT", "MUSTHAVE",
    "ZODIAC", "JIBAR", "JIBIAR", "LAVOO", "CHAOS", "DUFT", "ARGELINI",
    "SHISHA KARTEL", "ADALYA", "MUST HAVE", "CONSUME", "BLUE MIST",
    "TWO APPLE", "WHITE FOX", "CHABACCO", "PARADISE", "KRAKEN",
    "MAZAYA", "HOOKAFINA", "TOKYO SHISHA", "JBR", "ROYAL SMOKIN",
    "アルファーヘル", "ドザジ", "ダークサイド", "アフザル", "フマリ",
    "スターバズ", "セルベトリ", "デバジ", "ナハラ", "アルワハ", "マラキ", "リラ",
    "ロイヤルスモーキン",
]
PIPE_KW = [
    "MIXTURE", "BLEND", "FLAKE", "CUT", "CAVENDISH", "LATAKIA", "NAVY",
    "VIRGINIA", "BURLEY", "ORIENTAL", "ENGLISH", "SCOTTISH", "AROMATIC",
    "シャグ", "フレイク", "ミクスチャー", "ブレンド", "オリエント", "バージニア",
]
SHISHA_KW = ["HOOKAH", "MOLASSES", "FLAVORED", "ICE", "MOJITO", "FIZZ"]

CATEGORY_DISPLAY = {
    "加熱式たばこ": "加熱式",
    "葉巻たばこ": "葉巻",
    "紙巻たばこ": "紙巻",
    "パイプたばこ": "パイプ",
    "かぎたばこ": "かぎ",
    "刻みたばこ": "刻み",
}


def get_pipe_subcat(p: dict) -> str:
    """パイプたばこのサブカテゴリ（シーシャ/西洋パイプ）を返す"""
    country  = p.get("country", "")
    name_up  = (p.get("name", "") + " " + p.get("product_type", "")).upper()
    if any(b in name_up for b in (b.upper() for b in SHISHA_BRANDS)):
        return "シーシャ"
    if any(c in country for c in SHISHA_COUNTRIES):
        return "シーシャ"
    if any(c in country for c in WESTERN_COUNTRIES):
        return "西洋パイプ"
    if any(k in name_up for k in PIPE_KW):
        return "西洋パイプ"
    if any(k in name_up for k in SHISHA_KW):
        return "シーシャ"
    return "西洋パイプ"


def effective_category(p: dict) -> str:
    """通知フィルター用の実効カテゴリを返す（パイプはサブカテゴリに展開）"""
    if p.get("category") == "パイプたばこ":
        return get_pipe_subcat(p)
    return p.get("category", "")


def get_old_products() -> dict[str, dict]:
    """git show HEAD~1 で変更前の prices.json を取得して名前→商品のマップを返す"""
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD~1:{DATA_FILE_GIT}"],
            capture_output=True, text=True, check=True,
        )
        old = json.loads(result.stdout)
        return {p["name"]: p for p in old.get("products", [])}
    except Exception as e:
        log.warning("旧バージョン取得失敗: %s", e)
        return {}


def find_changes(old_map: dict, new_products: list) -> list:
    """新規追加・価格変更を検出して返す"""
    changes = []
    for p in new_products:
        if p.get("discontinued"):
            continue
        name = p["name"]
        if name not in old_map:
            changes.append({"type": "new", "product": p, "old_price": None, "new_price": p["current_price"]})
        elif old_map[name]["current_price"] != p["current_price"]:
            changes.append({"type": "price_change", "product": p,
                            "old_price": old_map[name]["current_price"], "new_price": p["current_price"]})
    return changes


def get_confirmed_subscribers() -> list:
    """Supabase から confirmed=true の購読者を全件取得"""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    url = f"{SUPABASE_URL}/rest/v1/subscribers?confirmed=eq.true&select=*"
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def matches_subscriber(change: dict, sub: dict) -> bool:
    """この変更が購読者のフィルター条件に合致するか判定"""
    p = change["product"]

    # カテゴリフィルター（空 = 全カテゴリ）
    cats = sub.get("category_filters") or []
    if cats:
        eff_cat = effective_category(p)
        if eff_cat not in cats:
            return False

    # 製造国フィルター（空 = 全国）
    countries = sub.get("country_filters") or []
    if countries:
        prod_country = p.get("country", "")
        if not any(c in prod_country for c in countries):
            return False

    # ブランドキーワードフィルター（空 = 全ブランド、部分一致）
    keywords = sub.get("name_keywords") or []
    if keywords:
        name_lower = p.get("name", "").lower()
        if not any(k.lower() in name_lower for k in keywords):
            return False

    return True


def build_email_html(changes: list, sub: dict, updated_at: str) -> str:
    new_items     = [c for c in changes if c["type"] == "new"]
    price_changes = [c for c in changes if c["type"] == "price_change"]

    def rows_html(items: list, label: str) -> str:
        if not items:
            return ""
        html = (
            f'<tr><td colspan="4" style="background:#f0f4ff;padding:6px 10px;'
            f'font-weight:bold;font-size:13px;color:#1a237e">{label}</td></tr>'
        )
        for c in items:
            p = c["product"]
            cat = CATEGORY_DISPLAY.get(p.get("category", ""), p.get("category", ""))
            if p.get("category") == "パイプたばこ":
                cat = get_pipe_subcat(p)
            if c["type"] == "new":
                price_str = f'¥{c["new_price"]:,}（新規）'
            else:
                price_str = f'¥{c["old_price"]:,} → ¥{c["new_price"]:,}'
            html += (
                f'<tr>'
                f'<td style="padding:6px 10px;font-size:12px;color:#666">{cat}</td>'
                f'<td style="padding:6px 10px;font-size:13px">{p["name"]}</td>'
                f'<td style="padding:6px 10px;font-size:12px;color:#555">{p.get("country","")}</td>'
                f'<td style="padding:6px 10px;font-size:13px;font-weight:bold;white-space:nowrap">{price_str}</td>'
                f'</tr>'
            )
        return html

    all_rows = rows_html(new_items, f"新規認可（{len(new_items)}件）") + \
               rows_html(price_changes, f"価格変更（{len(price_changes)}件）")

    token     = sub.get("unsubscribe_token", "")
    unsub_url = f"{SITE_URL}?unsubscribe={token}" if SITE_URL else "#"

    return f"""<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;font-family:'Hiragino Sans','Meiryo',sans-serif;background:#f4f6f8;padding:20px">
<div style="max-width:600px;margin:0 auto;background:white;border-radius:8px;overflow:hidden">
  <div style="background:#1a237e;padding:16px 20px">
    <h1 style="margin:0;color:white;font-size:16px">製造たばこ 小売定価 更新通知</h1>
    <p style="margin:4px 0 0;color:rgba(255,255,255,.7);font-size:12px">更新日: {updated_at}</p>
  </div>
  <div style="padding:16px 20px">
    <p style="margin:0 0 12px;font-size:13px">
      {len(new_items)}件の新規認可、{len(price_changes)}件の価格変更がありました。
    </p>
    <table style="width:100%;border-collapse:collapse">
      <thead>
        <tr style="background:#e8eaf6">
          <th style="padding:6px 10px;text-align:left;font-size:12px;color:#283593">種類</th>
          <th style="padding:6px 10px;text-align:left;font-size:12px;color:#283593">銘柄名</th>
          <th style="padding:6px 10px;text-align:left;font-size:12px;color:#283593">製造国</th>
          <th style="padding:6px 10px;text-align:left;font-size:12px;color:#283593">価格</th>
        </tr>
      </thead>
      <tbody>{all_rows}</tbody>
    </table>
    <div style="margin-top:20px;text-align:center">
      <a href="{SITE_URL}" style="background:#1a237e;color:white;padding:10px 24px;
         border-radius:6px;text-decoration:none;font-size:14px">データベースを見る</a>
    </div>
  </div>
  <div style="padding:12px 20px;border-top:1px solid #eee;text-align:center">
    <a href="{unsub_url}" style="font-size:11px;color:#999;text-decoration:none">配信停止</a>
  </div>
</div>
</body>
</html>"""


def send_email(to_email: str, html: str, updated_at: str, change_count: int) -> None:
    headers = {
        "Authorization": f"Bearer {RESEND_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": "たばこ価格通知 <onboarding@resend.dev>",
        "to": [to_email],
        "subject": f"たばこ価格更新（{change_count}件） - {updated_at}",
        "html": html,
    }
    r = requests.post("https://api.resend.com/emails", json=payload, headers=headers, timeout=30)
    r.raise_for_status()


def main() -> None:
    with open(DATA_FILE) as f:
        new_data = json.load(f)

    old_map  = get_old_products()
    changes  = find_changes(old_map, new_data.get("products", []))

    if not changes:
        log.info("変更なし - 通知スキップ")
        return

    log.info("変更検出: %d件", len(changes))

    subscribers = get_confirmed_subscribers()
    log.info("登録ユーザー: %d人", len(subscribers))

    sent = 0
    for sub in subscribers:
        matched = [c for c in changes if matches_subscriber(c, sub)]
        if not matched:
            continue
        try:
            html = build_email_html(matched, sub, new_data.get("updated_at", ""))
            send_email(sub["email"], html, new_data.get("updated_at", ""), len(matched))
            log.info("送信: %s (%d件)", sub["email"], len(matched))
            sent += 1
        except Exception as e:
            log.error("送信失敗 %s: %s", sub["email"], e)

    log.info("通知完了: %d/%d件送信", sent, len(subscribers))


if __name__ == "__main__":
    main()
