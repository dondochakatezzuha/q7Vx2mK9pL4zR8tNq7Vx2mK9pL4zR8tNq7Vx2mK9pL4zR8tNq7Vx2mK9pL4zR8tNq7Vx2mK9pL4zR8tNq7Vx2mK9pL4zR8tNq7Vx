"""Static safety net for the web UI's visible controls and routes.

This catches the class of regression where a script binds an event handler to
an element that no longer exists, which previously stopped later page setup and
made unrelated buttons appear dead.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "Anonymous_BotV2" / "index.html").read_text(encoding="utf-8")
WEB = (ROOT / "anonymous_bot" / "web_app.py").read_text(encoding="utf-8")

# IDs are present either in static markup or in a dynamically-created modal.
known_ids = set(re.findall(r'''\bid=["']([A-Za-z0-9_-]+)''', HTML))
direct_refs = set(re.findall(r'''\$\(["']([A-Za-z0-9_-]+)["']\)\s*\.''', HTML))
missing = sorted(direct_refs - known_ids)
if missing:
    raise SystemExit("Client handlers reference missing element IDs: " + ", ".join(missing))

routes = set(re.findall(r'''parsed\.path\s*==\s*["']([^"']+)''', WEB))
required_routes = {
    "/api/channels", "/api/channel-messages", "/api/messages",
    "/api/social", "/api/social/settings", "/api/gm/action",
    "/api/gm/emergency", "/api/gm/players", "/api/diagnostics",
}
if missing_routes := sorted(required_routes - routes):
    raise SystemExit("Required UI routes are missing: " + ", ".join(missing_routes))

required_controls = {
    "channelSelect", "sendBtn", "everyoneBtn", "gmOnlyBtn", "povAudienceBtn",
    "gameQaSession", "qaSession", "endSession", "gmEmergencyPause",
    "accent", "animations", "portraits", "timestamps", "messageDensity", "saveSettings", "runDiagnostics",
}
if missing_controls := sorted(required_controls - known_ids):
    raise SystemExit("Required visible controls are missing: " + ", ".join(missing_controls))

for snippet in ("channel_id:String(activeChannelId", "visibility:privateMode?\"gm\":\"public\"", "audience_ids:gm()?povAudience"):
    if snippet not in HTML:
        raise SystemExit("Final message bridge regression: " + snippet)

print("UI INTEGRITY CHECK: PASS")
print(f"Element IDs checked: {len(known_ids)}")
print(f"Direct client references checked: {len(direct_refs)}")
print(f"Required routes checked: {len(required_routes)}")
