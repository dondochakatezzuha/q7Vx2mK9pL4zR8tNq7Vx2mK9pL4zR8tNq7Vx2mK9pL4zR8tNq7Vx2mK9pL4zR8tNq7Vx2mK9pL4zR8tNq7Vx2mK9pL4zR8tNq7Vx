"""Action sound detection and generated starter SFX library."""
from __future__ import annotations

import hashlib
import math
import os
import random
import re
import struct
import wave
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

from .config import DATA_DIR

SFX_LIBRARY_DIR = Path(DATA_DIR) / "web_sfx"
SFX_SAMPLE_RATE = 44100
DEFAULT_PUBLIC_BASE_URL = "https://raw.githubusercontent.com/dondochakatezzuha/x7Qm2L9vK4x7Qm2L9vK4/main/sfx"


def sfx_public_base_url() -> str:
    return (os.getenv("SFX_PUBLIC_BASE_URL") or DEFAULT_PUBLIC_BASE_URL).strip().rstrip("/")


def sfx_stream_url(filename: str) -> str:
    parts = [part for part in Path(str(filename)).as_posix().split("/") if part not in {"", ".", ".."}]
    key = "/".join(parts)
    return urljoin(sfx_public_base_url() + "/", quote(key, safe="/"))

_FAMILIES: dict[str, tuple[str, ...]] = {
    "explosion": ("explosion", "explode", "explodes", "detonation", "blast", "erupts", "detonates", "bomb", "bombardment", "burst"),
    "laser": ("laser", "beam", "ray", "energy beam", "lance", "photon"),
    "time_stop": ("time stop", "time freeze", "freeze time", "stops time", "stop time", "time halted", "time halt", "frozen time"),
    "time_reverse": ("time reverse", "reverse time", "rewind time", "rewinds time", "time rewind", "rewind", "rollback"),
    "magic": ("magic", "spell", "sorcery", "arcane", "incantation", "mana", "enchantment", "curse", "ritual"),
    "fire": ("fire", "flame", "flames", "burn", "burning", "inferno", "fireball", "ember", "heat"),
    "ice": ("ice", "frost", "freeze", "frozen", "blizzard", "icicle", "snow"),
    "lightning": ("lightning", "thunder", "electric", "electricity", "bolt", "shock", "storm"),
    "sword": ("sword", "blade", "katana", "saber", "sabre", "rapier", "greatsword", "longsword", "dagger", "knife"),
    "axe": ("axe", "ax", "hatchet"), "hammer": ("hammer", "mace", "maul", "warhammer"),
    "bow": ("bow", "arrow", "archer", "crossbow", "bolt"), "gun": ("gun", "rifle", "pistol", "shotgun", "firearm", "bullet", "shoot", "shot"),
    "impact": ("hit", "hits", "strike", "strikes", "impact", "slam", "smash", "crash", "collide", "collision", "crush"),
    "whoosh": ("swing", "swings", "slashes", "slash", "swoop", "dash", "dashes", "rush", "rushing", "whips", "lunge", "leap"),
    "teleport": ("teleport", "teleports", "teleported", "blink", "blinks", "vanish", "appears behind", "warps"),
    "shield": ("block", "blocks", "blocked", "shield", "barrier", "guard", "guards"),
    "parry": ("parry", "parries", "deflect", "deflects", "counter", "counterattack"),
    "ground": ("ground", "floor", "earth", "terrain", "stone", "wall", "rock", "fortress"),
    "rubble": ("rubble", "debris", "crumbles", "collapse", "collapses", "shatter", "shatters", "cracks", "crack", "falling debris"),
    "critical": ("critical", "devastating", "fatal", "deadly", "decisive", "massive", "catastrophic", "overwhelming"),
    "heal": ("heal", "healing", "recover", "recovery", "regenerate", "regeneration"),
    "footstep": ("step", "steps", "footstep", "footsteps", "walk", "walking", "run", "running"),
    "door": ("door", "gate", "opens", "open", "closes", "close", "slam shut"),
}

