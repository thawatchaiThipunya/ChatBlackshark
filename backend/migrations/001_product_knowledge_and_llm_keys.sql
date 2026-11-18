create table public.bot_products (
    id bigint generated always as identity primary key,
    source_index integer not null unique,
    name text not null unique,
    description text not null,
    source jsonb not null check (jsonb_typeof(source) = 'object')
);

create table public.bot_faqs (
    id bigint generated always as identity primary key,
    source_index integer not null unique,
    model_name text not null,
    question text not null,
    answer text not null,
    source jsonb not null check (jsonb_typeof(source) = 'object')
);

create index bot_faqs_model_name_idx on public.bot_faqs (model_name);

alter table public.bot_products enable row level security;
alter table public.bot_faqs enable row level security;
grant select on public.bot_products, public.bot_faqs to anon;
create policy bot_products_public_read on public.bot_products for select to anon using (true);
create policy bot_faqs_public_read on public.bot_faqs for select to anon using (true);

create schema bot_private;
revoke all on schema bot_private from public, anon, authenticated;

create table bot_private.admin_gate (
    id boolean primary key default true check (id),
    token_sha256 text not null check (token_sha256 ~ '^[0-9a-f]{64}$')
);

create table bot_private.llm_keys (
    id bigint generated always as identity primary key,
    label text not null check (char_length(label) between 1 and 80),
    ciphertext text not null,
    last4 text not null check (char_length(last4) = 4),
    created_at timestamptz not null default now()
);

create sequence bot_private.llm_key_rotation_seq;

alter table bot_private.admin_gate enable row level security;
alter table bot_private.llm_keys enable row level security;

create function public.bot_llm_key_action(
    p_token text,
    p_action text,
    p_id bigint default null,
    p_label text default null,
    p_ciphertext text default null,
    p_last4 text default null
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    v_gate_hash text;
    v_result jsonb;
    v_count bigint;
    v_offset bigint;
begin
    select token_sha256 into v_gate_hash from bot_private.admin_gate where id = true;
    if v_gate_hash is null or v_gate_hash is distinct from
       encode(extensions.digest(coalesce(p_token, ''), 'sha256'), 'hex') then
        raise exception 'not authorized' using errcode = '28000';
    end if;

    if p_action = 'list' then
        select coalesce(jsonb_agg(jsonb_build_object(
            'id', id, 'label', label, 'last4', last4, 'created_at', created_at
        ) order by id desc), '[]'::jsonb)
        into v_result from bot_private.llm_keys;
        return v_result;
    elsif p_action = 'next' then
        select count(*) into v_count from bot_private.llm_keys;
        if v_count = 0 then
            return jsonb_build_object('count', 0);
        end if;
        v_offset := (nextval('bot_private.llm_key_rotation_seq'::regclass) - 1) % v_count;
        select jsonb_build_object('id', id, 'ciphertext', ciphertext, 'count', v_count)
        into v_result from bot_private.llm_keys order by id offset v_offset limit 1;
        return v_result;
    elsif p_action = 'add' then
        if nullif(btrim(p_label), '') is null or char_length(p_label) > 80
           or p_ciphertext is null or p_ciphertext not like 'gAAAA%'
           or char_length(p_last4) <> 4 then
            raise exception 'invalid key entry' using errcode = '22023';
        end if;
        insert into bot_private.llm_keys (label, ciphertext, last4)
        values (btrim(p_label), p_ciphertext, p_last4)
        returning jsonb_build_object('id', id) into v_result;
        return v_result;
    elsif p_action = 'delete' then
        delete from bot_private.llm_keys where id = p_id
        returning jsonb_build_object('id', id) into v_result;
        return coalesce(v_result, '{}'::jsonb);
    end if;
    raise exception 'invalid action' using errcode = '22023';
end;
$$;

revoke all on function public.bot_llm_key_action(text, text, bigint, text, text, text) from public;
grant execute on function public.bot_llm_key_action(text, text, bigint, text, text, text) to anon;
