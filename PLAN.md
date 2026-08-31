# 🧠 PROJECT BRAIN — Anonymous RPG Bot / RoR

> **This is the single source of truth for AI-assisted work on this project.**
> Read this file first. Do not automatically read the other documentation files that were consolidated into it.

## 1. Project purpose

Anonymous RPG Bot / RoR is a Discord-backed RPG companion with a game-first web client. It combines Discord campaign communication, persistent campaign/world memory, live game sessions, music/audio, character/player features, GM controls, and AI-assisted lore interaction.

The project is one evolving product, not a sequence of replacement demos. New work should preserve working behavior unless the user explicitly asks to replace it.

### Main project areas

- `anonymous_bot/` — Discord bot and web-server/backend code.
- `Anonymous_BotV2/` — game-first web interface.
- `campaign_data/` — persistent campaign database/world state and web audio assets.
- `tests/` — regression and UI integrity checks.
- `anonymous_bot/.env` — private local credentials; never share or commit secrets.
- `anonymous_bot/.env.example` — configuration template for a new installation.
- `render.yaml` — optional Render deployment configuration.
- `START_EVERYTHING.bat` — the intended single launcher.

Do not put actual passwords, API keys, Discord tokens, OAuth secrets, or other private credentials in this file.

## 2. AI reading and context rules

### Primary rule
Start with `PLAN.md`. Treat it as the authoritative project overview, development contract, current backlog, and AI behavior guide.

Do **not** automatically read `README.md`, `AI_README.md`, or `REGRESSION_POLICY.md`; their useful contents have been consolidated here. Those files are no longer separate sources of truth.

Only inspect source files when the user's specific task requires them. Open the minimum relevant files needed to understand or implement that task.

### Context/token discipline

Do not load the entire repository into an AI prompt just because it exists. Prefer:

1. This Brain.
2. The specific feature/bug requested.
3. The smallest relevant set of source files.
4. Relevant tests/configuration only when needed.

Do not dump ancient Discord history, unrelated source code, or duplicate documentation into an AI context merely for completeness.

### Development behavior

- Preserve existing working functionality.
- Prefer small, targeted changes over unnecessary rewrites.
- Do not change unrelated files.
- Before a large refactor, explain why it is needed.
- Never add a visible UI control unless it has a working action, a clear disabled state, or an explicit "coming with music pack" label.
- Verify a button's server request and success/failure feedback before considering it complete.
- Keep desktop and mobile usability intact.
- Use the web audio engine for UI button feedback and respect the global mute control.
- Player-facing settings are persisted only when the player explicitly saves them.
- Never put private credentials, campaign data, or uploaded music into a handoff package.
- When a feature is completed, update this Brain with the result and remaining work.

## 3. Product direction / UI contract

The website is a **game-first RPG companion**, not an admin dashboard. The primary screen should be understandable without reading documentation.

The current UI direction is good and should be preserved. Do not redesign it unnecessarily.

### Protected UI behavior

- GM quick-control panel stays on the Game page.
- GM Chat is GM-only and exists only on the GM Chat page.
- General uses Discord channel `1535189087282008114`.
- Game uses Discord channel `1535189087282008118`.
- Character navigation remains `Character` and has no Age field.
- Player pages never expose GM controls or GM-only data.
- Danger percentage/explanation is hidden from players; only the visual effect is shown.
- Preserve the neutral black/gray/red visual direction while improving weak visual treatment when specifically requested.
- Discord OAuth/session and tester-GM authorization must remain intact.
- Navigation should remain usable on narrow/mobile screens.
- Decorative RPG role icons and visual command/status treatments are CSS-only cues; they must not become extra controls or obscure existing actions.

### Chat formatting

Chat deliberately supports lightweight RPG emphasis only:

- `**word**` → bold.
- `*word*` → italic when the marker directly touches a letter.
- Spaced markers such as `* word *` remain plain text.
- Escape message text before adding formatting tags.

## 4. Backend and web contracts

Important existing endpoints/contracts to preserve unless a change explicitly replaces them:

- `GET /api/session` — signed-in user, GM role, and character.
- `GET /api/world` — session/world state and audio state.
- `POST /api/gm/session` — GM session toggle.
- `GET /api/channel-messages` and `POST /api/messages` — game-channel chat.
- `GET /api/social?kind=ooc` and `POST /api/social/ooc` — General chat.
- `POST /api/social/settings` — explicit player settings persistence.
- `POST /api/gm/audio` — GM audio asset upload/play/stop.

### Session behavior

