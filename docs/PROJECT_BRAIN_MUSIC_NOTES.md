# Project Brain — Shared Music System Notes

## Project Brain purpose

Project Brain is the quick-reference/context layer for Anonymous Bot and its website. It exists so an AI can quickly read the plan and understand what the bot, website, systems, and architecture are supposed to do without inspecting the entire codebase first.

The long-term goal is that **my Brain becomes my friend's Brain**. When we merge our websites and collaborate, both sides should use the same Project Brain context. Project Brain should remain a living summary of the actual bot/website architecture and major systems.

## Shared cloud music system

The project has around 1,500 music tracks already organized with tags such as `sad`, `action`, etc. Those tags are intended to become the vocabulary used by the Project Brain audio detector. Music should be selected from tags based on scene/context, while also supporting dedicated theme music for players and NPCs.

### Core rule

**Do not put the actual 1,500 audio files in GitHub.** GitHub should contain source code, configuration templates, music metadata/schema, and the logic for communicating with the music service. The actual audio should live in shared cloud/object storage.

```text
                 GITHUB
        code + Brain + metadata
                   |
                   v
             Music API/Service
                   |
                   v
          CLOUD MUSIC STORAGE
             1,500+ tracks
                   |
          +--------+--------+
          |                 |
       MY PC           FRIEND'S PC
```

Both collaborators' bot/website installations should use the same hosted music library. A collaborator can clone the repository and run the project without downloading all 1,500 songs. When Brain needs a track, the Music API returns a stream/reference to the cloud-hosted audio.

### Existing music foundation

The current web application already contains much of the foundation this system should build on: `audio_assets`, `active_audio`, `main_ost_id`, ambience/music states, local `web_audio` indexing, tag-based music selection, AI mood selection, `/media/audio/...` serving, uploads, player/NPC theme assignment, and real-time `audio_changed` events.

The cloud system should extend this existing functionality rather than replace it.

### Audio detector

Brain should detect scene/context and rank tracks by their tags. For example:

```text
NPC dies
  -> sad / emotional / death / slow
  -> Music API searches matching tracks
  -> rank candidates
  -> stream selected track
```

A track can have many tags, and dedicated player/NPC themes can be prioritized when appropriate.

### Importing music

The finished system should provide an easy authorized import flow: add a track to shared cloud storage, assign/edit tags, and make it immediately available to Brain without changing code or committing the audio file to GitHub.

A local-development fallback can remain available when cloud storage is not configured.

### Hosting

A Cloudflare R2/S3-compatible object-storage setup is a planned target. The actual provider still needs to be configured. Private credentials and the audio library must never be committed to GitHub.

`START_EVERYTHING` should start the local bot/website/Brain services, but the shared music library stays remotely hosted. Starting the project on a collaborator's computer must **not** download the whole library.

### Maintenance rule

When major bot/website systems are added or changed, update Project Brain so another collaborator or AI can quickly understand the current architecture and continue development without rediscovering the entire codebase.
