# 製造たばこ小売定価データベース — プロジェクト現状まとめ

> 作成日: 2026-05-08  
> 共有目的: AIへのコンテキスト引き継ぎ用

---

## サービス概要

**財務省「製造たばこの小売定価の認可」** のPDFを毎週自動スクレイピングし、ブランド・製造国・カテゴリで絞り込み検索できる静的Webアプリ。

- **公開URL**: https://ddaiki.github.io/tobacco-price-database/
- **リポジトリ**: https://github.com/Ddaiki/tobacco-price-database
- **データ最終更新**: 2026-05-05
- **作者Twitter**: @Ddaiki

---

## 技術スタック

| レイヤー | 技術 |
|----------|------|
| フロントエンド | HTML / CSS / JavaScript（フレームワークなし・単一ファイル index.html） |
| ホスティング | GitHub Pages |
| データ更新 | Python + GitHub Actions（毎週月曜 9:00 JST 自動実行） |
| PDF解析 | PyMuPDF（fitz）`get_text('blocks')` ※pdfminer/OCRは文字化けするため使用不可 |
| ユーザーDB | Supabase（PostgreSQL + Row Level Security） |
| メール送信 | Resend API |
| 登録時処理 | Supabase Edge Function（Deno / TypeScript） |

---

## データ統計（2026-05-08時点）

| 指標 | 値 |
|------|----|
| 総商品数 | 4,247件 |
| 葉巻たばこ | 1,964件 |
| パイプたばこ（西洋パイプ・シーシャ含む） | 1,846件 |
| 加熱式たばこ | 210件 |
| 紙巻たばこ | 159件 |
| かぎたばこ | 60件 |
| 刻みたばこ | 8件 |
| ユニークブランド数 | 464 |
| known_brands.json 登録ブランド数 | 302（brands[]） + 54（multi_segment） + 65（normalization） |
| brand==name 残存件数 | 47件（元464件 → 90%削減済み） |

---

## ファイル構成

```
tobacco-price-database/
├── index.html                          # フロントエンド全機能（1ファイル完結）
├── sitemap.xml                         # 検索エンジン向けサイトマップ
├── robots.txt                          # クローラー設定
├── README.md                           # 一般ユーザー向け説明
├── tobacco-prices/
│   ├── data/
│   │   └── prices.json                 # 全商品データ（GitHub Actionsが自動更新）
│   └── scripts/
│       ├── update_prices.py            # MOFサイトからPDF取得・価格更新・ブランド抽出
│       ├── notify_subscribers.py       # 価格差分検出・メール送信
│       ├── known_brands.json           # ブランド名正規化マスター（最重要）
│       ├── brand_overrides.json        # 手動ブランド上書きマップ
│       ├── ms_mincho_gid.json          # MS明朝フォントのGlyphIDマップ
│       └── requirements.txt
├── supabase/
│   ├── functions/on-subscriber-created/index.ts  # 登録時メール Edge Function
│   └── migrations/001_create_subscribers.sql
└── .github/workflows/update-prices.yml
```

---

## prices.json の構造

```json
{
  "updated_at": "2026-05-05",
  "products": [
    {
      "category": "葉巻たばこ",
      "name": "コイーバ ロブスト",
      "product_type": "124mm 1本",
      "country": "キューバ",
      "current_price": 4000,
      "last_changed": "2024-08-28",
      "discontinued": false,
      "brand": "コイーバ",
      "subcategory": null,
      "pdf_url": "https://www.mof.go.jp/.../20240828_kouriteika.pdf",
      "history": [
        { "price": 4000, "date": "2024-08-28", "pdf": "20240828_kouriteika.pdf" }
      ]
    }
  ]
}
```

**brand・subcategory フィールドは prices.json には保存せず、ページロード時に `enrich_products()` で動的付与。**

---

## ブランド抽出ロジック（update_prices.py）

`extract_brand(name)` の処理順：

1. **Step 1**: `known_brands.json` の全ブランド名（ja・en）でプレフィックスマッチング（長い順）
   - 単語境界チェック: スペース・`・`・ASCII→日本語・日本語→ASCII・`(`  
   - マッチ後 `brand_normalization` で正規化
2. **Step 2**: フォールバックヒューリスティック
   - `・` 区切り → 先頭セグメント
   - 全大文字単語の連続（AL FAKHER 等）
   - 2語目が数字・括弧・スキップワードなら1語目だけ
   - それ以外は「1語目 2語目」

