create table bot_private.escalations (
    id bigint generated always as identity primary key,
    session_id text not null default '',
    model_name text not null default '',
    question text not null default '',
    transcript jsonb not null default '[]'::jsonb check (jsonb_typeof(transcript) = 'array'),
    created_at timestamptz not null default now()
);

alter table bot_private.escalations enable row level security;

create function public.bot_escalate(
    p_token text,
    p_session_id text,
    p_model_name text,
    p_question text,
    p_transcript jsonb
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    v_gate_hash text;
    v_id bigint;
begin
    select token_sha256 into v_gate_hash from bot_private.admin_gate where id = true;
    if v_gate_hash is null or v_gate_hash is distinct from
       encode(extensions.digest(coalesce(p_token, ''), 'sha256'), 'hex') then
        raise exception 'not authorized' using errcode = '28000';
    end if;

    insert into bot_private.escalations (session_id, model_name, question, transcript)
    values (
        left(coalesce(p_session_id, ''), 100),
        left(coalesce(p_model_name, ''), 200),
        left(coalesce(p_question, ''), 1000),
        coalesce(p_transcript, '[]'::jsonb)
    )
    returning id into v_id;
    return jsonb_build_object('id', v_id);
end;
$$;

revoke all on function public.bot_escalate(text, text, text, text, jsonb) from public;
grant execute on function public.bot_escalate(text, text, text, text, jsonb) to anon;
