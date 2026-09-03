# 🧠 PROJECT BRAIN — Anonymous RPG Bot / Regnum of Regalia

> **Single source of truth for AI-assisted development, project understanding, navigation, architecture, Discord behavior, and current plans.**
>
> Read this file first. Do not automatically read other documentation. Inspect source files only when a task requires them, and then inspect the smallest relevant set.

---

# PROJECT BRAIN RULE: BRAIN → CODE

`PLAN.md` is not merely documentation or a wishlist. It is the project's summarized understanding of what exists, what has been decided, and what work remains.

The source code remains the final authority for what is actually implemented, but Brain entries describing required/approved project behavior are development requirements.

When Brain says a system or behavior is required but the source code does not implement it yet, that is unfinished project work. The AI/developer should implement it when working through that subsystem instead of treating the Brain entry as permanently informational.

A feature must never be marked **Implemented** until it is actually present and verified in source/tests.

When a feature is implemented, synchronize Brain immediately so Brain and code agree.

---

# AUTOMATIC BRAIN UPDATE RULE

Whenever the user or a collaborator proposes a project idea, architecture decision, behavior requirement, feature request, correction, or important project-direction change during development, the AI should automatically record the meaningful information in Project Brain.

This does **not** mean every suggestion is automatically implemented.

The lifecycle is:

```text
User / collaborator suggests idea or requirement
                    ↓
          Project Brain records it
                    ↓
      classify current status correctly
       ┌────────────┴────────────┐
       │                         │
  Requirement / Decision      Idea / Suggestion
       │                         │
       ↓                         ↓
    implement              keep as approved/planned
       │                    until explicitly approved
       └────────────┬────────────┘
                    ↓
             verify in code
                    ↓
          synchronize Brain again
```

The AI must not silently turn an optional idea into implemented behavior. However, it must not lose meaningful suggestions simply because they were not immediately coded.

If the user explicitly says an idea should be part of the project, treat that as an approved requirement and work toward implementing it.

If the user explicitly says that **everything currently recorded in Brain that is not yet coded should now be worked on**, treat those Brain requirements as active implementation work, prioritizing the current subsystem and preserving dependencies rather than falsely marking everything complete at once.

---

# 1. WHAT THIS PROJECT IS

**Anonymous Bot / Regnum of Regalia (RoR)** is a Discord-backed RPG campaign system with a game-first web application.

It has two connected faces:

1. **Discord bot** — the primary Discord-side RPG/game engine, command interface, campaign archive, economy, items, parties, dungeons, GM controls, AI, lore collection, and campaign automation.
2. **Web game client** — a browser UI that authenticates through Discord, displays the campaign/game state, mirrors Discord game/general chat, exposes player tools, and gives GMs a richer campaign control surface.

The two sides share campaign state and communicate through the backend. Discord remains the actual Discord authority for Discord-backed actions such as channel permissions and Discord messages.

The project is one evolving product. Preserve working behavior unless the user explicitly asks to replace it.

---

# 2. REPOSITORY MAP

```text
BOT/
├── PLAN.md                         ← THIS FILE: project Brain / source of truth
├── START_EVERYTHING.bat            ← intended one-click launcher
├── render.yaml                     ← optional deployment configuration
├── .gitattributes
│
├── anonymous_bot/                  ← Python Discord bot + web backend
│   ├── bot.py                      ← Discord bot entry point and event lifecycle
│   ├── config.py                   ← environment/configuration
│   ├── state.py                    ← shared state helpers
│   ├── web_app.py                  ← HTTP server, Discord OAuth, web APIs, SSE bridge
│   ├── requirements.txt
│   ├── .env.example                ← safe configuration template
│   ├── core/
│   │   ├── campaign_store.py       ← persistent campaign/SQLite storage + backups
│   │   └── lore_index.py            ← structured lore/entity index
│   └── features/
│       ├── anonymous.py             ← anonymous messaging features
│       ├── ai_channel.py            ← administrator AI Discord channel
│       ├── ai_providers.py          ← AI provider/model selection and fallbacks
│       ├── companions.py            ← companion system
│       ├── create.py                ← character/player creation features
│       ├── dungeon.py               ← dungeon gameplay
│       ├── economy.py               ← currency, shop, stocks, gambling
│       ├── equipment.py             ← equipment/equip/use
│       ├── gm_tools.py              ← major GM/admin campaign controls
│       ├── groups.py                ← group chats
│       ├── hell.py                  ← Hell event/system
│       ├── help_ui.py               ← categorized Discord command help
│       ├── item_art.py              ← item artwork helpers
│       ├── item_cards.py            ← item card presentation
│       ├── items.py                 ← items/inventory/catalog system
│       ├── main_ui.py               ← main Discord campaign UI
│       ├── memory.py                ← campaign memory/archive/retrieval
│       ├── rpg.py                   ← RPG/player mechanics
│       ├── server_lore.py            ← automatic Discord lore archive/backfill
│       ├── trade.py                 ← player trading
│       ├── ui.py                    ← small UI helper module
│       └── gm/                      ← GM-specific AI/assistant code
│
├── Anonymous_BotV2/
│   └── index.html                   ← large game-first web client
│
├── campaign_data/                  ← persistent campaign/world data + web audio
│   ├── campaign.db                 ← SQLite campaign store
│   ├── web_world_state.json        ← web/game world state
│   ├── web_audio/                  ← server music library
│   └── other campaign JSON/assets
│
├── tests/                          ← UI/web regression checks
└── .venv/                          ← local Python environment (should not be treated as source)
```

