# 🧠 Project Brain — Shared Brain Specification

> This is a supplemental specification for the Project Brain described by `PLAN.md`. It does not replace `PLAN.md` or create a competing source of truth.

## 1. What Project Brain is

Project Brain is the shared quick-reference/context layer for the entire Anonymous RPG Bot / Regnum of Regalia project.

It exists so:

- an AI can understand the project quickly without reading the entire repository;
- either human collaborator can understand the current architecture and decisions;
- the bot and website can be understood as one connected product;
- future development can continue from the current state instead of rediscovering the codebase;
- project knowledge can survive handoffs, merges, and changes of AI/developer.

## 2. Shared-brain rule

Project Brain is **not owned by one collaborator**.

The intended model is:

```text
             SHARED PROJECT BRAIN
                    │
          ┌─────────┴─────────┐
          │                   │
       YOUR WORK          FRIEND'S WORK
          │                   │
          └─────────┬─────────┘
                    │
             SAME PROJECT CONTEXT
                    │
              ┌─────┴─────┐
              │           │
             BOT       WEBSITE
              │           │
              └─────┬─────┘
                    │
              SHARED SYSTEMS
```

When the two websites/bot implementations are merged, Project Brain should become the common context for both sides. It should describe the **combined current project**, not merely the original author's implementation.

## 3. What Brain must contain

Project Brain should maintain concise, current information about:

- project purpose and product identity;
- repository/file map;
- startup and runtime architecture;
- Discord architecture and command systems;
- website navigation and page responsibilities;
- bot ↔ website communication;
- API endpoints/contracts;
- authentication and security rules;
- campaign/world state architecture;
- runtime memory and lore systems;
- AI providers, responsibilities, canon rules, and context rules;
- music/audio architecture and selection rules;
- player/GM permissions and privacy boundaries;
- configuration and credential requirements;
- local/deployment workflow;
- tests and verification requirements;
- known bugs/regressions;
- active user-approved work;
- roadmap/future systems;
- architecture decisions and important project-direction decisions;
- update history/versioning;
- explicit work inbox (`ADD.md`);
- AI suggestions/ideas and their approval state.

## 4. Current state vs planned state

Brain must distinguish between:

- **Implemented** — verified to exist in code;
- **Active/Bug** — exists or is partly present but needs fixing;
- **Planned** — desired but not implemented;
- **Idea** — optional suggestion requiring approval;
- **Decision** — an explicit project-direction rule;
- **Deprecated** — intentionally removed behavior that must not be reintroduced.

Never describe planned or suggested behavior as already implemented.

## 5. Synchronization rules

After a meaningful code or architecture change:

1. inspect the affected source;
2. verify what actually changed;
3. update the corresponding Project Brain section;
4. record a meaningful Update Log entry;
5. preserve active requirements and unresolved bugs;
6. do not rewrite history to hide previous behavior.

If code and Brain disagree, the code is the immediate implementation authority. The disagreement must then be corrected in Brain or recorded as a known issue.

## 6. Collaboration / merge rules

Both collaborators work from the same Project Brain concept.

When branches or websites are merged:

- preserve the useful architecture from both sides;
- reconcile conflicting documentation instead of creating two brains;
- update the repository map and system descriptions to the merged reality;
- record important merge decisions in the Update Log;
- keep one shared understanding of permissions, APIs, state, AI context, and UI behavior.

A collaborator should be able to clone the repository and read Project Brain first, then know where to look next.

## 7. AI context behavior

AI should use Brain as a routing layer, not blindly dump the whole repository into context.

Preferred flow:

```text
User request
   ↓
Project Brain
   ↓
identify relevant subsystem
   ↓
inspect only required source/data
   ↓
answer / modify / verify
   ↓
synchronize Brain when the project changes
```

Context priority remains relevance-first:

1. current request;
2. relevant game/session state;
3. relevant Project Brain sections;
4. relevant structured lore/profile data;
5. small recent conversation window;
6. additional evidence only when needed.

Do not automatically load ancient Discord history, the entire campaign database, unrelated source files, or the whole repository.

## 8. ADD work queue

`ADD.md` is an explicit user-authorized work inbox.

- A request must be explicitly authorized before implementation.
- Silence is never approval.
- Do not silently expand scope.
- Do not clear unfinished requests.
- Clear completed requests only after implementation, verification, Brain synchronization, and Update Log synchronization.

## 9. IDEAS system

Ideas are suggestions, not requirements.

Supported statuses:

- Suggested
- Approved
- Implemented
- Rejected
- Outdated
- Superseded

AI may suggest ideas but may not silently implement or approve them.

The website's Game Ideas area should ultimately represent the same Project Brain Ideas rather than inventing a separate AI-controlled idea system.

## 10. Update Log / versioning

Project Brain uses one chronological Update Log inside `PLAN.md`.

