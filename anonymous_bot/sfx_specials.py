"""Generated special-action and character-vocal SFX.

These are original procedural effects, not recordings from copyrighted games/anime.
They fill the special IDs used by the layered action system.
"""
from __future__ import annotations

import hashlib
import math
import random
import struct
import wave
from pathlib import Path
from typing import Any

from .config import DATA_DIR

LIBRARY = Path(DATA_DIR) / "web_sfx"
RATE = 44100

SPECIALS: dict[str, tuple[float, str]] = {
    "cero_charge": (1.05, "cero_charge"),
    "cero_blast": (1.10, "cero_blast"),
    "vocal_pain_male": (.48, "vocal_male_low"),
    "vocal_pain_female": (.42, "vocal_female_high"),
    "vocal_scream_male": (.90, "vocal_male_scream"),
    "vocal_scream_female": (.85, "vocal_female_scream"),
    "vocal_battle_cry_male": (.72, "vocal_male_cry"),
    "vocal_battle_cry_female": (.68, "vocal_female_cry"),
    "vocal_death_male": (.82, "vocal_male_scream"),
    "vocal_death_female": (.78, "vocal_female_scream"),
    "vocal_effort_male": (.38, "vocal_male_low"),
    "vocal_effort_female": (.34, "vocal_female_high"),
    "vocal_gasp_male": (.30, "vocal_male_gasp"),
    "vocal_gasp_female": (.28, "vocal_female_gasp"),
    "vocal_berserker_roar": (1.15, "vocal_berserker"),
    "vocal_monster_roar": (1.30, "vocal_monster"),
}


def _tone(freq: float, t: float) -> float:
    return math.sin(2 * math.pi * freq * t)


def _noise(rng: random.Random) -> float:
    return rng.uniform(-1.0, 1.0)


def _synth(kind: str, duration: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    n = max(1, int(duration * RATE))
    out: list[float] = []
    for i in range(n):
        t = i / RATE
        p = t / max(duration, 1e-6)
        env = min(1.0, t / .025) * math.exp(-t / max(duration * .72, .08))
        if kind == "cero_charge":
            value = .38*_tone(85 + 1150*p*p, t) + .20*_tone(170 + 2100*p, t) + .12*_noise(rng)
        elif kind == "cero_blast":
            value = .62*_tone(48 + 520*(1-p), t) + .34*_tone(1200 + 1900*(1-p), t) + .34*_noise(rng)
        elif kind == "vocal_male_low":
            f = 105 + 18*math.sin(t*8); value = .55*_tone(f,t) + .24*_tone(f*2.01,t) + .13*_noise(rng)
        elif kind == "vocal_female_high":
            f = 185 + 30*math.sin(t*9); value = .52*_tone(f,t) + .20*_tone(f*2.03,t) + .11*_noise(rng)
        elif kind == "vocal_male_scream":
            f = 125 + 95*p; value = .52*_tone(f,t) + .30*_tone(f*2.02,t) + .20*_noise(rng)
        elif kind == "vocal_female_scream":
            f = 220 + 150*p; value = .48*_tone(f,t) + .28*_tone(f*2.01,t) + .18*_noise(rng)
        elif kind == "vocal_male_cry":
            f = 115 + 65*p; value = .58*_tone(f,t) + .22*_tone(f*2,t) + .12*_noise(rng)
        elif kind == "vocal_female_cry":
            f = 205 + 90*p; value = .56*_tone(f,t) + .20*_tone(f*2,t) + .12*_noise(rng)
        elif kind == "vocal_male_gasp":
            value = .40*_noise(rng) + .22*_tone(95+80*p,t)
        elif kind == "vocal_female_gasp":
            value = .36*_noise(rng) + .22*_tone(180+100*p,t)
        elif kind == "vocal_berserker":
            f = 100 + 45*p; value = .58*_tone(f,t) + .30*_tone(f*2.02,t) + .30*_noise(rng)
        elif kind == "vocal_monster":
            f = 62 + 35*p; value = .62*_tone(f,t) + .32*_tone(f*2.01,t) + .38*_noise(rng)
        else:
            value = 0.0
        out.append(max(-1.0, min(1.0, value * env)))
    return out


def _write(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in samples))


def ensure_special_library() -> list[dict[str, Any]]:
    LIBRARY.mkdir(parents=True, exist_ok=True)
    assets: list[dict[str, Any]] = []
    for name, (duration, kind) in SPECIALS.items():
        path = LIBRARY / f"{name}.wav"
        if not path.exists():
            seed = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
            _write(path, _synth(kind, duration, seed))
        assets.append({"id": name, "url": f"/media/sfx/{name}.wav", "duration": duration, "source": "generated-special"})
    return assets