_STARTER: dict[str, dict[str, Any]] = {
    "sword_slash": {"tags": ["sword", "slash", "whoosh", "melee"], "duration": .42, "kind": "whoosh"},
    "sword_heavy": {"tags": ["sword", "heavy", "impact", "melee"], "duration": .62, "kind": "impact"},
    "blade_clash": {"tags": ["sword", "parry", "metal", "impact"], "duration": .50, "kind": "clash"},
    "heavy_impact": {"tags": ["impact", "heavy"], "duration": .72, "kind": "impact"},
    "explosion_small": {"tags": ["explosion", "blast", "fire", "impact"], "duration": .90, "kind": "explosion"},
    "explosion_large": {"tags": ["explosion", "blast", "fire", "impact", "large"], "duration": 1.60, "kind": "explosion"},
    "explosion_catastrophic": {"tags": ["explosion", "blast", "impact", "critical", "large"], "duration": 2.20, "kind": "explosion"},
    "laser_fire": {"tags": ["laser", "energy", "ranged"], "duration": .65, "kind": "laser"},
    "laser_heavy": {"tags": ["laser", "energy", "impact", "heavy"], "duration": 1.00, "kind": "laser"},
    "laser_charge": {"tags": ["laser", "energy", "charge"], "duration": .80, "kind": "charge"},
    "magic_cast": {"tags": ["magic", "spell", "energy"], "duration": .90, "kind": "magic"},
    "magic_burst": {"tags": ["magic", "spell", "impact", "burst"], "duration": 1.05, "kind": "magic"},
    "magic_charged": {"tags": ["magic", "spell", "charge", "energy"], "duration": 1.30, "kind": "magic_charge"},
    "fire_burst": {"tags": ["fire", "magic", "burst"], "duration": .90, "kind": "fire"},
    "ice_shatter": {"tags": ["ice", "impact", "shatter"], "duration": .70, "kind": "ice"},
    "lightning_strike": {"tags": ["lightning", "impact", "electric"], "duration": .75, "kind": "lightning"},
    "dash_whoosh": {"tags": ["whoosh", "movement", "dash"], "duration": .45, "kind": "whoosh"},
    "teleport": {"tags": ["teleport", "magic", "whoosh"], "duration": .75, "kind": "teleport"},
    "shield_block": {"tags": ["shield", "block", "impact"], "duration": .55, "kind": "clash"},
    "parry": {"tags": ["parry", "metal", "impact"], "duration": .40, "kind": "clash"},
    "ground_slam": {"tags": ["impact", "ground", "heavy"], "duration": 1.00, "kind": "impact"},
    "ground_crack": {"tags": ["ground", "rubble", "impact"], "duration": 1.15, "kind": "rubble"},
    "rubble_fall": {"tags": ["rubble", "debris", "ground"], "duration": 1.60, "kind": "rubble"},
    "wall_break": {"tags": ["rubble", "impact", "stone"], "duration": 1.25, "kind": "rubble"},
    "time_stop": {"tags": ["time_stop", "magic", "energy"], "duration": 1.25, "kind": "time_stop"},
    "time_reverse": {"tags": ["time_reverse", "magic", "rewind"], "duration": 1.35, "kind": "time_reverse"},
    "time_stop_release": {"tags": ["time_stop", "impact", "release"], "duration": .75, "kind": "time_release"},
    "heal": {"tags": ["heal", "magic", "energy"], "duration": 1.00, "kind": "heal"},
    "charge_up": {"tags": ["charge", "energy", "magic"], "duration": 1.20, "kind": "charge"},
    "energy_burst": {"tags": ["energy", "impact", "burst"], "duration": .80, "kind": "energy"},
    "weapon_draw": {"tags": ["sword", "metal", "draw"], "duration": .35, "kind": "draw"},
    "gun_shot": {"tags": ["gun", "ranged", "impact"], "duration": .28, "kind": "gun"},
    "footstep": {"tags": ["footstep", "movement"], "duration": .24, "kind": "footstep"},
    "heavy_footstep": {"tags": ["footstep", "ground", "heavy"], "duration": .40, "kind": "heavy_footstep"},
    "door_slam": {"tags": ["door", "impact", "heavy"], "duration": .55, "kind": "door"},
    "whoosh_heavy": {"tags": ["whoosh", "heavy", "movement"], "duration": .70, "kind": "whoosh_heavy"},
    "shockwave": {"tags": ["impact", "energy", "whoosh", "heavy"], "duration": 1.10, "kind": "shockwave"},
}

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", str(text or "").casefold())

