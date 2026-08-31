from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / 'Anonymous_BotV2' / 'index.html'
WEB = ROOT / 'anonymous_bot' / 'web_app.py'
CONFIG = ROOT / 'anonymous_bot' / 'config.py'

html = HTML.read_text(encoding='utf-8')
web = WEB.read_text(encoding='utf-8')
config = CONFIG.read_text(encoding='utf-8')

# 1. Every frontend API path must exist in the backend.
front = set(re.findall(r'api\("([^"]+)', html))
routes = set(re.findall(r'parsed\.path\s*==\s*"([^"]+)', web))
missing = sorted(x.split('?',1)[0] for x in front if x.startswith('/api/') and x.split('?',1)[0] not in routes)
if missing:
    raise SystemExit('Missing backend routes: ' + ', '.join(missing))

# 2. Critical Discord channel IDs must not drift.
for key, expected in [('GAME_CHANNEL_ID', '1535189087282008118'), ('GENERAL_CHANNEL_ID', '1535189087282008114')]:
    if expected not in config:
        raise SystemExit(f'{key} expected Discord channel ID is missing')

# 3. GM quick panel belongs on Game, but private GM chat is not a player-facing
# channel in the site navigation.
if 'id="gameQaSession"' not in html or 'id="gameQaDanger"' not in html:
    raise SystemExit('GM quick controls disappeared from Game')
if 'data-page="gmchat"' in html or 'id="page-gmchat"' in html:
    raise SystemExit('Private GM chat is still exposed as a web channel')

# 4. Channel messages do not add a GM marker to their displayed author.
if '" [GM]"' in html:
    raise SystemExit('Channel message renderer still adds a GM marker')

# 5. General must use the dedicated social endpoint and the backend must support it.
if 'social("ooc")' not in html or 'if kind == "ooc":' not in web or '_live_general_history(200)' not in web:
    raise SystemExit('Discord General bridge route is missing')

# 6. Run JavaScript syntax check on the embedded browser script.
scripts = re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>', html, re.S)
if not scripts:
    raise SystemExit('Could not locate client script')
js = Path(tempfile.gettempdir()) / 'anonymous_web_client_check.js'
node = shutil.which('node') or os.environ.get('CODEX_NODE')
if node:
    for index, source in enumerate(scripts, 1):
        js.write_text(source, encoding='utf-8')
        proc = subprocess.run([node, '--check', str(js)], capture_output=True, text=True)
        if proc.returncode:
            raise SystemExit(f'Client JavaScript syntax error in script {index}:\n' + proc.stderr)


# 7. World-state defaults must not reference an undefined runtime variable.
default_fn = re.search(r'def _default_world_state\(\):\n(.*?)(?=\n\ndef _load_world_state)', web, re.S)
if not default_fn or 'state.get(' in default_fn.group(1):
    raise SystemExit('World-state default regression: _default_world_state references undefined state')
if 'def _surprise_danger_loop' not in web or 'surprise_danger' not in web:
    raise SystemExit('Hidden surprise-danger system disappeared')

# 8. GM quick-access controls must remain on the Game page.
for control in ['gameQaSession','gameQaLock','gameQaBroadcast','gameQaDanger','gameQaMove','gameQaCurrency','gameQaItem','gameQaNpc','gameQaSearch','gameQaSurprise']:
    if f'id="{control}"' not in html:
        raise SystemExit(f'GM Game quick control disappeared: {control}')

print('WEB REGRESSION CHECK: PASS')
print(f'Frontend API paths checked: {len(front)}')
print(f'Backend routes checked: {len(routes)}')
print('Game/General channel IDs: PASS')
print('GM quick panel and channel-label cleanup: PASS')
print('GM Chat isolation: PASS')
print('General bridge route: PASS')
print('Browser JavaScript syntax: PASS' if node else 'Browser JavaScript syntax: SKIPPED (Node.js unavailable)')