The repository currently contains the Python application, the single-page web UI, campaign data, audio, and tests. Do not confuse `.venv` with project source.

---

# 3. HOW THE SYSTEM STARTS

The intended Windows launcher is **`START_EVERYTHING.bat`**.

Startup sequence:

```text
START_EVERYTHING.bat
        │
        ├── find project Python / .venv
        ├── verify Python 3.11+
        ├── install requirements if needed
        ├── start Ollama if installed and not already running
        │
        └── start `python -m anonymous_bot.bot`
                         │
                         ├── initialize campaign store
                         ├── initialize lore index
                         ├── load/register Discord features
                         ├── connect to Discord
                         ├── start web server
                         └── wait for Discord + campaign guild readiness
                                      │
                                      └── open local web client
```

The launcher intentionally does **not** consider the website healthy merely because HTTP responds. Discord readiness and configured campaign-guild readiness matter too.

The bot has a restart loop for unexpected Discord/network failures. It creates a fresh Discord client after a failed run instead of reusing a client whose HTTP session may be closed.

---

# 4. DISCORD BOT ARCHITECTURE

`anonymous_bot/bot.py` is the central Discord application entry point.

It creates a `discord.py` `commands.Bot` with:

- member intents enabled,
- message-content intent enabled,
- prefix `!` for legacy/prefix compatibility,
- application/slash commands registered through feature modules.

During `setup_hook()`, the bot initializes the campaign store and lore index, then registers feature modules.

Registered major systems:

- anonymous messaging
- RPG mechanics
- items
- trading
- character creation
- economy
- dungeon
- equipment
- main Discord UI
- GM tools
- GM AI assistant
- help/dashboard
- campaign memory
- companions
- Hell events

The bot centralizes errors for application commands, Discord UI views, and modals so users receive a usable error instead of Discord's generic interaction-failed state.

### Discord event flow

Every incoming Discord message passes through several relevant systems:

```text
Discord message
      │
      ├── web bridge records General/Game message
      ├── server-lore archive
      ├── administrator AI channel handler
      ├── GM `g:` handler / confirmation flow
      ├── DM or server memory archive
      ├── Hell handler
      └── companion handler
```

Bots are excluded from most user-facing processing to avoid loops/noise.

On Discord `on_ready()` the bot:

- starts the web app,
- ensures the administrator-only AI channel exists,
- syncs structured entity profiles into the lore index,
- cleans stale guild-specific command registrations,
- syncs the current global command tree,
- starts background server-lore backfill,
- restores scheduled game starts,
- restores Hell states,
- starts GM spawn scheduling,
- starts the stock-market task when available,
- sets Discord presence to `Anonymous RPG`.

---

# 5. DISCORD CHANNELS / CAMPAIGN ROLES

Configured campaign guild:

- Guild ID: `1535189086258855946`
- **General/OOC:** `1535189087282008114`
- **Game:** `1535189087282008118`

The website and bot treat the Game channel as the live RPG session channel and General as the normal/OOC campaign channel.

GM permissions are based on configured GM IDs, not simply Discord administrator status. The web client separately uses configured tester-GM IDs for web testing.

