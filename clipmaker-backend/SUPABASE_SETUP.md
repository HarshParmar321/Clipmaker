# Supabase setup for Phase 1

Supabase stores job, clip, and artifact metadata. Video files remain in the
local `storage/jobs` directory.

1. Open your Supabase project.
2. Open **SQL Editor**.
3. Run `supabase/migrations/001_phase1_storage.sql`.
   - If you already ran Phase 1 before the creative controls were added, also
     run `supabase/migrations/002_clip_preferences.sql`.
   - For Phase 2 clip count and duration controls, also run
     `supabase/migrations/003_highlight_preferences.sql`.
4. Open **Project Settings > API**.
5. Copy `.env.example` to `.env`.
6. Set `SUPABASE_URL` and `SUPABASE_SECRET_KEY` in `.env`.
7. Keep the secret key on the backend only. A legacy `service_role` key is
   supported through `SUPABASE_SERVICE_ROLE_KEY`, but the new secret key is
   preferred.
8. Leave `AUTH_REQUIRED=false` until the frontend sign-in screen is connected.

At startup, `GET /` reports `"metadata_store": "supabase"` when configuration
is complete. Without credentials, the backend uses an explicitly logged
in-memory fallback so local setup and tests remain usable.

The second migration stores the optional creative prompt, selected canvas
(`16:9`, `9:16`, or original), and caption preset. Media continues to stay on
the local filesystem.

## Cleanup

Inspect expired files without deleting anything:

```bash
python cleanup_storage.py --dry-run
```

Execute cleanup:

```bash
python cleanup_storage.py --execute
```

The API also cleans expired artifacts at startup and every
`CLEANUP_INTERVAL_MINUTES` while it is running.
