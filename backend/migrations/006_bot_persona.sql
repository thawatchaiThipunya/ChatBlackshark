create table if not exists bot_private.bot_persona (
    id boolean primary key default true check (id),
    answer_length text not null default 'medium'
        check (answer_length in ('short', 'medium', 'long')),
    tone text not null default 'normal'
        check (tone in ('friendly', 'normal', 'very_polite')),
    voice text not null default 'male'
        check (voice in ('male', 'female')),
    updated_at timestamptz not null default now()
);
insert into bot_private.bot_persona (id) values (true) on conflict (id) do nothing;
alter table bot_private.bot_persona enable row level security;
revoke all on bot_private.bot_persona from public, anon, authenticated;

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
        if coalesce(p_payload->>'length', '') not in ('short', 'medium', 'long')
           or coalesce(p_payload->>'tone', '') not in ('friendly', 'normal', 'very_polite')
           or coalesce(p_payload->>'voice', '') not in ('male', 'female') then
            raise exception 'invalid persona' using errcode = '22023';
        end if;
        update bot_private.bot_persona set
            answer_length = p_payload->>'length',
            tone = p_payload->>'tone',
            voice = p_payload->>'voice',
            updated_at = now()
        where id = true;
        insert into bot_private.admin_logs(actor_email, action, metadata)
            values (v_actor, 'llm.persona.update', jsonb_build_object(
                'length', p_payload->>'length',
                'tone', p_payload->>'tone',
                'voice', p_payload->>'voice'
            ));
    elsif p_action not in ('get_runtime', 'get_config') then
        raise exception 'unknown action' using errcode = '22023';
    end if;

    select jsonb_build_object(
        'length', answer_length,
        'tone', tone,
        'voice', voice,
        'updated_at', updated_at
    ) into v_result from bot_private.bot_persona where id = true;
    return v_result;
end;
$$;
revoke all on function public.bot_persona_action(text,text,text,jsonb) from public;
grant execute on function public.bot_persona_action(text,text,text,jsonb) to anon;
