/**
 * on-subscriber-created Edge Function
 * subscribersテーブルへのINSERT時にDatabase Webhookで起動。
 * confirmed=false のまま確認メールのみ送信する（ウェルカムメール・オーナー通知は本登録後）。
 */
import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const RESEND_API_KEY = Deno.env.get("RESEND_API_KEY")!;
const SITE_URL       = (Deno.env.get("SITE_URL") ?? "").replace(/\/$/, "");

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

function buildConfirmationEmail(email: string, confirmUrl: string): string {
  return `<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;font-family:'Hiragino Sans','Meiryo',sans-serif;background:#f4f6f8;padding:20px">
<div style="max-width:520px;margin:0 auto;background:white;border-radius:8px;overflow:hidden">
  <div style="background:#1a237e;padding:16px 20px">
    <h1 style="margin:0;color:white;font-size:16px">製造たばこ 小売定価データベース</h1>
    <p style="margin:4px 0 0;color:rgba(255,255,255,.7);font-size:12px">メールアドレスの確認</p>
  </div>
  <div style="padding:24px 20px">
    <p style="margin:0 0 8px;font-size:14px">価格更新通知への登録ありがとうございます。</p>
    <p style="margin:0 0 24px;font-size:14px">下のボタンをクリックして本登録を完了してください。</p>
    <div style="text-align:center;margin-bottom:24px">
      <a href="${confirmUrl}"
         style="background:#1a237e;color:white;padding:12px 32px;border-radius:6px;
                text-decoration:none;font-size:15px;font-weight:bold;display:inline-block">
        本登録を完了する
      </a>
    </div>
    <p style="margin:0;font-size:11px;color:#999;text-align:center">
      このメールに心当たりがない場合は無視してください。登録は完了しません。
    </p>
  </div>
</div>
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

    const token      = record.unsubscribe_token as string;
    const confirmUrl = `${SITE_URL}?confirm=${token}`;

    await sendEmail(
      record.email as string,
      "【確認】製造たばこ価格更新通知の登録",
      buildConfirmationEmail(record.email as string, confirmUrl),
    );

    return new Response(JSON.stringify({ ok: true }), {
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    console.error(err);
    return new Response(String(err), { status: 500 });
  }
});