Starting or ending a session from the website is Discord-backed. A session action must change the configured game-channel permission and publish the session announcement. If the bot cannot complete that work, the website must report an error instead of pretending a web-only session occurred.

The end-of-session review post is optional: if it fails, that must not prevent the session from being marked closed after the Discord channel lock succeeds.

### Health/launch behavior

`/healthz` and `START_EVERYTHING.bat` are healthy only when the Discord bot event loop and configured campaign guild are ready. A responding web server by itself is a failure state.

Diagnostics should include image/attachment-relevant Discord permissions.

`START_EVERYTHING.bat` is the single intended launcher. It uses the project `.venv` first, then a validated Python 3.11+ installation. Do not add duplicate launcher scripts.

## 5. Music system

The server music library is grouped as supplied:

- `action` — 47
- `calm` — 28
- `dark` — 42
- `funny` — 25
- `main_ost` — 32
- `sad` — 33
- `scary` — 29

It is indexed recursively on startup and served from `campaign_data/web_audio/`.

### Intended music behavior

- Main OST should be selected automatically from the `main_ost` group rather than repeatedly using the same track.
- The user wants the Main OST to begin when the game/site opens; preserve the intended global/main behavior while ensuring game-session-only detection remains separate.
- When a track changes, fade the previous server soundtrack down and fade the next one in.
- Do not leave stale library records pointing at files that were moved or removed.
- Theme-song assignments for players and NPCs are stored separately and must never silently overwrite the global Main OST.
- AI music/lore detection must operate **only during active game sessions**. It should not continuously react to ordinary/non-session chat.
- AI mood classification must never block sending a message.
- Clear scenes may map to `action`, `calm`, `dark`, `funny`, `main_ost`, `sad`, or `scary`; uncertain chatter should do nothing.

## 6. AI and campaign-lore behavior

The AI is used for campaign lore, continuity, summaries, NPCs, characters, factions, locations, items, mysteries, brainstorming, dialogue, encounters, worldbuilding, GM preparation, and bot/technical questions.

Suggestions must be labeled as suggestions. Never silently create canon or alter game state.

### Canon authority

- Clear GM canon statements are authoritative.
- If a clear GM canon statement corrects a previous answer, accept the correction immediately.
- Never defend an outdated answer because an archive or AI summary disagrees with a later GM correction.
- Never turn an inference into canon; explicitly state uncertainty.
- Do not invent acronyms, expansions, abilities, motivations, relationships, factions, or lore because a term is unfamiliar.
- Player corrections can help locate evidence but do not override established GM canon.
- If the current administrator is not a configured GM/writer, do not treat their statements as authoritative canon merely because they have Discord admin permissions.
- Obvious GM jokes, tests, questions, roleplay, hypotheses, and speculation are not automatically canon.
- A GM asking a question is not itself a canon declaration.
- If a deliberately false GM test is revealed as a test/joke, discard it as canon immediately.
- Do not use numeric confidence scores as permission to override a clear later GM correction.

### Chronology and identity

- Later clear GM corrections supersede earlier claims about the current state while preserving historical truth.
- A newer session recap must not rewrite an older session.
- Questions about a specific session must be answered from that session's record.
- "First session", "first ever session", or "session 1" means Session #1 specifically. If it is not recorded, say so.
- Chronology questions must be answered chronologically.
- Structured profiles are primary for entity questions: summary, backstory, origin, role, facts, relationships, status, sessions, first/last appearance, death, and player/character lifecycle should be preferred over generic raw message matches.
- For "who is X" / "tell me about X", explain the structured profile first and then only directly relevant evidence.
- For "what happened last time X played", use the player/character activity record for the latest session in which that player/character had recorded activity.
- A Discord player and their in-world characters are separate identities. A player may have multiple characters; a dead character remains dead and a replacement is a new character instance with its own history.
- GM-only hidden plot information is GM-only and must never leak into player-facing lore responses.

### Known canon corrections

- Aro is a word meaning Energy, not an acronym.
- Mother Prana is the original source of Aro.
- Yellow stones do not simply boost Aro; they modify the user's Aro to reflect the user's personality.
- For Vespa/Mevrick: Mevrick believes Vespa is a separate being and believes fragments keep Vespa away, but that belief is a coping lie; Mevrick is Vespa. Do not confuse Mevrick's belief with the underlying truth.

## 7. Campaign memory vs project Brain

The runtime Discord/campaign memory system is **not** the project's AI/developer Brain.

Runtime campaign memory exists to archive and retrieve Discord/campaign information. It can store raw messages, lore facts, sessions, structured entities, relationships, priority sources, and related campaign state.