### known_brands.json の構造

```json
{
  "brands": [
    { "ja": "コイーバ", "en": "Cohiba", "category": "葉巻たばこ", "origin": "キューバ" }
  ],
  "multi_segment_brand_names": {
    "ロメオ・Y・ジュリエッタ": "Romeo y Julieta"
  },
  "brand_normalization": {
    "Montecristo": "モンテクリスト",
    "アークローヤル": "アーク・ローヤル"
  }
}
```

---

## サブカテゴリ判定（パイプたばこ）

パイプたばこは `subcategory` で **シーシャ** / **西洋パイプ** に分類。判定優先順位：

1. ブランド名が `_SHISHA_BRANDS` リストに含まれる → シーシャ
2. 製造国が `_SHISHA_COUNTRIES`（UAE・トルコ・ヨルダン等）→ シーシャ
3. 製造国が `_WESTERN_COUNTRIES`（デンマーク・英国・ドイツ等）→ 西洋パイプ
4. 商品名にシーシャキーワード（HOOKAH・MOLASSES等）→ シーシャ
5. 商品名に西洋パイプキーワード（MIXTURE・BLEND・FLAKE等）→ 西洋パイプ
6. 未判定 → `null`

---

## フロントエンド主要機能（index.html）

### フィルター
- **カテゴリボタン**: すべて・加熱式・葉巻・紙巻・西洋パイプ・シーシャ・かぎ・刻み・廃盤
- **テキスト検索**: 商品名・ブランド名をリアルタイム検索
- **製造国プルダウン**: カテゴリ選択連動、`<optgroup>` でカナ行/アルファベット別グループ化
- **ブランドプルダウン**: カテゴリ+製造国に連動、同様にグループ化

### 表示
- テーブル行クリック → 価格履歴モーダル
- ブランドタグクリック → ブランドフィルター適用
- PDFバッジ → 財務省PDFへ直リンク
- ソート: 商品名・価格（高/安）・更新日（新/旧）・カテゴリ

### 通知登録
- カテゴリ・製造国・ブランドキーワードでフィルタ条件を指定して登録
- Supabase `register_subscriber` RPC で保存
- **現在メール送信は準備中**（独自ドメイン未取得のため）

---

## 現在の既知課題・未対応事項

| 課題 | 状況 |
|------|------|
| メール通知送信 | 独自ドメイン未取得のため準備中。登録は可能 |
| brand==name 残り47件 | 西洋パイプ37件（ブレンド名=ブランド名で実質正しい）、葉巻6件（単品ブランド）、紙巻3件・刻み1件（単品名） |
| Google Search Console 未登録 | サイトマップ送信未実施 |
| 廃盤フラグ | prices.json 上は全4247件が discontinued:false（廃盤判定ロジック未実装） |

---

## SEO対応状況

| 対応 | 実装済み |
|------|----------|
| meta description / keywords | ✅ |
| Open Graph (og:*) | ✅ |
| Twitter Card | ✅ |
| JSON-LD 構造化データ（WebApplication + Dataset） | ✅ |
| canonical URL | ✅ |
| sitemap.xml | ✅ |
| robots.txt | ✅ |
| Google Search Console 登録 | ❌ 未実施 |
| 独自ドメイン | ❌ 未取得 |

---

## 環境変数

### GitHub Actions Secrets

| 変数名 | 用途 |
|--------|------|
| `SUPABASE_URL` | `https://vqzflvuiiertnsuonvny.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase操作用 |
| `RESEND_API_KEY` | メール送信API |
| `SITE_URL` | `https://ddaiki.github.io/tobacco-price-database` |

---

## 重要な技術的注意事項

- **PDF解析**: `fitz.get_text('blocks')` のみ正常動作。`pdfminer` は `(cid:XXXX)` 化け、OCRも不可
- **パスの大文字小文字**: リポジトリは `/Users/daiki/Projects/tobacco-price-database`（P が大文字）。`/projects/`（小文字）は別ディレクトリ
- **prices.json の brand フィールド**: ファイルには保存しない設計。JS側の `enrich_products()` 相当処理でランタイム付与
- **known_brands.json の編集**: 追加後は必ず `enrich_products()` を再実行して prices.json を更新してからコミット