The administrator-only AI channel is ensured by the bot at startup.

Never expose GM-only information to normal players just because someone has broad Discord permissions.

---

# 6. DISCORD COMMAND GUIDE

The actual command guide is generated by `features/help_ui.py`. Players can use:

- `/help open` — open the categorized command guide.
- `/help command` — explain one command.
- `/dashboard` — personal campaign dashboard.

### Campaign

- `/main`
- `/dashboard`
- `/admin game start`
- `/admin game cancel`
- `/admin game end`
- `/game status`
- `/attendance check-in`
- `/attendance check-out`
- `/admin attendance view`
- `/admin session event`
- `/admin session summary`
- `/admin session history`

### Bounties

- `/bounty list`
- `/bounty place`
- `/admin bounty create`
- `/bounty info`
- `/admin bounty edit`
- `/bounty claim`
- `/admin bounty complete`
- `/admin bounty cancel`
- `/bounty history`
- `/admin bounty remove`

### Items / Inventory

- `/admin item create`
- `/admin item edit`
- `/item rarities`
- `/item claim`
- `/item info`
- `/item catalog`
- `/admin item catalog-remove`
- `/inventory inventory`
- `/inventory give`
- `/inventory secure`
- `/inventory unsecure`
- `/inventory secure-held`
- `/inventory steal`
- `/inventory rename`
- `/admin inventory take`
- `/admin inventory inventory-view`

### Item drops / GM RNG tools

- `/admin item rng-start`
- `/admin item rng-stop`
- `/admin item dm-start`
- `/admin item dm-stop`
- `/admin item status`
- `/admin item force-random`
- `/admin item force`
- `/admin item dm-force-random`
- `/admin item dm-force`
- `/admin item dm-time`
- `/admin item dm-chance`

> Important current behavior: automatic item RNG was removed. GM-created spawn timers are the automatic spawn mechanism. Do not reintroduce background item RNG unless explicitly requested.

### Factions / reputation

- `/faction info`
- `/admin faction create`
- `/faction join`
- `/faction donate`
- `/reputation view`
- `/admin reputation set`
- `/admin reputation add`
- `/admin reputation player`

### Party

- `/party create`
- `/party invite`
- `/party join`
- `/party leave`
- `/party info`

### Secrets / story

- `/story secret-channel`
- `/story traitor-channel`
- `/story dead-drop`
- `/admin story objective`
- `/story my-objective`
- `/admin story objective-complete`
- `/admin story objective-clear`
- `/admin story start-ballot`
- `/story ballot-status`
- `/anonymous send`
- `/anonymous dm`
- `/anonymous one-time`

### Economy

- `/economy balance`
- `/economy overview`
- `/economy values`
- `/economy shop`
- `/economy buy`
- `/economy sell`
- `/economy give`
- `/economy stocks`
- `/economy invest`
- `/economy sell-stock`
- `/economy gamble`
- `/economy gambling`
- `/gm`
- `/gm-economy`
- `/admin economy shop-add`
- `/admin economy shop-vip`
- `/admin economy shop-remove`
- `/admin economy shop-price`
- `/admin economy stock-create`
- `/admin economy stock-increase`
- `/admin economy stock-decrease`

### Companion

- `/companion hub`
- `/companion name`

### Dungeon

- `/dungeon open`
- `/dungeon move`
- `/dungeon event`
- `/dungeon fight`
- `/dungeon search`
- `/dungeon leaderboard`
- `/admin dungeon lock-floor`
- `/admin dungeon unlock-floor`

### Equipment / trading

- `/equipment equip`
- `/equipment unequip`
- `/equipment use`
- `/equipment view`
- `/trade start`
- `/trade add`
- `/trade remove`
- `/trade money`

### Memory / lore archive

- `/memory search`
- `/memory info`
- `/gm-memory channel`
- `/gm-memory priority`
- `/gm-memory backfill`
- `/gm-memory suggestions`
- `/gm-memory delete`

Memory commands are role-sensitive; GM-only records must stay hidden from players.

### Administration / Hell

- `/admin panel`
- `/admin gm-panel`
- GM Hell controls: start Hell event, lock Hell, turn messages on/off, view message status.
- `/admin clear`

---

# 7. WEBSITE: HOW TO NAVIGATE

