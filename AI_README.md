# AI README — Website Rebuild Contract

This file is the source of truth for future AI-assisted changes to the website. Update it in the same change that adds, removes, or changes a website feature.

## Rebuild direction

The website is becoming a game-first RPG companion, not an admin dashboard. The primary screen must be understandable without reading documentation.

## Non-negotiable rules

1. Do not add a visible button unless it has a working action, a clear disabled state, or an explicit "coming with music pack" label.
2. Verify the button's server request and its success/failure feedback before calling the feature complete.
3. Preserve Discord OAuth, GM authorization, campaign data, and the game/general message bridges unless a user explicitly asks to replace them.
4. Keep the UI usable on desktop and mobile.
5. Use the web audio engine for UI button feedback; respect the global mute control.
6. Persist player-facing settings only when the player explicitly saves them.
7. Do not place private credentials, campaign data, or uploaded music in a handoff package.
8. Chat supports only deliberate lightweight RPG emphasis: `**word**` renders bold and `*word*` renders italic when each asterisk marker directly touches a letter. Spaced markers such as `* word *` remain plain text. Escape message text before adding these tags.

## Current build phases

### Phase 1 — Game shell (in progress)

- Replace the dashboard-style layout with a scene-first game screen.
- Animated world background, readable game panels, and visible global mute control are implemented.
- Button sound effects are generated in-browser, so no separate SFX files are required.
- The active game screen uses grouped character-style messages and a touch-first mobile shell; navigation, controls, and the composer remain accessible on narrow screens.
- Navigation has decorative RPG role icons and the command/status cards use game-state visual treatment. These are CSS-only cues: they must never become separate controls or obscure the existing working actions.
- Starting or ending a session from the site is Discord-backed: it must change the configured game-channel permission and publish the session announcement. If the bot cannot complete that work, the site reports an error instead of claiming a web-only start.
- `/healthz` and `START_EVERYTHING.bat` are healthy only when the Discord bot event loop and configured campaign guild are ready. A responding web server alone is a failure state. Diagnostics must include image/attachment-relevant Discord permissions.
- Browser image messages must attach the actual file to Discord, not merely display a local preview in the website. A failed optional end-of-session review post must not prevent the session from being marked closed after the channel lock succeeds.
- The neutral character screen is named **Character**. Never infer a character from a Discord username or channel; only show a character name after a player or GM explicitly creates/assigns one.
- Live server events are the primary game-message update path. Keep polling as a slow recovery fallback, and show an optimistic pending message while a Discord webhook delivery finishes.
- Danger is a scene effect: it must transition in and out smoothly, tint the full game UI red, and use both bright and dark red borders without blocking controls.
- The displayed danger percentage must count smoothly to a new value; it must not jump straight from one number to another.
- Desktop navigation is a compact game rail, not a scroll of large boxed buttons. The right side is a restrained HUD: use one shell with clear danger, GM controls, character, inventory, and session hierarchy instead of unrelated rounded cards.

### Phase 2 — Music system (in progress)

- The server music library is grouped exactly as supplied: `action` (47), `calm` (28), `dark` (42), `funny` (25), `main_ost` (32), `sad` (33), and `scary` (29). It is indexed recursively on startup and served from `campaign_data/web_audio/`.
- Main OST is selected automatically from the `main_ost` group unless the GM explicitly chooses another track. It starts server-wide when a session starts; every player can mute it locally.
- When a track changes, fade the previous server soundtrack down while fading the next one in. Do not leave stale library records pointing to files that were moved or removed.
- AI lore classification receives browser-originated roleplay as well as Discord messages. Its structured `music_mood` cue can select `action` for a real battle, plus calm/dark/funny/main_ost/sad/scary for clear scenes. It must do nothing for uncertain chatter and must never block sending a message.
- Theme-song assignments are stored separately for players and NPCs. Do not let a theme assignment silently overwrite the global Main OST.

### Phase 3 — Game modules (planned with the user)

- Character, inventory, world/codex, general chat, and GM controls as focused game panels.
- Each module is added only after its live action and error feedback are defined.

## Existing working backend contracts to retain

- `GET /api/session` — signed-in user, GM role, and character
- `GET /api/world` — session/world state and audio state
- `POST /api/gm/session` — GM session toggle
- `GET /api/channel-messages` and `POST /api/messages` — game-channel chat
- `GET /api/social?kind=ooc` and `POST /api/social/ooc` — General chat
- `POST /api/social/settings` — explicit settings persistence
- `POST /api/gm/audio` — GM audio asset upload/play/stop

## Required verification for each UI change

- Run `python tests/ui_integrity_check.py`.
- Run `python tests/web_regression_check.py`.
- Run `python -m py_compile anonymous_bot/web_app.py` when the server changes.
- Manually verify each added button while signed in with the appropriate role.

## Launch requirement

`START_EVERYTHING.bat` is the only launcher. It uses a project `.venv` first, then a validated Python 3.11+ installation. Do not add duplicate launch scripts. A handoff recipient must install Python 3.11+ (or create the project `.venv`) before running it.
