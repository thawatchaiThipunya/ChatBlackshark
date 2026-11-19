alter table public.bot_products
  add column if not exists category text;

update public.bot_products
set category = nullif(btrim(regexp_replace(
  split_part(description, E'\n', 1),
  '^(ประเภทสินค้า|ประเภท):[[:space:]]*',
  ''
)), '')
where description ~ '^(ประเภทสินค้า|ประเภท):'
  and category is distinct from nullif(btrim(regexp_replace(
    split_part(description, E'\n', 1),
    '^(ประเภทสินค้า|ประเภท):[[:space:]]*',
    ''
  )), '');