The browser client is `Anonymous_BotV2/index.html` and is a game-first single-page UI.

The left sidebar is the primary navigation. The header contains session status, sidebar toggle, Main shortcut, music mute, and General shortcut.

## Main player navigation

- **Game** — primary live RPG screen; Game chat, current session state, game-side information, danger presentation, music/session UI, and GM quick controls when authorized.
- **Character** — the player's current character information. It intentionally has no Age field.
- **Inventory** — owned items and item-related player controls.
- **General** — OOC/general campaign chat.
- **Private Chats** — direct/private campaign conversations.
- **Group Chats** — group conversations.
- **Death** — death/death-state information and related UI.
- **World** — world information/codex-style content.
- **Aro** — Aro-related player/world information.
- **Character Codex** — character/entity reference information.
- **Notifications** — campaign notifications.
- **World Lore** — accessible campaign lore.
- **Announcements** — campaign announcements.
- **Rules** — campaign/game rules.
- **Loot Drop** — loot-drop information.
- **Game Ideas** — campaign/game ideas.
- **Settings** — player-facing settings.

## GM-only website navigation

These are hidden unless the authenticated viewer is recognized as a GM:

- **World Loom** — GM world/story construction tools.
- **Events & History** — campaign event/history controls.
- **World Map** — GM map controls/presentation.
- **Scene Studio** — scene construction/presentation.
- **Regional Atmosphere** — region-based atmosphere controls.
- **Economy & Currencies** — GM economy controls.
- **GM Admin Center** — administrative campaign controls.

GM Chat is GM-only and belongs on the GM Chat page; do not move it into the player Game page.

### Sidebar behavior

The sidebar can be hidden with the header sidebar toggle. Category/channel collapsing is intended behavior and is currently a known regression to fix.

On small screens the sidebar becomes a horizontally scrolling sticky navigation bar and the Game layout becomes stacked.

---

# 8. WEBSITE GAME PAGE

The Game page is the primary RPG interface.

Typical structure:

```text
Game
├── Game / Discord-backed chat
│   ├── visibility controls
│   ├── message list
│   ├── attachments
│   ├── replies / message actions
│   └── composer
│
└── right-side game information
    ├── current campaign/session information
    ├── danger/world-state presentation
    ├── GM quick controls (GM only)
    └── other live campaign cards
```

The web UI receives live state using server-sent events (SSE) when available and has slower polling/recovery behavior as fallback.

Game messages arriving over SSE are appended to the local recent-message list rather than forcing a full historical reload.

---

# 9. AUTHENTICATION AND SECURITY

The website authenticates through **Discord OAuth2**.

High-level flow:

```text
Browser
  ↓
Discord OAuth login
  ↓
OAuth callback
  ↓
server verifies Discord identity
  ↓
server determines player/GM authorization
  ↓
signed HttpOnly session cookie
  ↓
web API requests
```

The browser never receives the Discord client secret or bot token.

The web session uses a signed cookie (`anon_session`) with HttpOnly/SameSite behavior and Secure when configured for HTTPS.

Configuration is loaded from `.env` / environment variables. Important private values include:

- `DISCORD_TOKEN`
- `DISCORD_CLIENT_ID`
- `DISCORD_CLIENT_SECRET`
- `ANONYMOUS_SESSION_SECRET` / `WEB_SESSION_SECRET`
- AI provider API keys
- optional GitHub token and other provider credentials

**Never put real secrets in PLAN.md, GitHub commits, handoff packages, screenshots, or player-visible UI.**

The safe template is `anonymous_bot/.env.example`.

---

# 10. WEB ↔ DISCORD REQUEST FLOW

The web server in `web_app.py` bridges the authenticated browser to Discord/campaign state.

Important endpoints/contracts include:

- `GET /api/session` — authenticated identity, GM role, and character.
- `GET /api/world` — current world/session/audio state.
- `POST /api/gm/session` — GM start/end session action.
- `GET /api/channel-messages` — recent Game-channel messages.
- `POST /api/messages` — send Game-channel message.
- `GET /api/social?kind=ooc` — General/OOC messages.
- `POST /api/social/ooc` — send General/OOC message.
- `POST /api/social/settings` — explicit player settings save.
- `POST /api/gm/audio` — GM audio upload/play/stop.

### Session start/end

The web Start Session / End Session control is **not supposed to be a fake browser-only toggle**. It is Discord-backed and changes the real campaign session state.

