create or replace function public.bot_llm_key_cycle(p_token text) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    v_count bigint;
    v_offset bigint;
    v_result jsonb;
begin
    if not exists (
        select 1 from bot_private.admin_gate where id = true
          and token_sha256 = encode(extensions.digest(coalesce(p_token, ''), 'sha256'), 'hex')
    ) then
        raise exception 'not authorized' using errcode = '28000';
    end if;

    select count(*) into v_count from bot_private.llm_keys;
    if v_count = 0 then return '[]'::jsonb; end if;
    v_offset := (nextval('bot_private.llm_key_rotation_seq'::regclass) - 1) % v_count;
    select jsonb_agg(jsonb_build_object('id', id, 'ciphertext', ciphertext)
                     order by ((ordinal - 1 - v_offset + v_count) % v_count))
      into v_result
      from (
          select id, ciphertext, row_number() over (order by id) as ordinal
            from bot_private.llm_keys
      ) keys;
    return v_result;
end;
$$;
revoke all on function public.bot_llm_key_cycle(text) from public;
grant execute on function public.bot_llm_key_cycle(text) to anon;
