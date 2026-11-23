create unique index if not exists chat_messages_session_turn_role_uidx
  on bot_private.chat_messages (session_id, role, (payload->>'request_id'))
  where nullif(payload->>'request_id', '') is not null;

create or replace function public.bot_chat_action(
  p_app_token text, p_guest_token text, p_action text,
  p_session_id uuid default null, p_payload jsonb default '{}'::jsonb
) returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  v_guest uuid;
  v_message uuid;
  v_request_id text;
  v_result jsonb;
begin
  if not exists (select 1 from bot_private.admin_gate where id = true
    and token_sha256 = encode(extensions.digest(coalesce(p_app_token,''),'sha256'),'hex')) then
    raise exception 'not authorized' using errcode = '28000';
  end if;
  if p_guest_token is null or length(p_guest_token) < 32 then
    raise exception 'invalid guest' using errcode = '22023';
  end if;
  if p_action = 'create_guest' then
    insert into bot_private.chat_guests(token_sha256)
    values (encode(extensions.digest(p_guest_token,'sha256'),'hex'))
    on conflict (token_sha256) do nothing;
  end if;
  select id into v_guest from bot_private.chat_guests
    where token_sha256 = encode(extensions.digest(p_guest_token,'sha256'),'hex');
  if v_guest is null then raise exception 'unknown guest' using errcode = '28000'; end if;
  if p_action = 'create_guest' then return jsonb_build_object('ok',true); end if;
  if p_action = 'list_sessions' then
    select coalesce(jsonb_agg(jsonb_build_object('id',id,'title',title,'updated_at',updated_at) order by updated_at desc),'[]'::jsonb)
      into v_result from bot_private.chat_sessions where guest_id = v_guest;
    return v_result;
  end if;
  if p_action = 'create_session' then
    insert into bot_private.chat_sessions(guest_id) values (v_guest)
    returning jsonb_build_object('id',id,'title',title,'updated_at',updated_at) into v_result;
    return v_result;
  end if;
  if p_session_id is null or not exists (select 1 from bot_private.chat_sessions where id=p_session_id and guest_id=v_guest) then
    raise exception 'session not found' using errcode='22023';
  end if;
  if p_action = 'load_session' then
    select coalesce(jsonb_agg(jsonb_build_object('id',id,'role',role,'content',content,'payload',payload,'created_at',created_at) order by created_at,id),'[]'::jsonb)
      into v_result from (select * from bot_private.chat_messages where session_id=p_session_id order by created_at desc,id desc limit 100) m;
    return v_result;
  end if;
  if p_action = 'save_message' then
    if coalesce(p_payload->>'role','') not in ('user','assistant')
       or length(coalesce(p_payload->>'content','')) > 10000 then
      raise exception 'invalid message' using errcode='22023';
    end if;
    v_request_id := nullif(p_payload->'payload'->>'request_id','');
    if v_request_id is null then
      insert into bot_private.chat_messages(session_id,role,content,payload)
        values (p_session_id,p_payload->>'role',coalesce(p_payload->>'content',''),coalesce(p_payload->'payload','{}'::jsonb))
        returning id into v_message;
    else
      insert into bot_private.chat_messages(session_id,role,content,payload)
        values (p_session_id,p_payload->>'role',coalesce(p_payload->>'content',''),coalesce(p_payload->'payload','{}'::jsonb))
        on conflict do nothing returning id into v_message;
      if v_message is null then
        select id into v_message from bot_private.chat_messages
          where session_id=p_session_id and role=p_payload->>'role' and payload->>'request_id'=v_request_id
          order by created_at,id limit 1;
      end if;
    end if;
    update bot_private.chat_sessions set updated_at=now(),
      title=case when title='แชทใหม่' and p_payload->>'role'='user' then left(coalesce(nullif(p_payload->>'content',''),'แชทใหม่'),40) else title end
      where id=p_session_id;
    return jsonb_build_object('id',v_message);
  end if;
  if p_action = 'save_product' then
    insert into bot_private.chat_products(session_id,item_id,product_name,source,is_anchor,card_snapshot)
      values (p_session_id,left(p_payload->>'item_id',100),left(p_payload->>'product_name',200),p_payload->>'source',coalesce((p_payload->>'is_anchor')::boolean,false),coalesce(p_payload->'card_snapshot','{}'::jsonb));
    return jsonb_build_object('ok',true);
  end if;
  if p_action = 'product_anchor' then
    select jsonb_build_object('item_id',item_id,'product_name',product_name,'source',source) into v_result
      from bot_private.chat_products where session_id=p_session_id and is_anchor order by created_at desc,id desc limit 1;
    return coalesce(v_result,'{}'::jsonb);
  end if;
  if p_action = 'save_attachment' then
    insert into bot_private.chat_attachments(session_id,object_path,mime_type,byte_size)
      values(p_session_id,p_payload->>'object_path',p_payload->>'mime_type',(p_payload->>'byte_size')::bigint)
      returning jsonb_build_object('id',id) into v_result;
    return v_result;
  end if;
  if p_action = 'get_attachment' then
    select jsonb_build_object('id',id,'object_path',object_path,'mime_type',mime_type,
      'byte_size',byte_size,'status',status,'vision_description',vision_description)
      into v_result from bot_private.chat_attachments
      where session_id=p_session_id and id=(p_payload->>'id')::uuid;
    return coalesce(v_result,'{}'::jsonb);
  end if;
  if p_action = 'update_vision' then
    update bot_private.chat_attachments set status=p_payload->>'status',
      vision_model=p_payload->>'model', vision_description=left(p_payload->>'description',4000)
      where session_id=p_session_id and id=(p_payload->>'id')::uuid
      returning jsonb_build_object('id',id) into v_result;
    return coalesce(v_result,'{}'::jsonb);
  end if;
  if p_action = 'live_agent_settings' then
    return (select jsonb_build_object('action',faq_liveagent_action) from bot_private.chat_settings where id=true);
  end if;
  raise exception 'invalid action' using errcode='22023';
end $$;

revoke all on function public.bot_chat_action(text,text,text,uuid,jsonb) from public;
grant execute on function public.bot_chat_action(text,text,text,uuid,jsonb) to anon;