def detect_event(text: str, *, structured: dict[str, Any] | None = None) -> dict[str, Any] | None:
    structured = structured or {}; prose = str(text or "").casefold(); tokens = set(_tokenize(prose))
    scores = {family: 0.0 for family in _FAMILIES}
    for family, terms in _FAMILIES.items():
        for term in terms:
            if " " in term:
                if term in prose: scores[family] += 2.5
            elif term in tokens: scores[family] += 1.0
    for field in ("event", "action", "weapon", "ability", "element", "impact", "effect"):
        value = str(structured.get(field) or "").casefold()
        for family, terms in _FAMILIES.items():
            if value and any(term == value or term in value for term in terms): scores[family] += 5.0
    intensity = .5
    if any(w in tokens for w in ("tiny", "small", "minor", "light")): intensity = .25
    if any(w in tokens for w in ("heavy", "large", "huge", "massive", "gigantic", "catastrophic", "devastating")): intensity = .9
    if "critical" in tokens or "catastrophic" in tokens: intensity = max(intensity, 1.0)
    family, score = max(scores.items(), key=lambda pair: pair[1])
    if score < 2.0: return None
    if family in {"sword", "axe", "hammer", "bow", "gun"} and scores["impact"] >= 2: event = "weapon_impact"
    elif family in {"sword", "axe", "hammer", "bow", "gun"} and scores["whoosh"] >= 1: event = "weapon_swing"
    elif family == "fire" and scores["magic"] >= 2: event = "magic_fire"
    else: event = family
    return {"event": event, "family": family, "confidence": min(1.0, score / 8.0), "intensity": intensity, "tags": [family] + [n for n, v in scores.items() if v >= 2 and n != family]}

def choose_sfx(event: dict[str, Any]) -> dict[str, Any] | None:
    tags = set(event.get("tags") or []); intensity = float(event.get("intensity") or .5); ranked = []
    for name, item in _STARTER.items():
        overlap = len(tags & set(item["tags"]))
        if not overlap: continue
        score = overlap * 3.0
        if intensity >= .85 and any(tag in item["tags"] for tag in ("heavy", "large")): score += 2
        if event.get("family") == "explosion" and "explosion" in name: score += 4
        if event.get("family") == "laser" and "laser" in name: score += 4
        if event.get("family") == "time_stop" and "time_stop" in name: score += 5
        if event.get("family") == "time_reverse" and "time_reverse" in name: score += 5
        if event.get("family") == "rubble" and ("rubble" in name or "wall" in name): score += 4
        ranked.append((score, name, item))
    if not ranked: return None
    ranked.sort(reverse=True, key=lambda row: row[0]); _, name, item = ranked[0]
    return {"id": name, **item, "url": sfx_stream_url(f"{name}.wav")}

