alter table bot_private.bot_persona
    add column if not exists bot_name text not null default 'น้อง Black Shark',
    add column if not exists persona_text text not null default
        'พูดสุภาพ เป็นกันเอง ตอบกระชับและครบถ้วน ใส่ใจและช่วยเหลือลูกค้าอย่างเป็นธรรมชาติ',
    add column if not exists friendliness integer not null default 75
        check (friendliness between 0 and 100),
    add column if not exists detail_level integer not null default 50
        check (detail_level between 0 and 100);

create or replace function public.bot_persona_action(
    p_app_token text,
    p_session_token text,
    p_action text,
    p_payload jsonb default '{}'::jsonb
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    v_actor text;
    v_result jsonb;
begin
    if not exists (
        select 1 from bot_private.admin_gate where id = true
          and token_sha256 = encode(extensions.digest(coalesce(p_app_token, ''), 'sha256'), 'hex')
    ) then
        raise exception 'not authorized' using errcode = '28000';
    end if;

    if p_action <> 'get_runtime' then
        select email into v_actor from bot_private.admin_sessions
         where token_sha256 = encode(extensions.digest(coalesce(p_session_token, ''), 'sha256'), 'hex')
           and revoked_at is null and expires_at > now();
        if v_actor is null then
            raise exception 'unauthorized' using errcode = '28000';
        end if;
    end if;

    if p_action = 'save' then
        if nullif(btrim(p_payload->>'bot_name'), '') is null
           or length(p_payload->>'bot_name') > 80
           or length(coalesce(p_payload->>'persona_text', '')) > 2000
           or coalesce(p_payload->>'length', '') not in ('short', 'medium', 'long')
           or coalesce(p_payload->>'voice', '') not in ('male', 'female')
           or coalesce((p_payload->>'friendliness')::integer, -1) not between 0 and 100
           or coalesce((p_payload->>'detail_level')::integer, -1) not between 0 and 100 then
            raise exception 'invalid persona' using errcode = '22023';
        end if;
        update bot_private.bot_persona set
            bot_name = btrim(p_payload->>'bot_name'),
            persona_text = btrim(p_payload->>'persona_text'),
            friendliness = (p_payload->>'friendliness')::integer,
            answer_length = p_payload->>'length',
            detail_level = (p_payload->>'detail_level')::integer,
            voice = p_payload->>'voice',
            updated_at = now()
        where id = true;
        insert into bot_private.admin_logs(actor_email, action, metadata)
            values (v_actor, 'llm.persona.update', jsonb_build_object(
                'bot_name', btrim(p_payload->>'bot_name'),
                'friendliness', (p_payload->>'friendliness')::integer,
                'length', p_payload->>'length',
                'detail_level', (p_payload->>'detail_level')::integer,
                'voice', p_payload->>'voice'
            ));
    elsif p_action not in ('get_runtime', 'get_config') then
        raise exception 'unknown action' using errcode = '22023';
    end if;

    select jsonb_build_object(
        'bot_name', bot_name,
        'persona_text', persona_text,
        'friendliness', friendliness,
        'length', answer_length,
        'detail_level', detail_level,
        'voice', voice,
        'updated_at', updated_at
    ) into v_result from bot_private.bot_persona where id = true;
    return v_result;
end;
$$;
revoke all on function public.bot_persona_action(text,text,text,jsonb) from public;
grant execute on function public.bot_persona_action(text,text,text,jsonb) to anon;
