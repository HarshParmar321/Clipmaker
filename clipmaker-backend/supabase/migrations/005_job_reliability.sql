-- Persists accurate job progress, cancellation, retry, and restart recovery.
-- Safe to run more than once in the Supabase SQL editor.

alter table public.jobs
    add column if not exists progress_percent integer not null default 0,
    add column if not exists progress_message text not null default 'Waiting to start…',
    add column if not exists current_stage text,
    add column if not exists processed_clips integer not null default 0,
    add column if not exists total_clips integer not null default 0,
    add column if not exists heartbeat_at timestamptz,
    add column if not exists failed_stage text,
    add column if not exists retryable boolean not null default false,
    add column if not exists cancel_requested_at timestamptz;

do $$
declare
    constraint_record record;
begin
    for constraint_record in
        select conname
        from pg_constraint
        where conrelid = 'public.jobs'::regclass
          and contype = 'c'
          and pg_get_constraintdef(oid) like '%status%'
    loop
        execute format(
            'alter table public.jobs drop constraint if exists %I',
            constraint_record.conname
        );
    end loop;

    if not exists (
        select 1 from pg_constraint where conname = 'jobs_status_valid'
    ) then
        alter table public.jobs add constraint jobs_status_valid check (status in (
            'queued', 'downloading', 'transcribing', 'detecting_highlights',
            'cutting_clips', 'burning_captions', 'done', 'failed', 'cancelled'
        ));
    end if;
    if not exists (
        select 1 from pg_constraint where conname = 'jobs_progress_percent_valid'
    ) then
        alter table public.jobs add constraint jobs_progress_percent_valid
            check (progress_percent between 0 and 100);
    end if;
    if not exists (
        select 1 from pg_constraint where conname = 'jobs_clip_progress_valid'
    ) then
        alter table public.jobs add constraint jobs_clip_progress_valid
            check (
                processed_clips >= 0
                and total_clips >= 0
                and processed_clips <= total_clips
            );
    end if;
end $$;

create index if not exists jobs_active_heartbeat_idx
    on public.jobs(heartbeat_at)
    where status in (
        'queued', 'downloading', 'transcribing', 'detecting_highlights',
        'cutting_clips', 'burning_captions'
    );
