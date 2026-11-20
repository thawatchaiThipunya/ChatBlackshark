create function public.bot_chat_messages_page(
  p_app_token text, p_guest_token text, p_session_id uuid,
  p_before_created_at timestamptz default null, p_before_id uuid default null,
  p_limit integer default 100
) returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  v_guest uuid;
  v_rows jsonb;
  v_limit integer := least(greatest(coalesce(p_limit, 100), 1), 100);
begin
  if not exists (select 1 from bot_private.admin_gate where id = true
    and token_sha256 = encode(extensions.digest(coalesce(p_app_token,''),'sha256'),'hex')) then
    raise exception 'not authorized' using errcode = '28000';
  end if;
  select id into v_guest from bot_private.chat_guests
    where token_sha256 = encode(extensions.digest(coalesce(p_guest_token,''),'sha256'),'hex');
  if v_guest is null or not exists (
    select 1 from bot_private.chat_sessions where id = p_session_id and guest_id = v_guest
  ) then
    raise exception 'session not found' using errcode = '22023';
  end if;
  select coalesce(jsonb_agg(jsonb_build_object('id',id,'role',role,'content',content,
    'payload',payload,'created_at',created_at) order by created_at desc,id desc),'[]'::jsonb)
  into v_rows from (
    select * from bot_private.chat_messages
    where session_id = p_session_id and
      (p_before_created_at is null or (created_at,id) < (p_before_created_at,p_before_id))
    order by created_at desc,id desc limit v_limit + 1
  ) page;
  return jsonb_build_object('messages', (
    select coalesce(jsonb_agg(value order by position desc),'[]'::jsonb)
    from jsonb_array_elements(v_rows) with ordinality entries(value, position)
    where position <= v_limit
  ), 'has_more', jsonb_array_length(v_rows) > v_limit);
end $$;
revoke all on function public.bot_chat_messages_page(text,text,uuid,timestamptz,uuid,integer) from public;
grant execute on function public.bot_chat_messages_page(text,text,uuid,timestamptz,uuid,integer) to anon;
