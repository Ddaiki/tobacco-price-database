-- ダブルオプトイン対応マイグレーション

-- 1. confirmed のデフォルトを false に変更
ALTER TABLE public.subscribers ALTER COLUMN confirmed SET DEFAULT false;

-- 2. 重複をサイレントに吸収する登録RPC
--    SECURITY DEFINER でRLSをバイパスし、unique違反を外部に漏らさない
CREATE OR REPLACE FUNCTION public.register_subscriber(
  p_email            text,
  p_category_filters text[],
  p_country_filters  text[],
  p_name_keywords    text[]
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  INSERT INTO public.subscribers (email, category_filters, country_filters, name_keywords)
  VALUES (p_email, p_category_filters, p_country_filters, p_name_keywords);
EXCEPTION WHEN unique_violation THEN
  NULL; -- 既存アドレスはサイレントに無視（列挙攻撃対策）
END;
$$;

GRANT EXECUTE ON FUNCTION public.register_subscriber TO anon;

-- 3. 直接INSERTポリシーを削除（RPCに一本化）
DROP POLICY IF EXISTS "anon_can_register" ON public.subscribers;
