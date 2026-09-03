"""Action sound detection and starter SFX library for Regnum of Regalia.

The detector intentionally uses weighted semantic families rather than requiring
an exhaustive keyword list. A future AI/event pipeline can pass structured
fields directly through ``detect_event`` and get the same normalized result.

The starter sounds are generated locally with Python's standard library so the
repository does not need to store binary audio files. ``ensure_starter_library``
creates short WAV assets on first startup/use.
"""
from __future__ import annotations

import hashlib
import math
import random
import re
import struct
import wave
from pathlib import Path
from typing import Any

from .config import DATA_DIR

SFX_LIBRARY_DIR = Path(DATA_DIR) / "web_sfx"
SFX_SAMPLE_RATE = 44100

# Semantic families. These are intentionally broad: exact wording should not
# be required when the event is obvious from context.
_FAMILIES: dict[str, tuple[str, ...]] = {
    "explosion": ("explosion", "explode", "explodes", "detonation", "blast", "erupts", "detonates", "bomb", "bombardment", "burst"),
    "laser": ("laser", "beam", "ray", "energy beam", "lance", "photon"),
    "magic": ("magic", "spell", "sorcery", "arcane", "incantation", "mana", "enchantment", "curse", "ritual"),
    "fire": ("fire", "flame", "flames", "burn", "burning", "inferno", "fireball", "ember", "heat"),
    "lightning": ("lightning", "thunder", "electric", "electricity", "bolt", "shock", "storm"),
    "sword": ("sword", "blade", "katana", "saber", "sabre", "rapier", "greatsword", "longsword", "dagger", "knife"),
    "axe": ("axe", "ax", "hatchet"),
    "hammer": ("hammer", "mace", "maul", "warhammer"),
    "bow": ("bow", "arrow", "archer", "crossbow", "bolt"),
    "impact": ("hit", "hits", "strike", "strikes", "impact", "slam", "smash", "crash", "collide", "collision", "crush"),
    "whoosh": ("swing", "swings", "slashes", "slash", "swoop", "dash", "dashes", "rush", "rushing", "whips"),
    "teleport": ("teleport", "teleports", "teleported", "blink", "blinks", "vanish", "appears behind", "warps"),
    "shield": ("block", "blocks", "blocked", "shield", "barrier", "guard", "guards"),
    "parry": ("parry", "parries", "deflect", "deflects", "counter", "counterattack"),
    "ground": ("ground", "floor", "earth", "terrain", "stone", "wall", "rock", "fortress"),
    "critical": ("critical", "devastating", "fatal", "deadly", "decisive", "massive", "catastrophic", "overwhelming"),
}

_STARTER: dict[str, dict[str, Any]] = {
    "sword_slash": {"tags": ["sword", "slash", "whoosh", "melee"], "duration": 0.42, "kind": "whoosh"},
    "sword_heavy": {"tags": ["sword", "heavy", "impact", "melee"], "duration": 0.62, "kind": "impact"},
    "blade_clash": {"tags": ["sword", "parry", "metal", "impact"], "duration": 0.50, "kind": "clash"},
    "heavy_impact": {"tags": ["impact", "heavy"], "duration": 0.72, "kind": "impact"},
    "explosion_small": {"tags": ["explosion", "blast", "fire", "impact"], "duration": 0.90, "kind": "explosion"},
    "explosion_large": {"tags": ["explosion", "blast", "fire", "impact", "large"], "duration": 1.60, "kind": "explosion"},
    "explosion_catastrophic": {"tags": ["explosion", "blast", "impact", "critical", "large"], "duration": 2.20, "kind": "explosion"},
    "laser_fire": {"tags": ["laser", "energy", "ranged"], "duration": 0.65, "kind": "laser"},
    "laser_heavy": {"tags": ["laser", "energy", "impact", "heavy"], "duration": 1.00, "kind": "laser"},
    "magic_cast": {"tags": ["magic", "spell", "energy"], "duration": 0.90, "kind": "magic"},
    "magic_burst": {"tags": ["magic", "spell", "impact", "burst"], "duration": 1.05, "kind": "magic"},
    "fire_burst": {"tags": ["fire", "magic", "burst"], "duration": 0.90, "kind": "fire"},
    "lightning_strike": {"tags": ["lightning", "impact", "electric"], "duration": 0.75, "kind": "lightning"},
    "dash_whoosh": {"tags": ["whoosh", "movement", "dash"], "duration": 0.45, "kind": "whoosh"},
    "teleport": {"tags": ["teleport", "magic", "whoosh"], "duration": 0.75, "kind": "teleport"},
    "shield_block": {"tags": ["shield", "block", "impact"], "duration": 0.55, "kind": "clash"},
    "parry": {"tags": ["parry", "metal", "impact"], "duration": 0.40, "kind": "clash"},
    "ground_slam": {"tags": ["impact", "ground", "heavy"], "duration": 1.00, "kind": "impact"},
}


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").casefold())


