# 製造たばこ 小売定価データベース

財務省「製造たばこの小売定価の認可」PDFを自動取得・解析し、価格変更をメールで通知する静的Webアプリ。

**公開URL**: https://ddaiki.github.io/tobacco-price-database/  
**GitHubリポジトリ**: https://github.com/Ddaiki/tobacco-price-database

---

## 機能

- 約4,200件の製造たばこ価格データを検索・フィルタ表示
- カテゴリ別フィルタ（加熱式・葉巻・紙巻・パイプ・西洋パイプ・シーシャ・かぎ・刻み）
- 価格履歴表示・PDFソースリンク
- **価格更新通知メール**（カテゴリ/製造国/ブランドキーワードでフィルタ登録可）
- 新規登録者へウェルカムメール自動送信
- 新規登録時にオーナーへ通知メール自動送信
- 配信停止リンク（メール内URLから1クリック解除）

---

## アーキテクチャ

```
[GitHub Pages]          [Supabase]              [Resend]
index.html         →   subscribers テーブル  →  メール送信
(登録フォーム)          (ユーザーDB・RLS)
                              ↑↓
                    [Supabase Edge Function]
                    on-subscriber-created
                    (登録時: ウェルカム＋オーナー通知)

[GitHub Actions] 毎週月曜 9:00 JST
  update_prices.py   → prices.json 更新 → git push
  notify_subscribers.py → 差分検出 → 条件一致ユーザーへ送信
```

---

## 技術スタック

| レイヤー | 技術 |
|---|---|
| フロントエンド | HTML/CSS/JavaScript（静的・フレームワークなし） |
| ホスティング | GitHub Pages |
| データ更新 | Python + GitHub Actions（週次自動実行） |
| PDF解析 | PyMuPDF（fitz）`get_text('blocks')` |
| ユーザーDB | Supabase（PostgreSQL + Row Level Security） |
| メール送信 | Resend API |
| 登録時処理 | Supabase Edge Function（Deno/TypeScript） |

---

## ファイル構成

```
tobacco-price-database/
├── index.html                          # フロントエンド（全機能をこの1ファイルに集約）
├── tobacco-prices/
│   ├── data/
│   │   └── prices.json                 # 全商品データ（GitHub Actionsが自動更新）
│   └── scripts/
│       ├── update_prices.py            # MOFサイトからPDF取得・価格更新
│       ├── notify_subscribers.py       # 価格差分検出・メール送信
│       ├── requirements.txt            # Python依存パッケージ
│       └── ms_mincho_gid.json          # MS明朝フォントのGlyphIDマップ
├── supabase/
│   ├── functions/
│   │   └── on-subscriber-created/
│   │       └── index.ts               # Edge Function（登録時メール送信）
│   └── migrations/
│       └── 001_create_subscribers.sql # DBスキーマ
└── .github/
    └── workflows/
        └── update-prices.yml          # 週次自動更新ワークフロー
```

---

## データ構造

### prices.json

```json
{
  "updated_at": "2026-05-01",
  "products": [
    {
      "category": "葉巻たばこ",
      "name": "商品名",
      "product_type": "184mm 1本",
      "country": "キューバ",
      "current_price": 8000,
      "last_changed": "2024-08-28",
      "discontinued": false,
      "price_history": [
        { "price": 8000, "date": "2024-08-28", "pdf": "20240828_kouriteika.pdf" }
      ]
    }
  ]
}
```

### subscribers テーブル（Supabase）

| カラム | 型 | 説明 |
|---|---|---|
| id | uuid | 主キー |
| email | text | メールアドレス（unique） |
| category_filters | text[] | 空=全カテゴリ |
| country_filters | text[] | 空=全製造国 |
| name_keywords | text[] | 空=全ブランド（部分一致） |
| confirmed | boolean | 常にtrue（簡易実装） |
| unsubscribe_token | text | 配信停止用トークン |
| created_at | timestamptz | 登録日時 |

---

## 環境変数・Secrets

### GitHub Actions Secrets

| 変数名 | 説明 |
|---|---|
| `SUPABASE_URL` | `https://vqzflvuiiertnsuonvny.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabaseサービスロールキー（sb_secret_...） |
| `RESEND_API_KEY` | Resend APIキー（re_...） |
| `SITE_URL` | `https://ddaiki.github.io/tobacco-price-database` |

### Supabase Edge Function Secrets

```bash
supabase secrets set RESEND_API_KEY=re_... OWNER_EMAIL=owner@example.com SITE_URL=https://...
```

---

## セットアップ手順

### 1. Supabase テーブル作成

Supabase SQL Editor で `supabase/migrations/001_create_subscribers.sql` を実行。

### 2. Edge Function デプロイ

```bash
supabase link --project-ref vqzflvuiiertnsuonvny
supabase functions deploy on-subscriber-created
supabase secrets set RESEND_API_KEY=... OWNER_EMAIL=... SITE_URL=...
```

### 3. Database Webhook 設定

Supabase → Integrations → Database Webhooks → Create  
- Table: `subscribers`、Event: `INSERT`、Function: `on-subscriber-created`

### 4. GitHub Actions Secrets 設定

リポジトリ → Settings → Secrets and variables → Actions に4つ追加。

---

## 既知の制限・今後の課題

- **Resendの送信先制限**: 独自ドメイン未設定の場合、送信者自身のメールアドレスにしか送れない（無料プラン制限）。本格運用には独自ドメインのDNS設定が必要。
- **パイプたばこのサブカテゴリ**: シーシャ/西洋パイプの判定はブランドリスト・製造国・キーワードによるヒューリスティック。新ブランドは手動追加が必要。
- **メール確認フロー未実装**: 現状は登録即確認済み扱い。スパム登録対策として将来的にダブルオプトインを検討。
- **LINEなど他チャネル**: LINE Notifyが2025年3月廃止のため未実装。需要があればLINE Messaging APIまたはDiscord Webhookを検討。

---

## PDF解析について

財務省のPDFはMS明朝フォントを使用。`pdfminer`や`OCR`では文字化けするため、`PyMuPDF`（fitz）の`get_text('blocks')`が唯一の正常動作方法。`ms_mincho_gid.json`はMS-Mincho-90ms-RKSJ-Hフォントの文字マッピング用。

---

## 更新フロー

1. GitHub Actions が毎週月曜 0:00 UTC（9:00 JST）に起動
2. `update_prices.py` が財務省サイトをスクレイプしてPDFを解析、`prices.json`を更新
3. 変更があれば自動コミット＆プッシュ
4. `notify_subscribers.py` が差分を検出し、フィルター条件が一致する登録者にメール送信
5. GitHub Pages が自動的に最新の `index.html` + `prices.json` を配信