---

# 11. CAMPAIGN STATE / STORAGE

Campaign state is persisted primarily through `anonymous_bot/core/campaign_store.py` and the campaign database.

The web world state is persisted through the web state helpers and `campaign_data/web_world_state.json`.

Backups are part of the persistent-state architecture.

Do not casually reset or regenerate campaign data during feature work.

---

# 12. MEMORY / LORE ARCHITECTURE

Campaign memory and Project Brain are different systems.

**Project Brain** describes the software/project itself.

**Campaign memory/lore** describes the RPG world, characters, events, Discord history, and campaign knowledge.

Do not merge these concepts or put runtime player/campaign secrets into Project Brain.

The lore index provides structured entity retrieval while campaign memory provides archived/retrieved context.

---

# 13. AI ARCHITECTURE

AI provider selection/fallback behavior lives in the AI provider modules.

AI should use relevant context rather than dumping the entire campaign database into every request.

AI-assisted features must respect canon/source-of-truth rules and GM/player privacy boundaries.

AI suggestions are not automatically authoritative game outcomes unless the surrounding feature explicitly defines them as such.

---

# 14. MUSIC / AUDIO ARCHITECTURE

Audio is a shared bot + website subsystem.

### Music

The project has a large shared music collection (roughly 1,500 tracks) with contextual tags such as:

- `sad`
- `scary`
- `action`
- `calm`
- `dark`
- `funny`
- `main_ost`

Music selection is context/tag driven. Music should transition smoothly on mood changes rather than abruptly hard-cutting unless a deliberate effect requires it.

### SFX

SFX uses a local generated/expanded library plus layered event recipes. Existing families include combat, explosions, lasers, magic, fire, ice, lightning, weapons, impacts, movement, teleportation, shields, parries, ground destruction, rubble, time stop/reverse, healing, footsteps, doors, vehicles, environmental sounds, and cinematic effects.

SFX can be layered. A single GM narration event may produce a sequence such as:

```text
missile launch → whoosh → explosion → ground impact → shockwave → rubble
```

The exact sequence is selected from detected context and available library assets.

### GM narration is the audio trigger source

The **GM's written story/narration is the authoritative text input for automatic music and SFX detection.** Player messages are not audio triggers.

```text
GM narration
      ↓
context/event + mood detection
      ↓
music selection + layered SFX selection
      ↓
playback
```

Example:

`"Zero shot out missiles out his staff, exploding the terra beneath him"`

should detect projectile/missile + explosion + ground destruction/impact context and produce appropriate layered SFX.

Music follows the same principle. For example, GM narration describing an ominous battlefield can move music toward `dark/scary/tension`, while an explicitly narrated battle can move it toward `action/combat`.

The system should understand context and combinations rather than trigger from a single exact keyword.

### Local audio

Local development uses:

- `campaign_data/web_audio/` — full local music library;
- `campaign_data/web_sfx/` — full local SFX library;
- `anonymous_bot/local_audio_library.py` — unified runtime registry/scanner.

The local registry should expose all locally available music and SFX to the local website without requiring the binary audio files to be committed to GitHub.

### Cloud audio

The eventual shared/public architecture uses external object storage such as Cloudflare R2/S3-compatible storage.

```text
Browser
   ↓
Audio API/service
   ↓
metadata
   ↓
cloud object storage/CDN
```

The browser streams requested tracks/SFX instead of downloading the complete shared library. Storage credentials never belong in frontend code.

The repository should contain code, configuration templates, schemas/metadata where appropriate, and Brain—not the 1,500 shared audio binaries.

### GM audio management

The website should provide a GM-facing audio library manager where authorized GMs can:

- upload music;
- upload SFX;
- assign/edit multiple tags;
- search/filter by tags;
- preview audio;
- edit metadata;
- delete assets they own/manage;
- use automatic tag suggestions and approve them.

Music and SFX remain separate logical libraries while sharing common audio-library infrastructure.

### Permission boundary

Audio playback follows the GM narration model: the player does not cause automatic audio playback by merely describing an action. The GM's actual narration is what the detector processes.

The backend must still enforce authenticated campaign/GM permissions for GM-only library management and explicit audio-control endpoints. Frontend button hiding is not sufficient security.

---

# 15. CLOUD / LOCAL DEVELOPMENT MODEL

