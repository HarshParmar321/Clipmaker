create extension if not exists pgcrypto;

create table if not exists public.jobs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references auth.users(id) on delete set null,
    status text not null check (status in (
        'queued', 'downloading', 'transcribing', 'detecting_highlights',
        'cutting_clips', 'burning_captions', 'done', 'failed'
    )),
    source_type text not null check (source_type in ('url', 'upload')),
    source_value text not null default '',
    video_path text,
    transcript jsonb,
    error text,
    creative_prompt text check (char_length(creative_prompt) <= 1000),
    output_format text not null default 'vertical'
        check (output_format in ('original', 'landscape', 'vertical')),
    caption_style text not null default 'bold'
        check (caption_style in ('bold', 'clean', 'karaoke')),
    target_clip_count integer not null default 5
        check (target_clip_count between 1 and 10),
    min_clip_duration integer not null default 20
        check (min_clip_duration between 5 and 90),
    max_clip_duration integer not null default 60
        check (max_clip_duration between 10 and 120 and max_clip_duration > min_clip_duration),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.clips (
    id uuid primary key default gen_random_uuid(),
    job_id uuid not null references public.jobs(id) on delete cascade,
    title text not null,
    start_seconds double precision not null check (start_seconds >= 0),
    end_seconds double precision not null check (end_seconds > start_seconds),
    score integer not null check (score between 0 and 100),
    reason text not null default '',
    file_path text,
    output_format text not null default 'vertical'
        check (output_format in ('original', 'landscape', 'vertical')),
    caption_style text not null default 'bold'
        check (caption_style in ('bold', 'clean', 'karaoke')),
    created_at timestamptz not null default now()
);

create index if not exists clips_job_id_idx on public.clips(job_id);

create table if not exists public.artifacts (
    id uuid primary key default gen_random_uuid(),
    job_id uuid not null references public.jobs(id) on delete cascade,
    clip_id uuid references public.clips(id) on delete cascade,
    kind text not null check (kind in (
        'source', 'audio_temp', 'subtitle_temp', 'thumbnails',
        'final_clip', 'edited_clip'
    )),
    local_path text not null unique,
    size_bytes bigint not null default 0 check (size_bytes >= 0),
    expires_at timestamptz not null,
    deleted_at timestamptz,
    created_at timestamptz not null default now()
);

create index if not exists artifacts_expiry_idx
    on public.artifacts(expires_at)
    where deleted_at is null;
create index if not exists artifacts_job_id_idx on public.artifacts(job_id);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists jobs_set_updated_at on public.jobs;
create trigger jobs_set_updated_at
before update on public.jobs
for each row execute function public.set_updated_at();

alter table public.jobs enable row level security;
alter table public.clips enable row level security;
alter table public.artifacts enable row level security;

drop policy if exists "Users can read their own jobs" on public.jobs;
create policy "Users can read their own jobs"
on public.jobs for select to authenticated
using (auth.uid() = user_id);

drop policy if exists "Users can read clips from their jobs" on public.clips;
create policy "Users can read clips from their jobs"
on public.clips for select to authenticated
using (exists (
    select 1 from public.jobs
    where jobs.id = clips.job_id and jobs.user_id = auth.uid()
));

drop policy if exists "Users can read artifacts from their jobs" on public.artifacts;
create policy "Users can read artifacts from their jobs"
on public.artifacts for select to authenticated
using (exists (
    select 1 from public.jobs
    where jobs.id = artifacts.job_id and jobs.user_id = auth.uid()
));
