import os
from pathlib import Path

try:
  from dotenv import load_dotenv
  # Load .env from the bot package, project root, and current working directory.
  # This prevents API keys from appearing as "not configured" when the bot is
  # launched from the parent project folder.
  _here = Path(__file__).resolve().parent
  _project_root = _here.parent
  for _env_path in (_here / ".env", _project_root / ".env", Path.cwd() / ".env"):
    if _env_path.exists():
      load_dotenv(_env_path, override=False)
except ImportError:
  pass

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
  raise RuntimeError("DISCORD_TOKEN environment variable is not set. Put it in anonymous_bot/.env or set it in Windows Environment Variables.")

GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
CEREBRAS_API_KEY = (os.getenv("CEREBRAS_API_KEY") or "").strip()
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")
MISTRAL_API_KEY = (os.getenv("MISTRAL_API_KEY") or "").strip()
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
SAMBANOVA_API_KEY = (os.getenv("SAMBANOVA_API_KEY") or "").strip()
SAMBANOVA_MODEL = os.getenv("SAMBANOVA_MODEL", "Meta-Llama-3.3-70B-Instruct")
OPENROUTER_API_KEY = (os.getenv("OPENROUTER_API_KEY") or "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
GITHUB_TOKEN = (os.getenv("GITHUB_TOKEN") or "").strip()
GITHUB_MODEL = os.getenv("GITHUB_MODEL", "openai/gpt-4.1-mini")
NVIDIA_API_KEY = (os.getenv("NVIDIA_API_KEY") or "").strip()
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1")
HUGGINGFACE_API_KEY = (os.getenv("HUGGINGFACE_API_KEY") or "").strip()
HUGGINGFACE_MODEL = os.getenv("HUGGINGFACE_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
CHUTES_API_KEY = (os.getenv("CHUTES_API_KEY") or "").strip()
CHUTES_MODEL = os.getenv("CHUTES_MODEL", "Qwen/Qwen3-32B-TEE")
POLLINATIONS_API_KEY = (os.getenv("POLLINATIONS_API_KEY") or "").strip()
POLLINATIONS_MODEL = os.getenv("POLLINATIONS_MODEL", "openai")
LLM7_API_KEY = (os.getenv("LLM7_API_KEY") or "").strip()
LLM7_MODEL = os.getenv("LLM7_MODEL", "default")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b").strip()
AI_PROVIDER = os.getenv("AI_PROVIDER", "auto").strip().lower()
DATA_DIR = os.getenv("DATA_DIR", str((_project_root / "campaign_data").resolve()))
BOT_OWNER_USER_ID = 1388446131620548760
# The only regular player allowed to directly use the AI is Kaizen (the bot builder/tester).
# Guild administrators and configured GMs/writers are also allowed.
AI_PLAYER_USER_IDS = {x.strip() for x in os.getenv("AI_PLAYER_USER_IDS", str(BOT_OWNER_USER_ID)).split(",") if x.strip()}
# Campaign GM / writer identities. Draven also uses the aliases Rivic and Rico.
# Civic is also an authorized GM/writer.
GM_USER_IDS = {
  "1311460994660306996",  # Draven / Rivic / Rico
  "1463100226318368874",  # Draven / Rivic / Rico
  "1538115083513761813",  # Draven / Rivic / Rico
  "1187513925739237408",  # Civic
  "1388446131620548760",  # Kaizen / bot builder / web GM tester
  "1541867048664301678",  # Campaign GM
}
WEB_TEST_GM_IDS = {x.strip() for x in os.getenv("WEB_TEST_GM_IDS", "1388446131620548760").split(",") if x.strip()}
GM_PRIMARY_USER_ID = "1311460994660306996"
# Historical server lore collection. The bot continuously archives new messages
# and can backfill existing channel history on startup.
SERVER_LORE_COLLECTION = os.getenv("SERVER_LORE_COLLECTION", "true").strip().lower() not in {"0", "false", "no", "off"}
SERVER_LORE_BACKFILL_LIMIT = None  # Historical lore backfill is intentionally unlimited.
SERVER_LORE_CHANNELS = os.getenv("SERVER_LORE_CHANNELS", "").strip()
GAME_GUILD_ID = 1535189086258855946
GAME_CHANNEL_ID = 1535189087282008118
GENERAL_CHANNEL_ID = int(os.getenv("GENERAL_CHANNEL_ID", "1535189087282008114"))
CAMPAIGN_NAME = os.getenv("CAMPAIGN_NAME", "Regnum of Regalia").strip() or "Regnum of Regalia"

# Web client / Discord OAuth2. The app can run locally or behind a generic HTTPS proxy.
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
ANONYMOUS_APP_URL = os.getenv("ANONYMOUS_APP_URL", "").strip().rstrip("/")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", f"{ANONYMOUS_APP_URL}/oauth/callback" if ANONYMOUS_APP_URL else "http://127.0.0.1:18474/oauth/callback")
WEB_SESSION_SECRET = os.getenv("ANONYMOUS_SESSION_SECRET", os.getenv("WEB_SESSION_SECRET", "")).strip() or ("local-" + os.urandom(32).hex())
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0").strip()
WEB_PORT = int(os.getenv("PORT", os.getenv("WEB_PORT", "18474")))
COOKIE_HTTPS_ONLY = os.getenv("COOKIE_HTTPS_ONLY", "false").strip().lower() not in {"0", "false", "no", "off"}
