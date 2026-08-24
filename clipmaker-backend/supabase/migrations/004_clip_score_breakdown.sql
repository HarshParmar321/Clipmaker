-- Persists the evidence behind each clip's calibrated quality score.
-- Safe to run more than once in the Supabase SQL editor.

alter table public.clips
    add column if not exists score_breakdown jsonb not null default '{}'::jsonb;

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'clips_score_breakdown_object'
    ) then
        alter table public.clips add constraint clips_score_breakdown_object
            check (jsonb_typeof(score_breakdown) = 'object');
    end if;
end $$;