Local development must be able to use the complete locally available audio collection without requiring the user to commit those files to GitHub.

Cloud production must reference remote object storage without downloading the complete library to every cloned installation.

The two modes share the same logical audio metadata/selection model:

```text
                 Audio Library
                /             \
        LOCAL STORAGE       CLOUD STORAGE
        web_audio/sfx       R2/S3/CDN
             ↓                  ↓
          local URL          remote URL
                \              /
                 selection API
```

`START_EVERYTHING.bat` must never download the complete shared audio library merely to start the application.

---

# 16. GM / PLAYER PRIVACY AND PERMISSIONS

GM-only UI and data must remain hidden from players.

GM authorization is determined server-side using configured GM identity/role information.

Player actions and player-authored text must not be treated as GM narration for automatic audio playback.

Player-authored content can be used by other AI/game systems where explicitly intended, but the automatic audio detector's story input is the GM narration.

---

# 17. WEB UI RULES

Current UI direction is game-first and should not be unnecessarily redesigned while implementing backend/audio functionality.

New controls must have real actions, a clear disabled state, or an explicit unavailable state.

Responsive layouts must preserve usability on desktop and smaller screens.

---

# 18. SERVER-SENT EVENTS / LIVE STATE

Live state should use SSE where available rather than repeatedly reloading large datasets.

Relevant live events include campaign/session changes, messages, audio changes, and other state updates that require immediate UI synchronization.

---

# 19. CURRENT AUDIO IMPLEMENTATION NOTES

Verified existing audio foundations include:

- `anonymous_bot/music_service.py` with local/cloud URL behavior and manifest construction;
- `anonymous_bot/sfx_engine.py` for event detection, starter SFX, and procedural generation;
- `anonymous_bot/sfx_expansion.py` for expanded generated SFX;
- `anonymous_bot/sfx_layers.py` for layered recipes;
- `anonymous_bot/sfx_specials.py` for special SFX behavior;
- `anonymous_bot/sfx_client.js` for browser-side SFX playback;
- `anonymous_bot/local_audio_library.py` for unified local registry.

These files are the foundation for completing the full local hosting and later cloud-library system. Verify source before extending them.

---

# 20. KNOWN / ACTIVE WORK

The audio system is currently an active development area. The local unified registry exists, but the full local web audio-library manager and complete GM-narration-to-playback integration still need to be implemented and verified.

The eventual cloud library/storage/upload system remains to be implemented unless source verification shows otherwise.

Do not mark these systems implemented merely because their architecture is documented here.

---

# 21. REGRESSION RULES

These are mandatory unless the user explicitly requests a behavior change.

- Game-page GM quick-control panel remains on Game.
- GM Chat remains GM-only and on GM Chat.
- General uses the configured General Discord channel.
- Game uses the configured Game Discord channel.
- Character remains the Character navigation item and has no Age field.
- Players cannot see GM controls or GM-only data.
- Players see the danger visual effect but not its percentage/explanation.
- Discord OAuth/session authorization remains intact.
- Tester-GM authorization remains intact.
- Start/end session remains Discord-backed.
- The website must not pretend a Discord action succeeded when it did not.
- Live server events should be preferred over repeated full polling reloads.
- Current UI direction should not be unnecessarily redesigned.
- New buttons must have real actions, a clear disabled state, or an explicit unavailable label.
- Do not silently save player settings that are supposed to require Save.
- Do not introduce unrelated refactors while fixing a targeted bug.
- Never put secrets in source/docs/hand-offs.
- **GM narration, not player narration, is the automatic music/SFX story input.**
- **Do not trigger automatic music/SFX from player-authored action text.**
- **Local audio mode must not require committing the user's complete audio collection to GitHub.**
- **Cloud audio mode must not download the complete shared library just to start a cloned installation.**

---

# 22. TESTING / VERIFICATION

Before considering a web/UI change complete, run:

```text
python tests/ui_integrity_check.py
python tests/web_regression_check.py
```

When the web server changes:

```text
python -m py_compile anonymous_bot/web_app.py
```

When bot modules change, verify the bot starts and Discord command sync completes.

Manually test every changed button/action under the correct player or GM role.

For audio changes, verify:

- GM narration can be analyzed;
- player text does not trigger automatic audio;
- music context changes correctly;
- layered SFX can be selected;
- local audio URLs resolve;
- the browser can play the selected local asset;
- missing assets fail gracefully;
- the complete local collection is discoverable without committing it to GitHub.

For session changes, verify both browser state and actual Discord Game-channel permissions.

For message-performance changes, verify that:

- recent messages appear,
- new messages arrive live,
- old history remains available through Discord,
- the browser does not repeatedly reload huge history.

---

# 23. CONFIGURATION / CREDENTIALS

Configuration is loaded by `anonymous_bot/config.py` from environment variables / `.env` locations.

Examples of configuration include:

- Discord bot token
- Discord OAuth client ID/secret
- redirect URL
- web session secret
- web host/port
- campaign name
- game/general channel IDs
- GM/tester IDs
- AI provider/model settings
- Ollama URL/model
- campaign data directory
- server-lore collection options

`anonymous_bot/.env.example` is safe to use as a template.

**Never commit the real `.env` or expose credentials in PLAN.md.**

---

# 24. WINDOWS / LOCAL DEVELOPMENT

The user's local repository path is currently:

```text
C:\Users\maksi\Desktop\anonymous bot\bot
```

GitHub Desktop is connected to this repository.

The user's separate local `auto-sync.ps1` setup is intended to pull GitHub changes into this exact repository every 30 seconds:

```powershell
while ($true) {
    Set-Location "C:\Users\maksi\Desktop\anonymous bot\bot"
    git pull origin main
    Start-Sleep -Seconds 30
}
```

This is a local convenience and is **not part of the bot application**. Do not confuse it with project runtime behavior.

GitHub → PC can be automated with that pull loop. PC → GitHub still requires a commit/push through Git/GitHub Desktop.

---

# 25. DEVELOPMENT WORKFLOW

Preferred workflow:

```text
User request
    ↓
PLAN.md first
    ↓
identify relevant subsystem
    ↓
inspect only relevant source/tests
    ↓
make smallest safe change
    ↓
run appropriate regression checks
    ↓
update PLAN.md if architecture/behavior/backlog changed
    ↓
commit/push to GitHub
```

The **BRAIN → CODE rule takes precedence over treating Brain as passive documentation**: if a user-approved requirement is recorded in Brain and remains unimplemented, it is active unfinished work and should be implemented as the relevant subsystem is worked on.

---

# 26. FUTURE / ROADMAP AREAS

Focused game modules include:

- Character
- Inventory
- World/Codex
- General chat
- GM controls

Other continuing systems include:

- world/lore management,
- map and scene presentation,
- regional atmosphere,
- economy and currencies,
- companion gameplay,
- dungeon gameplay,
- faction/reputation,
- bounties,
- secret story objectives,
- campaign notifications,
- timelines/personas/POV/storybooks,
- AI-assisted campaign management.

Only add or expose functionality when the underlying action actually works.

---

# 27. SOURCE-OF-TRUTH RULE

`PLAN.md` is the **one real project Brain**.

The former standalone documentation concepts from `README.md`, `AI_README.md`, and `REGRESSION_POLICY.md` have been consolidated into this file.

Do not recreate competing documentation unless there is a strong technical reason.

When a significant feature, bug fix, architecture change, regression rule, AI behavior rule, navigation change, or project-direction decision is made, update this file so the next AI/developer can understand the project without rereading the entire repository.

**Keep this file accurate. Keep it useful. Keep it focused on the actual bot/system.**

---

# 28. PROJECT BRAIN OPERATING RULES

**Project Brain = `PLAN.md` = PB = P.B. = Plans.** These names all refer to the same Project Brain.

The actual source code is the ultimate authority for what the software currently does. Project Brain describes that current state in human/AI-readable form.

### Synchronization rule

Whenever an approved code change changes an existing feature, architecture, navigation, endpoint, configuration behavior, regression rule, testing requirement, or other documented behavior, the AI **MUST update the corresponding Project Brain section** so `PLAN.md` accurately describes what actually exists in code.

The AI must not invent implemented behavior, claim unfinished work is complete, or silently rewrite active requirements because it has an opinion.

If code and Brain disagree, inspect the code first. If the difference is accidental or unresolved, document it as a known bug/regression rather than pretending the intended behavior exists.

### Documentation consolidation

`README.md`, `AI_README.md`, and `REGRESSION_POLICY.md` are consolidated into Project Brain. Do not recreate competing documentation systems unless technically necessary.

