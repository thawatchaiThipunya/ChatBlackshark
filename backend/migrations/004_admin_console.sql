create table if not exists bot_private.admin_sessions (
    token_sha256 text primary key check (token_sha256 ~ '^[0-9a-f]{64}$'),
    email text not null,
    username text not null,
    display_name text not null default '',
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    last_seen_at timestamptz not null default now(),
    revoked_at timestamptz
);
create index if not exists admin_sessions_email_created_idx
    on bot_private.admin_sessions (email, created_at desc);

create table if not exists bot_private.admin_preferences (
    email text primary key,
    bubble_color text not null default '#8b1e28' check (bubble_color ~ '^#[0-9A-Fa-F]{6}$'),
    chat_text_size text not null default 'medium' check (chat_text_size in ('small', 'medium', 'large')),
    theme_preference text not null default 'light' check (theme_preference in ('light', 'dark')),
    updated_at timestamptz not null default now()
);

create table if not exists bot_private.admin_logs (
    id bigint generated always as identity primary key,
    actor_email text not null,
    action text not null check (char_length(action) between 1 and 100),
    metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
    created_at timestamptz not null default now()
);
create index if not exists admin_logs_created_idx on bot_private.admin_logs (created_at desc);
create index if not exists admin_logs_action_created_idx on bot_private.admin_logs (action, created_at desc);

alter table bot_private.admin_sessions enable row level security;
alter table bot_private.admin_preferences enable row level security;
alter table bot_private.admin_logs enable row level security;
revoke all on bot_private.admin_sessions, bot_private.admin_preferences, bot_private.admin_logs
    from public, anon, authenticated;