def detect_event(text: str, *, structured: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Infer an audio event from prose plus optional structured game data.

    Returns ``None`` for low-confidence text. Structured fields get priority,
    while prose contributes supporting evidence and modifiers.
    """
    structured = structured or {}
    prose = str(text or "").casefold()
    tokens = set(_tokenize(prose))
    scores: dict[str, float] = {family: 0.0 for family in _FAMILIES}

    for family, terms in _FAMILIES.items():
        for term in terms:
            if " " in term:
                if term in prose:
                    scores[family] += 2.5
            elif term in tokens:
                scores[family] += 1.0

    # Structured event fields are much stronger than individual words.
    for field in ("event", "action", "weapon", "ability", "element", "impact", "effect"):
        value = str(structured.get(field) or "").casefold()
        if not value:
            continue
        for family, terms in _FAMILIES.items():
            if any(term == value or term in value for term in terms):
                scores[family] += 5.0

    # Context modifiers.
    intensity = 0.5
    if any(word in tokens for word in ("tiny", "small", "minor", "light")):
        intensity = 0.25
    if any(word in tokens for word in ("heavy", "large", "huge", "massive", "gigantic", "catastrophic", "devastating")):
        intensity = 0.9
    if "critical" in tokens or "catastrophic" in tokens:
        intensity = max(intensity, 1.0)

    family, score = max(scores.items(), key=lambda pair: pair[1])
    if score < 2.0:
        return None

    if family in {"sword", "axe", "hammer", "bow"} and scores["impact"] >= 2:
        event = "weapon_impact"
    elif family in {"sword", "axe", "hammer", "bow"} and scores["whoosh"] >= 1:
        event = "weapon_swing"
    elif family == "fire" and scores["magic"] >= 2:
        event = "magic_fire"
    else:
        event = family

    return {
        "event": event,
        "family": family,
        "confidence": min(1.0, score / 8.0),
        "intensity": intensity,
        "tags": [family] + [name for name, value in scores.items() if value >= 2 and name != family],
    }


def choose_sfx(event: dict[str, Any]) -> dict[str, Any] | None:
    """Choose the closest starter asset by weighted tag overlap."""
    tags = set(event.get("tags") or [])
    intensity = float(event.get("intensity") or 0.5)
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for name, item in _STARTER.items():
        overlap = len(tags & set(item["tags"]))
        if not overlap:
            continue
        score = overlap * 3.0
        if intensity >= 0.85 and any(tag in item["tags"] for tag in ("heavy", "large")):
            score += 2.0
        if event.get("family") == "explosion" and "explosion" in name:
            score += 4.0
        if event.get("family") == "laser" and "laser" in name:
            score += 4.0
        ranked.append((score, name, item))
    if not ranked:
        return None
    ranked.sort(reverse=True, key=lambda row: row[0])
    _, name, item = ranked[0]
    return {"id": name, **item, "url": f"/media/sfx/{name}.wav"}


def _write_wav(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SFX_SAMPLE_RATE)
        frames = bytearray()
        for sample in samples:
            sample = max(-1.0, min(1.0, sample))
            frames += struct.pack("<h", int(sample * 32767))
        out.writeframes(frames)


def _env(t: float, duration: float, attack: float = 0.005, decay: float = 0.25) -> float:
    if t < attack:
        return t / max(attack, 1e-6)
    return max(0.0, math.exp(-(t - attack) / max(decay, 1e-6)))


def _noise(rng: random.Random, duration: float, scale: float = 1.0) -> list[float]:
    n = int(duration * SFX_SAMPLE_RATE)
    return [rng.uniform(-1, 1) * scale for _ in range(n)]


def _synth(kind: str, duration: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    n = int(duration * SFX_SAMPLE_RATE)
    result: list[float] = []
    noise = _noise(rng, duration, 1.0)
    for i in range(n):
        t = i / SFX_SAMPLE_RATE
        e = _env(t, duration, decay=max(0.08, duration * 0.32))
        if kind == "whoosh":
            f = 180 + 1800 * min(1, t / duration)
            value = math.sin(2 * math.pi * f * t) * 0.55 + noise[i] * 0.28
        elif kind == "impact":
            f = 65 + 35 * math.exp(-t * 8)
            value = math.sin(2 * math.pi * f * t) * 0.75 + noise[i] * 0.38
        elif kind == "explosion":
            f = 42 + 70 * math.exp(-t * 5)
            value = math.sin(2 * math.pi * f * t) * 0.7 + noise[i] * 0.65
        elif kind == "laser":
            f = 700 + 2800 * (1 - t / duration)
            value = math.sin(2 * math.pi * f * t) * 0.5 + math.sin(2 * math.pi * (f * 0.47) * t) * 0.18
        elif kind == "magic":
            f = 260 + 420 * math.sin(t * 8)
            value = math.sin(2 * math.pi * f * t) * 0.45 + noise[i] * 0.16
        elif kind == "fire":
            value = noise[i] * 0.5 + math.sin(2 * math.pi * (90 + 35 * math.sin(t * 25)) * t) * 0.35
        elif kind == "lightning":
            crack = noise[i] * (1.0 if t < 0.12 else 0.15)
            value = crack + math.sin(2 * math.pi * 55 * t) * 0.5
        elif kind == "teleport":
            f = 1200 - 900 * (t / duration)
            value = math.sin(2 * math.pi * f * t) * 0.5 + noise[i] * 0.12
        elif kind == "clash":
            value = noise[i] * 0.7 + math.sin(2 * math.pi * 1900 * t) * 0.28
        else:
            value = noise[i] * 0.25
        result.append(value * e)
    return result


def ensure_starter_library() -> list[dict[str, Any]]:
    """Generate the starter WAV pack if it is missing and return its catalog."""
    SFX_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    for name, item in _STARTER.items():
        path = SFX_LIBRARY_DIR / f"{name}.wav"
        if not path.exists():
            seed = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
            _write_wav(path, _synth(item["kind"], float(item["duration"]), seed))
    return [
        {"id": name, "name": name.replace("_", " ").title(), "filename": f"{name}.wav", "url": f"/media/sfx/{name}.wav", "tags": item["tags"], "source": "generated-starter"}
        for name, item in _STARTER.items()
    ]


def build_sfx_event(text: str, *, structured: dict[str, Any] | None = None) -> dict[str, Any] | None:
    event = detect_event(text, structured=structured)
    if not event or event["confidence"] < 0.35:
        return None
    asset = choose_sfx(event)
    if not asset:
        return None
    return {**event, "asset": asset}
