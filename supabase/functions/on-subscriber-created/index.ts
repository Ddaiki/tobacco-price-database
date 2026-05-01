import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const RESEND_API_KEY = Deno.env.get("RESEND_API_KEY")!;
const OWNER_EMAIL    = Deno.env.get("OWNER_EMAIL")!;
const SITE_URL       = (Deno.env.get("SITE_URL") ?? "").replace(/\/$/, "");

const CATEGORY_DISPLAY: Record<string, string> = {
  "加熱式たばこ": "加熱式",
  "葉巻たばこ":   "葉巻",
  "紙巻たばこ":   "紙巻",
  "パイプたばこ": "パイプ",
  "西洋パイプ":   "西洋パイプ",
  "シーシャ":     "シーシャ",
  "かぎたばこ":   "かぎ",
  "刻みたばこ":   "刻み",
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
    const body = await res.text();
    throw new Error(`Resend error: ${res.status} ${body}`);
  }
}

function buildWelcomeEmail(record: Record<string, unknown>): string {
  const cats      = (record.category_filters as string[] ?? []).map(c => CATEGORY_DISPLAY[c] ?? c);
  const countries = record.country_filters as string[] ?? [];
  const keywords  = record.name_keywords  as string[] ?? [];

  const filterRows = [
    `<tr><td style="padding:6px 12px;color:#666;font-size:13px">カテゴリ</td>
     <td style="padding:6px 12px;font-size:13px">${cats.length ? cats.join("、") : "すべて"}</td></tr>`,
    `<tr style="background:#fafafa"><td style="padding:6px 12px;color:#666;font-size:13px">製造国</td>
     <td style="padding:6px 12px;font-size:13px">${countries.length ? countries.join("、") : "すべて"}</td></tr>`,
    `<tr><td style="padding:6px 12px;color:#666;font-size:13px">ブランド</td>
     <td style="padding:6px 12px;font-size:13px">${keywords.length ? keywords.join("、") : "すべて"}</td></tr>`,
  ].join("");

  const token    = record.unsubscribe_token as string ?? "";
  const unsubUrl = `${SITE_URL}?unsubscribe=${token}`;

  return `<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;font-family:'Hiragino Sans','Meiryo',sans-serif;background:#f4f6f8;padding:20px">
<div style="max-width:560px;margin:0 auto;background:white;border-radius:8px;overflow:hidden">
  <div style="background:#1a237e;padding:16px 20px">
    <h1 style="margin:0;color:white;font-size:16px">製造たばこ 小売定価データベース</h1>
    <p style="margin:4px 0 0;color:rgba(255,255,255,.7);font-size:12px">価格更新通知 登録完了</p>
  </div>
  <div style="padding:20px">
    <p style="margin:0 0 16px;font-size:14px">価格更新通知への登録が完了しました。<br>以下の条件に一致する更新があったときにメールをお送りします。</p>
    <table style="width:100%;border-collapse:collapse;border:1px solid #eee;border-radius:6px;overflow:hidden">
      <thead>
        <tr style="background:#e8eaf6">
          <th colspan="2" style="padding:8px 12px;text-align:left;font-size:12px;color:#283593">通知条件</th>
        </tr>
      </thead>
      <tbody>${filterRows}</tbody>
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

function buildOwnerNotificationEmail(record: Record<string, unknown>): string {
  const email     = record.email as string;
  const cats      = (record.category_filters as string[] ?? []).map(c => CATEGORY_DISPLAY[c] ?? c);
  const countries = record.country_filters as string[] ?? [];
  const keywords  = record.name_keywords  as string[] ?? [];
  const createdAt = record.created_at as string ?? "";

  return `<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"></head>
<body style="font-family:'Hiragino Sans','Meiryo',sans-serif;padding:20px;max-width:480px">
  <h2 style="color:#1a237e;font-size:15px;margin:0 0 12px">新規通知登録がありました</h2>
  <table style="border-collapse:collapse;width:100%;font-size:13px">
    <tr><td style="padding:6px 12px;color:#666;background:#f5f5f5;width:90px">メール</td>
        <td style="padding:6px 12px">${email}</td></tr>
    <tr><td style="padding:6px 12px;color:#666">カテゴリ</td>
        <td style="padding:6px 12px">${cats.length ? cats.join("、") : "すべて"}</td></tr>
    <tr><td style="padding:6px 12px;color:#666;background:#f5f5f5">製造国</td>
        <td style="padding:6px 12px;background:#f5f5f5">${countries.length ? countries.join("、") : "すべて"}</td></tr>
    <tr><td style="padding:6px 12px;color:#666">ブランド</td>
        <td style="padding:6px 12px">${keywords.length ? keywords.join("、") : "すべて"}</td></tr>
    <tr><td style="padding:6px 12px;color:#666;background:#f5f5f5">登録日時</td>
        <td style="padding:6px 12px;background:#f5f5f5">${createdAt}</td></tr>
  </table>
</body>
</html>`;
}

serve(async (req) => {
  try {
    const payload = await req.json();
    const record  = payload.record as Record<string, unknown>;

    if (!record?.email) {
      return new Response("no email", { status: 400 });
    }

    await sendEmail(
      record.email as string,
      "【登録完了】製造たばこ価格更新通知",
      buildWelcomeEmail(record),
    );

    await sendEmail(
      OWNER_EMAIL,
      `新規登録: ${record.email as string}`,
      buildOwnerNotificationEmail(record),
    );

    return new Response(JSON.stringify({ ok: true }), {
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    console.error(err);
    return new Response(String(err), { status: 500 });
  }
});