- `v1.0` = initial baseline;
- normal meaningful changes increment the minor version;
- `v2.0` is reserved for genuinely major architectural/system changes;
- trivial edits do not require their own product version.

The current-state sections describe what exists now; the Update Log describes how it changed.

## 11. Shared cloud music knowledge

The music system is part of the shared Brain architecture.

The project has roughly 1,500 tracks with tags such as `sad`, `action`, and similar mood/context labels. The tag vocabulary is intended to drive automatic contextual music selection, while player and NPC themes remain separately assignable.

The actual audio library must not be committed to GitHub. GitHub stores code, Brain documentation, configuration templates, and metadata/schema. Shared audio belongs in cloud/object storage such as the planned Cloudflare R2/S3-compatible setup.

Both collaborators should access the same remote library without downloading all tracks locally.

```text
GitHub
  ├── code
  ├── Project Brain
  └── music metadata/schema
          ↓
     Music service
          ↓
  Cloud music storage
       ↙       ↘
    Your PC   Friend's PC
```

The detector should map context to tags, rank candidates, and stream/reference the selected remote track. Dedicated character themes can take priority where explicitly configured without silently replacing the global Main OST.

The existing local `campaign_data/web_audio` system remains a development fallback and should be extended rather than discarded.

## 12. Music import/update behavior

Authorized collaborators should be able to add a track, edit its tags/metadata, and make it available to Brain without committing the audio file to GitHub or changing application source code.

`START_EVERYTHING` may start local application services, but must not download the complete shared music library just to start the project.

Only audio that the collaborators are authorized to host/use should be placed in shared storage.

## 13. GM narration drives audio

The **GM's story/narration text is the authoritative input for automatic audio detection**.

Player messages are not audio triggers. The audio analyzer should not inspect a player's attempted action and start combat/music/SFX from it. The GM writes the actual story outcome, and that narration is what the audio system interprets.

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

- `"Zero shot out missiles out his staff, exploding the terra beneath him"` → missile/projectile + explosion + impact/ground destruction SFX.
- `"He slams his fist into the ground, sending a massive shockwave through the battlefield"` → ground slam + impact + shockwave + rubble/destruction SFX.
- `"The sky darkens as black clouds gather around the battlefield"` → dark/scary/tension music context.
- `"He quietly walks through the abandoned hospital"` → scary/ambient context + footsteps.

Detection should be semantic/contextual rather than a single exact-keyword lookup. Multiple detected concepts may produce a layered SFX sequence and/or a music mood transition.

This rule does **not** mean players are requesting permission to trigger audio. It means the GM is the narrator/source text for the automatic audio engine.

## 14. Local audio library

Local development has a dedicated unified registry at `anonymous_bot/local_audio_library.py`.

It treats:

- `campaign_data/web_audio/` as the complete local music library;
- `campaign_data/web_sfx/` as the complete local SFX library;
- the procedural starter SFX definitions as discoverable even before their WAV files are generated.

`build_local_library()` returns both libraries and their counts for local web/API integration. The registry scans the machine at runtime, so the local library can contain the user's full music collection without putting those binary files into GitHub source.

The local mode is deliberately separate from shared cloud hosting: local development can use every locally available track/SFX, while the eventual public/shared deployment references remote object storage instead of downloading the full collection.

## 15. Security boundary

Brain must never contain:

- bot tokens;
- OAuth client secrets;
- session secrets;
- provider API keys;
- storage access keys;
- private credentials.

It may describe which environment variables exist and what they are used for, but values remain local secrets.

## 16. Verification rule

A Brain entry claiming a feature is implemented should be backed by the source code and appropriate tests/manual verification.

For web changes, use the project's UI/web regression checks. For Python changes, use the relevant compile/startup checks. For Discord-backed behavior, verify actual Discord behavior rather than browser-only state.

## 17. Completeness checklist

Before calling Project Brain complete, confirm it has coverage for:

- [x] shared purpose/context layer;
- [x] shared use by both collaborators;
- [x] bot + website as one product;
- [x] repository map;
- [x] runtime/startup architecture;
- [x] Discord systems;
- [x] website systems/navigation;
- [x] web ↔ Discord/API architecture;
- [x] authentication/security;
- [x] campaign state/database;
- [x] runtime memory/lore distinction;
- [x] AI responsibilities/canon/context rules;
- [x] music/audio architecture;
- [x] permissions/privacy boundaries;
- [x] configuration/credentials rules;
- [x] development workflow;
- [x] testing/verification;
- [x] active bugs and approved work;
- [x] roadmap;
- [x] architecture/project decisions;
- [x] ADD work queue;
- [x] IDEAS approval workflow;
- [x] Update Log/versioning;
- [x] shared cloud music library;
- [x] GM-narration-driven audio detection;
- [x] local unified audio library;
- [x] collaborator merge/handoff rules;
- [x] current-vs-planned state distinction;
- [x] AI context/token-saving behavior;
- [x] maintenance/synchronization rules;
]
