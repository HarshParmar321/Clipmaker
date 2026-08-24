-- Persists Phase 2 highlight quantity and duration preferences.
-- Safe to run more than once in the Supabase SQL editor.

alter table public.jobs
    add column if not exists target_clip_count integer not null default 5,
    add column if not exists min_clip_duration integer not null default 20,
    add column if not exists max_clip_duration integer not null default 60;

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'jobs_target_clip_count_valid'
    ) then
        alter table public.jobs add constraint jobs_target_clip_count_valid
            check (target_clip_count between 1 and 10);
    end if;
    if not exists (
        select 1 from pg_constraint where conname = 'jobs_min_clip_duration_valid'
    ) then
        alter table public.jobs add constraint jobs_min_clip_duration_valid
            check (min_clip_duration between 5 and 90);
    end if;
    if not exists (
        select 1 from pg_constraint where conname = 'jobs_max_clip_duration_valid'
    ) then
        alter table public.jobs add constraint jobs_max_clip_duration_valid
            check (
                max_clip_duration between 10 and 120
                and max_clip_duration > min_clip_duration
            );
    end if;
end $$;