The project Brain exists to tell an AI/developer **how the software works, what matters, what is currently broken, and how changes should be made**.

Keep these concepts separate.

Historical Discord backfill intentionally loads accessible campaign text channels and queues material for later classification. Do not mistake that runtime archive behavior for a requirement to feed all historical messages into every AI request.

## 8. Current AI context/token strategy

The goal is to minimize unnecessary model context and cost without destroying useful continuity.

Existing code already uses bounded context in several places, including bounded AI queues and limited recent conversation/lore context. Preserve those safeguards.

When improving this area, prefer a centralized context-budget approach:

1. Current user request.
2. Current game/session state when relevant.
3. Directly relevant structured profile/lore.
4. Small recent conversation window when relevant.
5. Only then additional supporting evidence.

Do not send entire archives by default. Old messages should be retrieved only when the question actually requires them.

A future/ongoing improvement can expose explicit limits for:

- maximum recent messages,
- maximum lore facts,
- maximum characters/tokens per context section,
- maximum total AI context budget,
- optional model/provider-specific budgets.

The budget should prioritize relevant/current/authoritative information instead of simply truncating everything equally.

## 9. Current known bugs and requested work

These are the user's active priorities and should remain visible until fixed and verified.

### High priority

- **Main OST selection:** randomly select a Main OST from the `main_ost` pool instead of repeatedly playing the same track.
- **Main OST startup:** start the Main OST on opening as intended by the current product direction.
- **Music detection scope:** AI music detection must happen **only during active game sessions**.
- **End Session:** Start Session works, but ending a session currently does not work correctly from the user's perspective. Fix the complete Discord-backed end flow.
- **Sidebar categories:** category collapse/expand stopped working after the sidebar-hider update. Restore it without breaking the sidebar hider.
- **Discord message shower performance:** the website's Discord message display/reload feels laggy. Stop repeatedly loading very old Discord messages into the web UI. Discord should retain full history, while the website should prefer a recent window plus live events and load older history only when needed.
- **Game menu Settings:** the game menu GUI does not currently show/provide Settings correctly. Make Settings reachable.
- **Player Settings:** expand player configuration and allow players to change the newly introduced UI colors. Preserve explicit Save behavior.
- **Danger meter:** improve the red danger treatment so it is visually clear while retaining the existing smooth full-UI danger effect and player-facing privacy rule.

### Performance direction for Discord messages

Live server events should be the primary game-message update path. Polling is only a slow recovery fallback. Show an optimistic pending message while Discord webhook delivery finishes.

The web client should not repeatedly reload a huge historical Discord transcript. Keep recent context on the website and use targeted history retrieval for older messages.

### UI quality direction

The current UI is considered very good overall. Fix the listed bugs/features without unnecessary redesign.

## 10. Game modules / roadmap

Planned focused game modules include:

- Character.
- Inventory.
- World/codex.
- General chat.
- GM controls.

Each module should be added only after its live action and error feedback are defined.

## 11. Required verification

Before considering a web/UI change complete, run:

```text
python tests/ui_integrity_check.py
python tests/web_regression_check.py
```

When the server changes, also run:

```text
python -m py_compile anonymous_bot/web_app.py
```

Manually verify each new/changed button while signed in with the appropriate role.

Regression behavior that must remain protected:

- Game-page GM quick-control panel remains on Game.
- GM Chat remains GM-only and on GM Chat.
- General/Game Discord channel assignments remain correct.
- Character remains the Character navigation item with no Age field.
- Players cannot see GM controls or GM-only data.
- Players see the danger effect but not its percentage/explanation.
- Discord OAuth/session and tester-GM authorization remain intact.
- Existing working layout/behavior remains unless explicitly replaced.

## 12. Setup / handoff

To run the project, double-click `START_EVERYTHING.bat`. It installs missing Python packages, starts Ollama when available, starts the Discord bot, waits for the local website, and opens it.

For a new installation:

- Supply the code and `.env.example`.
- The recipient supplies their own Discord credentials/configuration.
- Never share `anonymous_bot/.env`.
- Do not package or hand off private campaign data or uploaded music unless explicitly intended and secured.
- Python 3.11+ is required unless the project `.venv` is already prepared.

## 13. Completed / history

- Project Brain created and made the intended single AI/developer context file.
- README, AI README, and regression-policy information have now been consolidated here.
- Current user-reported feature/bug backlog has been recorded here for continuity.

## 14. Rule for future updates

Whenever a significant feature, bug fix, architecture change, regression rule, AI behavior rule, or project-direction decision is made, update this file in the same change.

**PLAN.md is the source of truth.** Other documentation should not become competing sources of truth.