create or replace function public.bot_admin_action(
    p_app_token text,
    p_session_token text,
    p_action text,
    p_payload jsonb default '{}'::jsonb
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    v_session bot_private.admin_sessions%rowtype;
    v_actor text;
    v_result jsonb;
    v_limit integer;
    v_color text;
    v_size text;
    v_theme text;
begin
    if not exists (
        select 1 from bot_private.admin_gate where id = true
          and token_sha256 = encode(extensions.digest(coalesce(p_app_token, ''), 'sha256'), 'hex')
    ) then
        raise exception 'not authorized' using errcode = '28000';
    end if;

    if p_action = 'create_session' then
        if length(coalesce(p_session_token, '')) < 32
           or nullif(btrim(p_payload->>'email'), '') is null
           or nullif(btrim(p_payload->>'username'), '') is null
           or (p_payload->>'expires_at')::timestamptz <= now() then
            raise exception 'invalid session' using errcode = '22023';
        end if;
        v_actor := lower(btrim(p_payload->>'email'));
        insert into bot_private.admin_sessions (
            token_sha256, email, username, display_name, expires_at
        ) values (
            encode(extensions.digest(p_session_token, 'sha256'), 'hex'),
            v_actor,
            left(btrim(p_payload->>'username'), 120),
            left(btrim(coalesce(p_payload->>'name', '')), 200),
            (p_payload->>'expires_at')::timestamptz
        );
        insert into bot_private.admin_logs (actor_email, action, metadata)
            values (v_actor, 'auth.login', jsonb_build_object('method', 'system81'));
        return jsonb_build_object('ok', true);
    end if;

    if length(coalesce(p_session_token, '')) < 32 then
        if p_action = 'get_session' then return null; end if;
        raise exception 'unauthorized' using errcode = '28000';
    end if;

    select * into v_session
      from bot_private.admin_sessions
     where token_sha256 = encode(extensions.digest(p_session_token, 'sha256'), 'hex')
       and revoked_at is null and expires_at > now();

    if p_action = 'get_session' then
        if not found then return null; end if;
        update bot_private.admin_sessions set last_seen_at = now()
         where token_sha256 = v_session.token_sha256;
        return jsonb_build_object(
            'email', v_session.email,
            'username', v_session.username,
            'name', v_session.display_name,
            'expires_at', v_session.expires_at
        );
    end if;

    if not found then
        raise exception 'unauthorized' using errcode = '28000';
    end if;
    v_actor := v_session.email;

    if p_action = 'revoke_session' then
        update bot_private.admin_sessions set revoked_at = now()
         where token_sha256 = v_session.token_sha256 and revoked_at is null;
        insert into bot_private.admin_logs (actor_email, action, metadata)
            values (v_actor, 'auth.logout', '{}'::jsonb);
        return jsonb_build_object('ok', true);
    elsif p_action = 'get_preferences' then
        select jsonb_build_object(
            'bubble_color', bubble_color,
            'chat_text_size', chat_text_size,
            'theme_preference', theme_preference,
            'updated_at', updated_at
        ) into v_result from bot_private.admin_preferences where email = v_actor;
        return coalesce(v_result, jsonb_build_object(
            'bubble_color', '#8b1e28', 'chat_text_size', 'medium', 'theme_preference', 'light'
        ));
    elsif p_action = 'save_preferences' then
        v_color := p_payload->>'bubble_color';
        v_size := p_payload->>'chat_text_size';
        v_theme := p_payload->>'theme_preference';
        if v_color !~ '^#[0-9A-Fa-f]{6}$'
           or v_size not in ('small', 'medium', 'large')
           or v_theme not in ('light', 'dark') then
            raise exception 'invalid preferences' using errcode = '22023';
        end if;
        insert into bot_private.admin_preferences (email, bubble_color, chat_text_size, theme_preference, updated_at)
        values (v_actor, lower(v_color), v_size, v_theme, now())
        on conflict (email) do update set
            bubble_color = excluded.bubble_color,
            chat_text_size = excluded.chat_text_size,
            theme_preference = excluded.theme_preference,
            updated_at = now();
        insert into bot_private.admin_logs (actor_email, action, metadata)
            values (v_actor, 'preferences.update', jsonb_build_object(
                'bubble_color', lower(v_color), 'chat_text_size', v_size, 'theme_preference', v_theme
            ));
        return jsonb_build_object('ok', true);
    elsif p_action = 'log' then
        if nullif(btrim(p_payload->>'action'), '') is null
           or char_length(p_payload->>'action') > 100 then
            raise exception 'invalid log action' using errcode = '22023';
        end if;
        insert into bot_private.admin_logs (actor_email, action, metadata)
            values (v_actor, btrim(p_payload->>'action'), coalesce(p_payload->'metadata', '{}'::jsonb));
        return jsonb_build_object('ok', true);
    elsif p_action = 'list_logs' then
        v_limit := least(greatest(coalesce((p_payload->>'limit')::integer, 50), 1), 100);
        select coalesce(jsonb_agg(row_to_json(rows)), '[]'::jsonb) into v_result from (
            select id, actor_email, action, metadata, created_at
              from bot_private.admin_logs
             where nullif(p_payload->>'action', '') is null or action = p_payload->>'action'
             order by created_at desc, id desc
             limit v_limit
        ) rows;
        return v_result;
    elsif p_action = 'dashboard' then
        select jsonb_build_object(
            'total_sessions', (select count(*) from bot_private.chat_sessions),
            'sessions_today', (select count(*) from bot_private.chat_sessions where created_at >= date_trunc('day', now())),
            'messages_today', (select count(*) from bot_private.chat_messages where created_at >= date_trunc('day', now())),
            'total_messages', (select count(*) from bot_private.chat_messages),
            'key_count', (select count(*) from bot_private.llm_keys),
            'sessions_by_day', (
                select coalesce(jsonb_agg(jsonb_build_object('date', to_char(days.day, 'YYYY-MM-DD'), 'count', coalesce(counts.total, 0)) order by days.day), '[]'::jsonb)
                  from generate_series(current_date - interval '6 days', current_date, interval '1 day') as days(day)
                  left join (
                      select date_trunc('day', created_at)::date as day, count(*) as total
                        from bot_private.chat_sessions
                       where created_at >= current_date - interval '6 days'
                       group by 1
                  ) counts on counts.day = days.day::date
            ),
            'recent_activity', (
                select coalesce(jsonb_agg(row_to_json(recent)), '[]'::jsonb) from (
                    select id, actor_email, action, metadata, created_at
                      from bot_private.admin_logs order by created_at desc, id desc limit 8
                ) recent
            )
        ) into v_result;
        return v_result;
    elsif p_action = 'list_keys' then
        select coalesce(jsonb_agg(jsonb_build_object(
            'id', id, 'label', label, 'last4', last4, 'created_at', created_at
        ) order by id desc), '[]'::jsonb) into v_result from bot_private.llm_keys;
        return v_result;
    elsif p_action = 'add_key' then
        if nullif(btrim(p_payload->>'label'), '') is null
           or char_length(p_payload->>'label') > 80
           or coalesce(p_payload->>'ciphertext', '') not like 'gAAAA%'
           or char_length(coalesce(p_payload->>'last4', '')) <> 4 then
            raise exception 'invalid key entry' using errcode = '22023';
        end if;
        insert into bot_private.llm_keys (label, ciphertext, last4)
        values (btrim(p_payload->>'label'), p_payload->>'ciphertext', p_payload->>'last4')
        returning jsonb_build_object('id', id) into v_result;
        insert into bot_private.admin_logs (actor_email, action, metadata)
            values (v_actor, 'llm_key.add', jsonb_build_object('label', btrim(p_payload->>'label'), 'last4', p_payload->>'last4'));
        return v_result;
    elsif p_action = 'delete_key' then
        delete from bot_private.llm_keys where id = (p_payload->>'id')::bigint
        returning jsonb_build_object('id', id) into v_result;
        if v_result is not null then
            insert into bot_private.admin_logs (actor_email, action, metadata)
                values (v_actor, 'llm_key.delete', v_result);
        end if;
        return coalesce(v_result, '{}'::jsonb);
    end if;

    raise exception 'invalid action' using errcode = '22023';
end;
$$;

revoke all on function public.bot_admin_action(text, text, text, jsonb) from public;
grant execute on function public.bot_admin_action(text, text, text, jsonb) to anon;
