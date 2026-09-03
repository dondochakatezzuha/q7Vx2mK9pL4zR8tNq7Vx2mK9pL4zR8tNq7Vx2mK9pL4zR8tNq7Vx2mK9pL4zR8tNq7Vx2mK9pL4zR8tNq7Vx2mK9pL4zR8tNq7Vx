# 🧠 Project Brain — Shared Brain Specification

> This is a supplemental specification for the Project Brain described by `PLAN.md`. It does not replace `PLAN.md` or create a competing source of truth.

## Audio decisions added September 3, 2026

### GM narration drives automatic audio

The **GM's story/narration text is the authoritative input for automatic audio detection**. Player messages are not audio triggers and are not analyzed for automatic music/SFX playback.

Flow:

```text
GM narration
    ↓
context/event + mood detection
    ↓
music selection + SFX selection
    ↓
playback
```

Examples:

- `"Zero shot out missiles out his staff, exploding the terra beneath him"` → projectile/missile + explosion + impact/ground-destruction SFX.
- `"He slams his fist into the ground, sending a massive shockwave through the battlefield"` → ground slam + impact + shockwave + rubble/destruction SFX.
- `"The sky darkens as black clouds gather around the battlefield"` → dark/scary/tension music context.
- `"He quietly walks through the abandoned hospital"` → scary/ambient music context + footsteps.

Detection should be semantic/contextual rather than one exact keyword. Multiple detected concepts may create layered SFX and/or a music mood transition.

### Local unified audio library

`anonymous_bot/local_audio_library.py` is the dedicated local-development registry.

It treats:

- `campaign_data/web_audio/` as the local music library;
- `campaign_data/web_sfx/` as the local SFX library;
- procedural starter SFX definitions as discoverable local SFX.

`build_local_library()` returns both libraries and counts. The registry scans the machine at runtime so the full local music collection can remain local instead of being committed to GitHub.

The local registry is intentionally separate from the eventual shared cloud library. Local mode should use the complete local collection; cloud/public mode should reference remote object storage without downloading the complete shared collection.

### Shared cloud rule

The actual audio binaries should not be committed to GitHub. GitHub stores code, Brain documentation, configuration templates, and metadata/schema. Shared audio is planned for S3-compatible object storage such as Cloudflare R2.

### Status

- GM-narration-driven detection: **Decision / active architecture requirement**.
- Unified local registry: **Implemented** at `anonymous_bot/local_audio_library.py`.
- Full website integration of the local registry: **Next implementation step**.
- Cloud upload/storage service: **Planned**.
