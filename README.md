# 製造たばこ小売定価データベース

> 財務省が認可した製造たばこの小売定価を、ブランド・製造国・カテゴリで絞り込み検索できる無料Webサービスです。

**[→ サイトを開く](https://ddaiki.github.io/tobacco-price-database/)**

[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-公開中-brightgreen)](https://ddaiki.github.io/tobacco-price-database/)
[![Data Update](https://img.shields.io/badge/更新-毎週自動-blue)](https://github.com/Ddaiki/tobacco-price-database/actions)
[![Products](https://img.shields.io/badge/収録商品-4%2C200件以上-orange)](https://ddaiki.github.io/tobacco-price-database/)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

---

## どんなサービス？

**財務省「製造たばこの小売定価の認可」** のPDFデータを毎週自動収集し、検索しやすい形で公開しています。

- 葉巻（シガー）・紙巻たばこ・加熱式たばこ・シーシャ・パイプたばこ・かぎたばこ・刻みたばこ
- キューバ産、ニカラグア産、ホンジュラス産など製造国でフィルタリング
- コイーバ、モンテクリスト、ダビドフ、マールボロなど国内流通ブランドを網羅
- 価格改定の履歴・財務省PDFへの直リンク付き

> たばこ小売価格の調査・比較や、葉巻愛好家のコレクション管理などにご活用ください。

---

## 主な機能

| 機能 | 詳細 |
|------|------|
| 価格検索 | 商品名・ブランド・製造国・カテゴリでフィルタリング |
| 価格履歴 | 商品ごとの改定日・旧価格を一覧表示 |
| PDFリンク | 財務省認可PDFを直接参照可能 |
| 廃盤表示 | 現行品・廃盤品を切り替えて表示 |
| 価格更新通知 | メールアドレスを登録すると更新をお知らせ（準備中） |
| 自動更新 | 毎週月曜に財務省サイトをチェック、新データを自動反映 |

---

## スクリーンショット

<!-- スクリーンショットを追加する場合はここに -->
> ※ [サイトを開いてご確認ください](https://ddaiki.github.io/tobacco-price-database/)

---

## データについて

- **データソース**: [財務省 製造たばこの小売定価の認可](https://www.mof.go.jp/policy/tab_salt/topics/kouriteika.html)
- **収録件数**: 4,200件以上（現行品・廃盤品含む）
- **更新頻度**: 毎週月曜 9:00 JST（GitHub Actions で自動実行）
- **対象カテゴリ**: 葉巻たばこ / 紙巻たばこ / 加熱式たばこ / パイプたばこ（西洋パイプ・シーシャ）/ かぎたばこ / 刻みたばこ

---

## フィードバック・要望

不具合の報告や機能要望は [Issues](https://github.com/Ddaiki/tobacco-price-database/issues) にお気軽にどうぞ。

「このブランドが抜けている」「分類が違う」といった情報提供も歓迎です。

---

## 技術スタック

| レイヤー | 技術 |
|----------|------|
| フロントエンド | HTML / CSS / JavaScript（フレームワークなし） |
| ホスティング | GitHub Pages |
| データ更新 | Python + GitHub Actions（週次自動実行） |
| PDF解析 | PyMuPDF（fitz）`get_text('blocks')` |
| ユーザーDB | Supabase（PostgreSQL + Row Level Security） |
| メール送信 | Resend API（独自ドメイン取得後に本格稼働予定） |
| 登録時処理 | Supabase Edge Function（Deno / TypeScript） |

---

## アーキテクチャ

```
[GitHub Pages]          [Supabase]                [Resend]
  index.html      →   subscribers テーブル   →   メール送信
 (登録フォーム)         (ユーザーDB・RLS)
                               ↑↓
                     [Supabase Edge Function]
                     on-subscriber-created
                     (登録時: ウェルカム＋オーナー通知)

[GitHub Actions] 毎週月曜 9:00 JST
  update_prices.py    → prices.json 更新 → git push
  notify_subscribers.py → 差分検出 → 条件一致ユーザーへ送信
```

---

## ファイル構成

```
tobacco-price-database/
├── index.html                          # フロントエンド（全機能をこの1ファイルに集約）
├── sitemap.xml                         # 検索エンジン向けサイトマップ
├── robots.txt                          # クローラー設定
├── tobacco-prices/
│   ├── data/
│   │   └── prices.json                 # 全商品データ（GitHub Actionsが自動更新）
│   └── scripts/
│       ├── update_prices.py            # MOFサイトからPDF取得・価格更新
│       ├── notify_subscribers.py       # 価格差分検出・メール送信
│       ├── known_brands.json           # ブランド名正規化マスター
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

## セルフホスト・開発者向け情報

<details>
<summary>環境変数・Secrets</summary>

### GitHub Actions Secrets

| 変数名 | 説明 |
|--------|------|
| `SUPABASE_URL` | `https://vqzflvuiiertnsuonvny.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabaseサービスロールキー |
| `RESEND_API_KEY` | Resend APIキー |
| `SITE_URL` | `https://ddaiki.github.io/tobacco-price-database` |

### Supabase Edge Function Secrets

```bash
supabase secrets set RESEND_API_KEY=re_... OWNER_EMAIL=owner@example.com SITE_URL=https://...
```

</details>

<details>
<summary>セットアップ手順</summary>

1. **Supabase テーブル作成**: SQL Editor で `supabase/migrations/001_create_subscribers.sql` を実行
2. **Edge Function デプロイ**:
   ```bash
   supabase link --project-ref vqzflvuiiertnsuonvny
   supabase functions deploy on-subscriber-created
   ```
3. **Database Webhook 設定**: Supabase → Database Webhooks → Create（Table: `subscribers`、Event: `INSERT`）
4. **GitHub Actions Secrets 設定**: リポジトリ → Settings → Secrets and variables → Actions に4つ追加

</details>

<details>
<summary>PDF解析の技術メモ</summary>

財務省のPDFはMS明朝フォントを使用。`pdfminer` や OCR では文字化けするため、`PyMuPDF`（fitz）の `get_text('blocks')` が唯一の正常動作方法。`ms_mincho_gid.json` はMS-Mincho-90ms-RKSJ-Hフォントの文字マッピング用。

</details>

---

## 既知の制限

- **メール通知**: 独自ドメイン未設定のため現在は準備中。登録は可能で、ドメイン取得後に送信を開始予定。
- **パイプたばこのサブカテゴリ**: シーシャ/西洋パイプの判定はヒューリスティック。新ブランドは `known_brands.json` への手動追加が必要。

---

*データは財務省公開情報をもとに作成しています。価格の正確性については財務省の認可資料をご確認ください。*
