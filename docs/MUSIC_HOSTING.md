# Project Brain — Shared Music Hosting

Project Brain's music should not be stored in Git or downloaded by every collaborator.
The repository contains code and small metadata/manifests; the actual audio library lives
in shared object storage.

## Intended architecture

```text
GitHub repo
  ├─ Brain / bot / website code
  ├─ music_service.py
  └─ track metadata / tags

Cloud object storage (R2/S3/B2/etc.)
  └─ 1,500+ audio files

Your PC ───────┐
Friend's PC ───┼──> shared music URLs ──> object storage
Hosted website ┘
```

The browser streams only the selected track. It does not clone or download the entire
library when the project is installed.

## Configuration

Set `MUSIC_PUBLIC_BASE_URL` in the real `.env` to the public bucket/CDN base URL.
For example:

```text
MUSIC_PUBLIC_BASE_URL=https://music.example.com
```

Do not put storage access keys in GitHub. If private uploads are added later, keep the
S3/R2 endpoint, bucket, access key, and secret only in environment variables.

## Current implementation status

- `anonymous_bot/music_service.py` provides cloud/local URL resolution and track manifests.
- The existing web app already has a `campaign_data/web_audio` library and audio metadata.
- The next integration step is to make the web audio endpoints consume the shared cloud
  URL when `MUSIC_PUBLIC_BASE_URL` is configured, while retaining local mode for offline
  development.
- A cloud bucket itself must be created outside GitHub; the application cannot create an
  R2/S3 account or bucket from the repository alone.

## Tags and Brain

Track metadata should keep the existing RPG tags such as `sad`, `action`, `calm`,
`dark`, `funny`, `scary`, and `main_ost`. Additional tags can describe `combat`,
`death`, `victory`, `character`, `boss`, `exploration`, `tension`, etc.

The Brain/audio detector can score scene context against these tags and select a track.
Character/NPC theme assignments should be stored as metadata rather than hard-coded
file paths.
