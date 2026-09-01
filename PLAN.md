# 🧠 PROJECT BRAIN — Anonymous RPG Bot / Regnum of Regalia

> **Single source of truth for AI-assisted development, project understanding, navigation, architecture, Discord behavior, and current plans.**
>
> Read this file first. Do not automatically read other documentation. Inspect source files only when a task requires them, and then inspect the smallest relevant set.

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

The web Start Session / End Session control is **not supposed to be a fake browser-only toggle**.

The server changes the configured Discord Game channel permissions and publishes the session announcement. If Discord work fails, the browser should report the failure rather than pretending the session changed.

The end-of-session review is optional; failure to post the review must not undo a successful channel lock/session close.

---

# 11. REAL-TIME MESSAGE SYSTEM

The preferred path is:

```text
Discord / web message
       ↓
backend bridge
       ↓
SSE event
       ↓
connected browsers
       ↓
append recent message locally
```

SSE event types include the concepts of:

- `world_state`
- `game_message`
- `general_message`
- `social_update`
- audio/state events

Polling is a recovery fallback, not the preferred constant full-refresh mechanism.

### Current performance problem

The website's Discord message shower/reload feels laggy because too much historical material can be involved.

Desired behavior:

- Discord keeps the full history.
- Website initially shows a sensible recent window.
- Live messages arrive through SSE.
- Old history is loaded only when explicitly needed.
- Do not repeatedly re-download ancient Discord history just because the UI refreshed.
- Do not feed ancient message archives into every AI request.

---

# 12. CAMPAIGN STATE / DATABASE

`campaign_data/campaign.db` is the persistent campaign store. `core/campaign_store.py` handles initialization/storage/backups.

`web_world_state.json` stores web/game state such as:

- campaign identity,
- world threat/danger,
- region danger,
- session state,
- chapter/session history,
- events,
- lore connections,
- emergency state,
- OOC/DM/group information,
- player settings,
- GM messages/notifications,
- companions,
- audio assets/active audio,
- main OST selection,
- ability catalog,
- character journals/inbox,
- economy state,
- advanced atmosphere/timeline/persona/POV/storybook state.

The web server uses a world-state lock and atomic temporary-file replacement when saving the JSON state.

Do not casually edit live SQLite/JSON campaign data from source-code changes. Treat campaign data as runtime state.

---

# 13. CAMPAIGN MEMORY / LORE SYSTEM

There are two different meanings of “memory” in this project:

### Runtime campaign memory

`features/memory.py` and `features/server_lore.py` archive Discord/campaign information so the bot can retrieve it later.

It can contain:

- raw Discord messages,
- lore facts,
- sessions,
- structured entities/profiles,
- relationships,
- priority sources,
- suggestions,
- DM/archive information.

Server lore continuously archives new messages and can perform a background historical backfill after startup.

### Project Brain

`PLAN.md` is **not** campaign memory. It is the developer/AI knowledge layer describing the software, rules, architecture, navigation, and work plan.

Do not feed the entire runtime memory archive to an AI merely because it exists.

---

# 14. AI SYSTEM

The project supports multiple AI providers/models through `features/ai_providers.py`, with configuration in `config.py`.

Configured provider families include examples such as:

- Gemini
- Groq
- Cerebras
- Mistral
- SambaNova
- OpenRouter
- GitHub Models
- NVIDIA
- Hugging Face
- Chutes
- Pollinations
- LLM7
- local Ollama

`AI_PROVIDER=auto` allows provider selection/fallback behavior.

The administrator AI channel is separate from normal campaign/player use.

### AI responsibilities

The AI can assist with:

- campaign lore
- continuity
- summaries
- NPCs
- characters
- factions
- locations
- items
- mysteries
- dialogue
- encounters
- worldbuilding
- GM preparation
- bot/technical questions
- optional music mood analysis

### AI canon rules

- Clear GM canon is authoritative.
- A later clear GM correction supersedes an older incorrect answer.
- Do not turn inference into canon.
- Do not invent unknown lore, acronyms, abilities, relationships, or motivations.
- Player corrections may provide evidence but do not override established GM canon.
- GM jokes/tests/questions/speculation are not automatically canon.
- GM-only hidden information must never leak to players.
- Structured profiles are preferred over generic raw-message matches for entity questions.
- Session-specific questions must use the correct session record.
- A Discord player and an in-world character are different identities.
- Dead characters remain dead; replacements are new character instances with their own history.

### Known lore corrections

- Aro is a word meaning Energy, not an acronym.
- Mother Prana is the original source of Aro.
- Yellow stones modify the user's Aro to reflect personality; they are not simply generic Aro boosters.
- Mevrick/Vespa: Mevrick believes Vespa is separate and believes fragments keep Vespa away, but that belief is a coping lie; Mevrick is Vespa.

---

# 15. AI CONTEXT / TOKEN BUDGET

The project should minimize unnecessary model context and cost.

The correct context priority is:

```text
1. Current user request
2. Current game/session state when relevant
3. Relevant structured profile/lore
4. Small recent conversation window
5. Additional supporting evidence only if needed
```

Do not send entire archives by default.

The project already has bounded queues/context in several places. Preserve those safeguards while improving them.