Project Brain is for the BOT/RoR software and development system. Runtime campaign memory is separate and must not be confused with PB.

---

# 29. AI CONTEXT / TOKEN-SAVING RULES

Read Project Brain first. Inspect only the code/data directly relevant to the current task.

Do not automatically load the entire repository, ancient Discord history, the entire campaign database, unrelated source files, duplicate documentation, or large old message archives.

Context priority:

1. Current user request.
2. Current relevant game/session state.
3. Relevant Project Brain sections.
4. Relevant structured lore/profile information.
5. Small recent message/conversation window.
6. Additional evidence only when required.

The goal is **relevance-first context reduction**, not blindly cutting newest or oldest text.

Future context-budget systems should support bounded recent messages, lore, records, characters, per-section character/token limits, total AI context limits, and provider/model-specific limits.

---

# 30. ADD.md — EXPLICIT USER-AUTHORIZED WORK INBOX

`ADD.md` is an optional implementation inbox.

The existence of `ADD.md` is **not permission to act**. The AI processes it only when the user explicitly asks it to process/show/implement the requests in the file.

### Safety rules

- `ADD.md` contains explicit user requests, not AI ideas.
- Do not silently expand the requested scope.
- Do not treat silence as approval.
- Do not turn an AI opinion into an ADD request automatically.
- If a request is ambiguous or conflicts with Project Brain, stop and explain the conflict instead of silently choosing.
- Protect existing working systems and campaign data.
- If implementation fails or is partial, **do not clear the unfinished request**.
- Preserve unfinished portions for later.
- Clear a request only after successful implementation and verification.
- After successful implementation, synchronize Project Brain and the Update Log before clearing the completed request.
- Never place secrets in `ADD.md`.

Recommended structure:

```md
# ADD — WORK QUEUE

<!-- Put explicit user-authorized work requests below this line. -->
```

Lifecycle:

```text
User writes request
      ↓
User explicitly asks AI to process ADD.md
      ↓
AI reads ADD.md + relevant Project Brain sections
      ↓
AI inspects relevant code
      ↓
AI implements only the requested work
      ↓
AI verifies it
      ↓
Project Brain synchronized
      ↓
Update Log entry created
      ↓
Completed request cleared
```

---

# 31. IDEAS — AI OPINIONS AND SUGGESTIONS ONLY

`IDEAS` is where AI suggestions, recommendations, critiques, optimizations, UX suggestions, performance suggestions, maintainability suggestions, and opinions belong.

**If the AI wants to give an opinion about improving the project, that opinion belongs in IDEAS.** It must not silently become an active requirement.

After a meaningful update, AI may suggest useful ideas. If there is nothing genuinely useful to suggest, it should add nothing.

### Ideas safety

- Ideas are suggestions only.
- AI must not automatically implement an Idea.
- AI must not automatically approve or promote an Idea.
- Explicit user approval is required before an Idea becomes active work.
- Approval is never inferred from silence.
- Check existing Ideas before suggesting duplicates.
- Ideas include creation version and timestamp.
- Stale Ideas are marked `Outdated` rather than silently deleted.
- Replaced Ideas are marked `Superseded` with the replacement identified.

Supported statuses:

- `Suggested`
- `Approved`
- `Implemented`
- `Rejected`
- `Outdated`
- `Superseded`

Suggested format:

```md
### Idea — v1.x — YYYY-MM-DD HH:MM UTC
**Idea:** ...
**Why:** ...
**Impact:** Low / Medium / High
**Status:** Suggested
```

---

# 32. UPDATE LOG

- **Current baseline:** Project Brain rules now explicitly require meaningful user/collaborator suggestions and project-direction decisions to be recorded in Brain, while distinguishing optional Ideas from approved implementation requirements.
- **Current audio work:** GM narration is the authoritative automatic music/SFX input; local unified audio-library work is active; cloud shared audio remains a later implementation stage.

---

# 33. MAINTENANCE

Keep `PLAN.md` concise enough to serve as a routing/context layer.

When a section becomes stale, correct it rather than stacking contradictory notes.

When source code proves a Brain claim wrong, update the Brain to reality.

When a user makes a meaningful project decision, record the decision and then implement it when authorized.

Never let Project Brain become a second codebase full of fictional claims.