-- Gaming/OBS processing preferences. Safe to run more than once.
alter table public.jobs
  add column if not exists content_mode text not null default 'general';

alter table public.jobs
  drop constraint if exists jobs_content_mode_check;

alter table public.jobs
  add constraint jobs_content_mode_check
  check (content_mode in ('general', 'gaming'));

alter table public.clips
  add column if not exists layout_mode text not null default 'smart';

alter table public.clips
  drop constraint if exists clips_layout_mode_check;

alter table public.clips
  add constraint clips_layout_mode_check
  check (layout_mode in ('smart', 'gameplay_reaction'));

comment on column public.jobs.content_mode is
  'General-purpose or Gaming/OBS highlight workflow.';

comment on column public.clips.layout_mode is
  'Smart crop or stacked gameplay-and-reaction portrait layout.';
