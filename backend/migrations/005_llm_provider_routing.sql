create table if not exists bot_private.llm_runtime_config (
    id boolean primary key default true check (id),
    provider text not null default 'gemini' check (provider in ('gemini', 'openrouter')),
    gemini_intent_model text not null default 'gemini-3.5-flash-lite',
    gemini_vision_model text not null default 'gemini-3.5-flash-lite',
    gemini_answer_model text not null default 'gemini-3.5-flash-lite',
    openrouter_intent_model text not null default 'google/gemini-2.5-flash',
    openrouter_vision_model text not null default 'google/gemini-2.5-flash',
    openrouter_answer_model text not null default 'google/gemini-2.5-flash',
    openrouter_key_ciphertext text,
    openrouter_key_last4 text,
    updated_at timestamptz not null default now()
);
insert into bot_private.llm_runtime_config(id) values (true) on conflict (id) do nothing;
alter table bot_private.llm_runtime_config enable row level security;
revoke all on bot_private.llm_runtime_config from public, anon, authenticated;

create or replace function public.bot_llm_config_action(
    p_app_token text,
    p_session_token text,
    p_action text,
    p_payload jsonb default '{}'::jsonb
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    v_config bot_private.llm_runtime_config%rowtype;
    v_actor text;
    v_provider text;
    v_field text;
    v_value text;
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

    if p_action = 'save_config' then
        v_provider := p_payload->>'provider';
        if v_provider not in ('gemini', 'openrouter') then
            raise exception 'invalid provider' using errcode = '22023';
        end if;
        for v_field in select unnest(array[
            'gemini_intent_model', 'gemini_vision_model', 'gemini_answer_model',
            'openrouter_intent_model', 'openrouter_vision_model', 'openrouter_answer_model'
        ]) loop
            v_value := p_payload->>v_field;
            if v_value is null or v_value !~ '^[A-Za-z0-9][A-Za-z0-9._:/+-]{1,119}$' then
                raise exception 'invalid model' using errcode = '22023';
            end if;
        end loop;
        update bot_private.llm_runtime_config set
            provider = v_provider,
            gemini_intent_model = p_payload->>'gemini_intent_model',
            gemini_vision_model = p_payload->>'gemini_vision_model',
            gemini_answer_model = p_payload->>'gemini_answer_model',
            openrouter_intent_model = p_payload->>'openrouter_intent_model',
            openrouter_vision_model = p_payload->>'openrouter_vision_model',
            openrouter_answer_model = p_payload->>'openrouter_answer_model',
            updated_at = now()
        where id = true and (v_provider = 'gemini' or openrouter_key_ciphertext is not null);
        if not found then
            raise exception 'OpenRouter key is required' using errcode = '22023';
        end if;
        insert into bot_private.admin_logs(actor_email, action, metadata)
            values (v_actor, 'llm.config.update', jsonb_build_object('provider', v_provider));
    elsif p_action = 'set_openrouter_key' then
        if length(coalesce(p_payload->>'ciphertext', '')) < 40
           or length(coalesce(p_payload->>'last4', '')) <> 4 then
            raise exception 'invalid key' using errcode = '22023';
        end if;
        update bot_private.llm_runtime_config set
            openrouter_key_ciphertext = p_payload->>'ciphertext',
            openrouter_key_last4 = p_payload->>'last4', updated_at = now()
        where id = true;
        insert into bot_private.admin_logs(actor_email, action, metadata)
            values (v_actor, 'llm.openrouter_key.update', '{}'::jsonb);
    elsif p_action not in ('get_runtime', 'get_config') then
        raise exception 'unknown action' using errcode = '22023';
    end if;

    select * into v_config from bot_private.llm_runtime_config where id = true;
    if p_action = 'get_runtime' then
        return to_jsonb(v_config) - 'id' - 'openrouter_key_last4';
    end if;
    return to_jsonb(v_config) - 'id' - 'openrouter_key_ciphertext';
end;
$$;
revoke all on function public.bot_llm_config_action(text,text,text,jsonb) from public;
grant execute on function public.bot_llm_config_action(text,text,text,jsonb) to anon;
