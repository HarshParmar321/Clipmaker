-- Adds creative and render settings to an existing Phase 1 database.
-- Safe to run more than once in the Supabase SQL editor.

alter table public.jobs
    add column if not exists creative_prompt text,
    add column if not exists output_format text not null default 'vertical',
    add column if not exists caption_style text not null default 'bold';

alter table public.clips
    add column if not exists output_format text not null default 'vertical',
    add column if not exists caption_style text not null default 'bold';

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'jobs_creative_prompt_length'
    ) then
        alter table public.jobs add constraint jobs_creative_prompt_length
            check (creative_prompt is null or char_length(creative_prompt) <= 1000);
    end if;
    if not exists (
        select 1 from pg_constraint where conname = 'jobs_output_format_valid'
    ) then
        alter table public.jobs add constraint jobs_output_format_valid
            check (output_format in ('original', 'landscape', 'vertical'));
    end if;
    if not exists (
        select 1 from pg_constraint where conname = 'jobs_caption_style_valid'
    ) then
        alter table public.jobs add constraint jobs_caption_style_valid
            check (caption_style in ('bold', 'clean', 'karaoke'));
    end if;
    if not exists (
        select 1 from pg_constraint where conname = 'clips_output_format_valid'
    ) then
        alter table public.clips add constraint clips_output_format_valid
            check (output_format in ('original', 'landscape', 'vertical'));
    end if;
    if not exists (
        select 1 from pg_constraint where conname = 'clips_caption_style_valid'
    ) then
        alter table public.clips add constraint clips_caption_style_valid
            check (caption_style in ('bold', 'clean', 'karaoke'));
    end if;
end $$;