def _write_wav(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(SFX_SAMPLE_RATE)
        frames = bytearray()
        for sample in samples: frames += struct.pack("<h", int(max(-1, min(1, sample)) * 32767))
        out.writeframes(frames)

def _env(t: float, duration: float, attack=.005, decay=.25) -> float:
    if t < attack: return t / max(attack, 1e-6)
    return max(0.0, math.exp(-(t - attack) / max(decay, 1e-6)))

def _noise(rng: random.Random, duration: float, scale=1.0) -> list[float]:
    return [rng.uniform(-1, 1) * scale for _ in range(int(duration * SFX_SAMPLE_RATE))]

def _synth(kind: str, duration: float, seed: int) -> list[float]:
    rng = random.Random(seed); n = int(duration * SFX_SAMPLE_RATE); result = []; noise = _noise(rng, duration)
    for i in range(n):
        t = i / SFX_SAMPLE_RATE; e = _env(t, duration, decay=max(.08, duration * .32))
        if kind == "whoosh": f = 180 + 1800 * min(1, t / duration); value = math.sin(2*math.pi*f*t)*.55 + noise[i]*.28
        elif kind == "whoosh_heavy": f = 90 + 900 * min(1, t/duration); value = math.sin(2*math.pi*f*t)*.65 + noise[i]*.40
        elif kind == "impact": value = math.sin(2*math.pi*(65+35*math.exp(-t*8))*t)*.75 + noise[i]*.38
        elif kind == "explosion": value = math.sin(2*math.pi*(42+70*math.exp(-t*5))*t)*.7 + noise[i]*.65
        elif kind == "laser": f = 700 + 2800*(1-t/duration); value = math.sin(2*math.pi*f*t)*.5 + math.sin(2*math.pi*f*.47*t)*.18
        elif kind == "charge": f = 140 + 1600*(t/duration)**2; value = math.sin(2*math.pi*f*t)*.45 + math.sin(2*math.pi*f*.5*t)*.2
        elif kind == "magic": f = 260 + 420*math.sin(t*8); value = math.sin(2*math.pi*f*t)*.45 + noise[i]*.16
        elif kind == "magic_charge": f = 180 + 900*(t/duration); value = math.sin(2*math.pi*f*t)*.42 + math.sin(2*math.pi*f*1.5*t)*.18
        elif kind == "fire": value = noise[i]*.5 + math.sin(2*math.pi*(90+35*math.sin(t*25))*t)*.35
        elif kind == "ice": f = 900 + 1800*math.exp(-t*5); value = math.sin(2*math.pi*f*t)*.35 + noise[i]*.4
        elif kind == "lightning": value = noise[i]*(1 if t<.12 else .15) + math.sin(2*math.pi*55*t)*.5
        elif kind == "teleport": f = 1200-900*t/duration; value = math.sin(2*math.pi*f*t)*.5 + noise[i]*.12
        elif kind == "clash": value = noise[i]*.7 + math.sin(2*math.pi*1900*t)*.28
        elif kind == "rubble": value = noise[i]*(.85*math.exp(-t*1.7)) + math.sin(2*math.pi*(70+25*math.sin(t*13))*t)*.3
        elif kind == "time_stop": f = 1100 - 850*(t/duration); value = math.sin(2*math.pi*f*t)*.48 + math.sin(2*math.pi*55*t)*.18
        elif kind == "time_reverse": f = 250 + 1500*(1-t/duration); value = math.sin(2*math.pi*f*t)*.4 + math.sin(2*math.pi*f*.5*t)*.2
        elif kind == "time_release": value = math.sin(2*math.pi*(120+1800*t/duration)*t)*.45 + noise[i]*.25
        elif kind == "heal": f = 400 + 700*t/duration; value = math.sin(2*math.pi*f*t)*.35 + math.sin(2*math.pi*f*2*t)*.12
        elif kind == "energy": value = math.sin(2*math.pi*(180+1300*t/duration)*t)*.5 + noise[i]*.15
        elif kind == "draw": value = noise[i]*.5 + math.sin(2*math.pi*(1000+1500*t/duration)*t)*.25
        elif kind == "gun": value = noise[i]*.9 + math.sin(2*math.pi*120*t)*.65
        elif kind == "footstep": value = noise[i]*.7 + math.sin(2*math.pi*85*t)*.25
        elif kind == "heavy_footstep": value = noise[i]*.85 + math.sin(2*math.pi*52*t)*.55
        elif kind == "door": value = noise[i]*.7 + math.sin(2*math.pi*75*t)*.55
        elif kind == "shockwave": value = math.sin(2*math.pi*(45+600*t/duration)*t)*.55 + noise[i]*.3
        else: value = noise[i]*.25
        result.append(value*e)
    return result

def ensure_starter_library() -> list[dict[str, Any]]:
    SFX_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    for name, item in _STARTER.items():
        path = SFX_LIBRARY_DIR / f"{name}.wav"
        if not path.exists():
            seed = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
            _write_wav(path, _synth(item["kind"], float(item["duration"]), seed))
    return [{"id": name, "name": name.replace("_", " ").title(), "filename": f"{name}.wav", "url": sfx_stream_url(f"{name}.wav"), "tags": item["tags"], "source": "generated-starter"} for name, item in _STARTER.items()]

def build_sfx_event(text: str, *, structured: dict[str, Any] | None = None) -> dict[str, Any] | None:
    event = detect_event(text, structured=structured)
    if not event or event["confidence"] < .35: return None
    asset = choose_sfx(event)
    if not asset: return None
    return {**event, "asset": asset}
