# Anonymous RPG Bot

## Run it

Double-click `START_EVERYTHING.bat`. It installs missing Python packages, starts Ollama when available, starts the Discord bot, waits for the local website, and opens it.

## What to keep

- `anonymous_bot/` — bot and web-server code
- `Anonymous_BotV2/` — web interface
- `campaign_data/` — campaign database and world state
- `tests/` — regression checks
- `anonymous_bot/.env` — private local credentials; never share this file
- `anonymous_bot/.env.example` — configuration template for a new install
- `REGRESSION_POLICY.md` — feature-preservation policy
- `render.yaml` — optional Render deployment configuration

## Handing it to someone else

Give them the code and `.env.example`, not `anonymous_bot/.env` or `campaign_data/`. They should supply their own Discord credentials and campaign data.
