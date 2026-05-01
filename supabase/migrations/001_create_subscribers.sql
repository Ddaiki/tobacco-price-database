-- subscribers テーブル
create table if not exists public.subscribers (
  id                uuid        default gen_random_uuid() primary key,
  email             text        not null,
  category_filters  text[]      default '{}',   -- 空 = 全カテゴリ
  country_filters   text[]      default '{}',   -- 空 = 全製造国
  name_keywords     text[]      default '{}',   -- 空 = 全ブランド (部分一致)
  confirmed         boolean     default true,   -- メール確認不要の簡易実装
  unsubscribe_token text        default encode(gen_random_bytes(32), 'hex'),
  created_at        timestamptz default now(),
  constraint subscribers_email_unique unique (email)
);

-- Row Level Security
alter table public.subscribers enable row level security;

-- 匿名ユーザーは INSERT のみ許可（登録）
create policy "anon_can_register" on public.subscribers
  for insert to anon with check (true);

-- 配信停止用 RPC (security definer でRLSをバイパスして削除)
create or replace function public.unsubscribe_by_token(p_token text)
returns boolean
language plpgsql
security definer
as $$
begin
  delete from public.subscribers where unsubscribe_token = p_token;
  return found;
end;
$$;