A future/ongoing context-budget system should be able to limit:

- recent messages,
- lore facts,
- characters/records per section,
- maximum characters/tokens per section,
- maximum total AI context,
- provider/model-specific budgets.

The important goal is **relevance-first truncation**, not simply cutting the newest/oldest text blindly.

---

# 16. MUSIC / AUDIO SYSTEM

The bundled server music library is organized into mood/category groups, including:

- `action`
- `calm`
- `dark`
- `funny`
- `main_ost`
- `sad`
- `scary`

It lives under `campaign_data/web_audio/` and is indexed by the web backend.

The web audio client supports cross-fading when the active soundtrack changes and has a per-device Music On/Off control.

### Intended music behavior

- Main OST should randomly choose from the Main OST pool instead of repeatedly selecting the same track.
- Main OST should begin on opening as intended by the current product direction, subject to browser autoplay/user-gesture restrictions.
- AI music/mood detection must happen **only while a real Game session is active**.
- AI music detection must not continuously react to ordinary General/OOC chat.
- AI music classification must never block sending a message.
- Player/NPC theme assignments are separate from the global Main OST.
- Never silently replace the global Main OST with a character theme.
- Missing/stale audio files must not remain as dead library records.

---

# 17. DANGER / WORLD ATMOSPHERE

Danger is regional/location-specific. The GM can set a region's 0–100 danger level and a GM description.

The player sees the visual danger effect but not the numeric percentage/explanation.

The UI uses smooth full-interface danger transitions, including a red danger overlay/tint and enhanced borders/shadows as danger rises.

Current requested improvement: the high/red danger state does not read strongly enough. Improve it without destroying the existing smooth dark-fantasy UI.

---

# 18. PLAYER SETTINGS

Player settings are saved explicitly through the web settings endpoint.

Existing settings include behavior such as:

- accent/color preferences,
- reduced animations,
- portrait visibility,
- timestamps,
- message density/compactness,
- other client presentation preferences.

Current requested improvement:

- expose the newly introduced UI colors to players,
- expand useful player configuration,
- keep explicit Save behavior,
- do not silently save every temporary control change.

---

# 19. UI DESIGN CONTRACT

The current UI direction is considered very good. **Do not redesign it unnecessarily.**

The visual identity is a dark-fantasy/game interface with dark panels, muted gray UI, red danger/accent treatment, atmospheric backgrounds, and smooth world-state transitions.

Preserve:

- game-first hierarchy,
- readable chat,
- responsive layout,
- GM/player separation,
- dark-fantasy presentation,
- live session indicator,
- sidebar navigation,
- audio controls,
- player settings behavior.

Chat supports lightweight RPG formatting:

- `**word**` → bold.
- `*word*` → italic when the marker directly touches a letter.
- spaced markers such as `* word *` stay plain text.
- escape message text before adding formatting tags.

---

# 20. CURRENT BUGS / ACTIVE PLAN

These are the user's active priorities and stay here until fixed and verified.

### 1. Main OST

Randomly select a Main OST track from the `main_ost` library instead of repeatedly using the same track. Main OST should start when opening as intended.

### 2. Music detection scope

AI music detection must happen **ONLY during live Game sessions**.

### 3. End Session

Start Session works. End Session currently does not work correctly from the user's perspective. Fix the full Discord-backed end flow.

### 4. Sidebar category collapsing

Category/channel collapse stopped working after the sidebar-hider update. Restore category collapse/expand without breaking the sidebar hider.

### 5. Discord message shower performance

The website should not repeatedly reload very old Discord messages. Keep Discord history intact but make the website recent-first + live-event-driven, with targeted old-history loading only when needed.

### 6. Game menu Settings

The game menu GUI does not currently expose Settings correctly. Make Settings reachable from the intended game menu/navigation flow.

### 7. Player Settings

Add meaningful configuration for the newer UI colors and other useful player presentation settings while preserving explicit Save behavior.

### 8. Danger meter

Make the high/red danger state visually stronger and clearer while keeping the existing smooth full-UI danger effect and player privacy behavior.

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
C:\Users\maksi\Desktop\anonymous bot\BOT
```

GitHub Desktop is connected to this repository.

The user's separate local `auto-sync.ps1` setup is intended to pull GitHub changes into this exact repository every 30 seconds:

```powershell
while ($true) {
    Set-Location "C:\Users\maksi\Desktop\anonymous bot\BOT"
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

For AI tools:

> **PLAN.md is the first and primary context. Do not read the whole repository unless the task genuinely requires it.**

If a task concerns a specific feature, open its source file(s) rather than loading unrelated campaign data or historical Discord archives.

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

`PLAN.md` is now the **one real project Brain**.

The former standalone documentation concepts from `README.md`, `AI_README.md`, and `REGRESSION_POLICY.md` have been consolidated into this file.

Do not recreate competing documentation unless there is a strong technical reason.

When a significant feature, bug fix, architecture change, regression rule, AI behavior rule, navigation change, or project-direction decision is made, update this file so the next AI/developer can understand the project without rereading the entire repository.

**Keep this file accurate. Keep it useful. Keep it focused on the actual bot/system.**
