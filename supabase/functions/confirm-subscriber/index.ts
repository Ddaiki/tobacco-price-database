/**
 * confirm-subscriber Edge Function
 * フロントエンドの確認URLから呼ばれ、以下を行う:
 *   1. unsubscribe_token でレコードを特定し confirmed=true に更新
 *   2. ウェルカムメールを登録者へ送信
 *   3. オーナーへ新規登録通知を送信
 *
 * SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY は Supabase が自動注入する。
 */
import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const SUPABASE_URL      = Deno.env.get("SUPABASE_URL")!;
const SERVICE_ROLE_KEY  = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const RESEND_API_KEY    = Deno.env.get("RESEND_API_KEY")!;
const OWNER_EMAIL       = Deno.env.get("OWNER_EMAIL")!;
const SITE_URL          = (Deno.env.get("SITE_URL") ?? "").replace(/\/$/, "");

const CATEGORY_DISPLAY: Record<string, string> = {
  "加熱式たばこ": "加熱式", "葉巻たばこ": "葉巻", "紙巻たばこ": "紙巻",
  "パイプたばこ": "パイプ", "西洋パイプ": "西洋パイプ", "シーシャ": "シーシャ",
  "かぎたばこ": "かぎ",   "刻みたばこ": "刻み",
};

async function sendEmail(to: string, subject: string, html: string) {
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: "たばこ価格通知 <onboarding@resend.dev>",
      to: [to],
      subject,
      html,
    }),
  });
  if (!res.ok) {
    throw new Error(`Resend error: ${res.status} ${await res.text()}`);
  }
}

/** confirmed=true に更新して登録者レコードを返す。未登録・更新済みはnullを返す。 */
async function confirmAndFetch(token: string): Promise<Record<string, unknown> | null> {
  const url = `${SUPABASE_URL}/rest/v1/subscribers` +
    `?unsubscribe_token=eq.${encodeURIComponent(token)}&confirmed=eq.false` +
    `&select=*`;

  const headers = {
    "apikey": SERVICE_ROLE_KEY,
    "Authorization": `Bearer ${SERVICE_ROLE_KEY}`,
    "Content-Type": "application/json",
    "Prefer": "return=representation",
  };

  // PATCH で confirmed=true
  const patchRes = await fetch(url, {
    method: "PATCH",
    headers,
    body: JSON.stringify({ confirmed: true }),
  });

  if (!patchRes.ok) return null;
  const rows = await patchRes.json() as Record<string, unknown>[];
  return rows.length > 0 ? rows[0] : null;
}

function buildWelcomeEmail(record: Record<string, unknown>): string {
  const cats      = (record.category_filters as string[] ?? []).map(c => CATEGORY_DISPLAY[c] ?? c);
  const countries = record.country_filters as string[] ?? [];
  const keywords  = record.name_keywords   as string[] ?? [];
  const token     = record.unsubscribe_token as string ?? "";
  const unsubUrl  = `${SITE_URL}?unsubscribe=${token}`;

  const rows = [
    ["カテゴリ",     cats.length      ? cats.join("、")      : "すべて"],
    ["製造国",       countries.length ? countries.join("、") : "すべて"],
    ["ブランド",     keywords.length  ? keywords.join("、")  : "すべて"],
  ].map(([label, val], i) =>
    `<tr style="${i % 2 === 1 ? "background:#fafafa" : ""}">
      <td style="padding:6px 12px;color:#666;font-size:13px;width:90px">${label}</td>
      <td style="padding:6px 12px;font-size:13px">${val}</td>
    </tr>`
  ).join("");

  return `<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;font-family:'Hiragino Sans','Meiryo',sans-serif;background:#f4f6f8;padding:20px">
<div style="max-width:520px;margin:0 auto;background:white;border-radius:8px;overflow:hidden">
  <div style="background:#1a237e;padding:16px 20px">
    <h1 style="margin:0;color:white;font-size:16px">製造たばこ 小売定価データベース</h1>
    <p style="margin:4px 0 0;color:rgba(255,255,255,.7);font-size:12px">価格更新通知 登録完了</p>
  </div>
  <div style="padding:20px">
    <p style="margin:0 0 16px;font-size:14px">本登録が完了しました。以下の条件に一致する更新があったときにメールをお送りします。</p>
    <table style="width:100%;border-collapse:collapse;border:1px solid #eee">
      <thead>
        <tr style="background:#e8eaf6">
          <th colspan="2" style="padding:8px 12px;text-align:left;font-size:12px;color:#283593">通知条件</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    <div style="margin-top:20px;text-align:center">
      <a href="${SITE_URL}" style="background:#1a237e;color:white;padding:10px 24px;
         border-radius:6px;text-decoration:none;font-size:14px">データベースを見る</a>
    </div>
  </div>
  <div style="padding:12px 20px;border-top:1px solid #eee;text-align:center">
    <a href="${unsubUrl}" style="font-size:11px;color:#999;text-decoration:none">配信停止</a>
  </div>
</div>
</body>
</html>`;
}

function buildOwnerEmail(record: Record<string, unknown>): string {
  const email     = record.email as string;
  const cats      = (record.category_filters as string[] ?? []).map(c => CATEGORY_DISPLAY[c] ?? c);
  const countries = record.country_filters as string[] ?? [];
  const keywords  = record.name_keywords   as string[] ?? [];

  return `<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"></head>
<body style="font-family:'Hiragino Sans','Meiryo',sans-serif;padding:20px;max-width:480px">
  <h2 style="color:#1a237e;font-size:15px;margin:0 0 12px">新規登録者が本登録を完了しました</h2>
  <table style="border-collapse:collapse;width:100%;font-size:13px">
    <tr><td style="padding:6px 12px;color:#666;background:#f5f5f5;width:90px">メール</td>
        <td style="padding:6px 12px;background:#f5f5f5">${email}</td></tr>
    <tr><td style="padding:6px 12px;color:#666">カテゴリ</td>
        <td style="padding:6px 12px">${cats.length ? cats.join("、") : "すべて"}</td></tr>
    <tr><td style="padding:6px 12px;color:#666;background:#f5f5f5">製造国</td>
        <td style="padding:6px 12px;background:#f5f5f5">${countries.length ? countries.join("、") : "すべて"}</td></tr>
    <tr><td style="padding:6px 12px;color:#666">ブランド</td>
        <td style="padding:6px 12px">${keywords.length ? keywords.join("、") : "すべて"}</td></tr>
  </table>
</body>
</html>`;
}

serve(async (req) => {
  try {
    const { token } = await req.json() as { token: string };
    if (!token) {
      return new Response(JSON.stringify({ ok: false, reason: "no token" }), { status: 400 });
    }

    const record = await confirmAndFetch(token);

    // トークン不正・既に確認済み → フロントには成功を返す（列挙攻撃対策）
    if (!record) {
      return new Response(JSON.stringify({ ok: true, alreadyConfirmed: true }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    // ウェルカムメール → 登録者
    await sendEmail(
      record.email as string,
      "【登録完了】製造たばこ価格更新通知",
      buildWelcomeEmail(record),
    );

    // 通知メール → オーナー
    await sendEmail(
      OWNER_EMAIL,
      `新規本登録: ${record.email as string}`,
      buildOwnerEmail(record),
    );

    return new Response(JSON.stringify({ ok: true }), {
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    console.error(err);
    return new Response(String(err), { status: 500 });
  }
});
