"""Expanded procedural SFX catalog.

All assets are generated locally at runtime. No copyrighted recordings are bundled.
The module deliberately favors many reusable variations over a small set of identical clips.
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

SFX: dict[str, tuple[list[str], float, str]] = {
    "punch_light": (["combat", "impact", "melee"], .28, "impact"),
    "punch_heavy": (["combat", "impact", "heavy"], .55, "heavy_impact"),
    "kick_heavy": (["combat", "impact", "kick", "heavy"], .48, "heavy_impact"),
    "body_hit": (["combat", "impact", "body"], .34, "impact"),
    "blade_scrape": (["sword", "metal", "scrape"], .65, "metal_scrape"),
    "axe_swing": (["axe", "whoosh", "weapon"], .55, "whoosh"),
    "axe_impact": (["axe", "impact", "heavy"], .62, "heavy_impact"),
    "hammer_swing": (["hammer", "whoosh", "weapon", "heavy"], .58, "heavy_whoosh"),
    "hammer_impact": (["hammer", "impact", "heavy"], .72, "heavy_impact"),
    "arrow_fire": (["bow", "arrow", "ranged"], .32, "whoosh"),
    "arrow_impact": (["arrow", "impact"], .38, "impact"),
    "gunshot_heavy": (["gun", "shot", "ranged", "heavy"], .38, "gun"),
    "bullet_impact": (["bullet", "impact", "metal"], .30, "impact"),
    "ricochet": (["bullet", "metal", "ricochet"], .55, "ricochet"),
    "glass_break": (["glass", "break", "destruction"], .85, "glass"),
    "wood_break": (["wood", "break", "destruction"], .90, "wood"),
    "metal_break": (["metal", "break", "destruction"], .80, "metal_break"),
    "wall_collapse": (["wall", "collapse", "rubble", "destruction"], 1.80, "collapse"),
    "debris_burst": (["debris", "rubble", "impact"], 1.20, "rubble"),
    "dust_burst": (["dust", "impact", "environment"], .95, "dust"),
    "shockwave_heavy": (["shockwave", "impact", "heavy"], 1.20, "shockwave"),
    "energy_charge_deep": (["energy", "charge", "magic"], 1.25, "energy_charge"),
    "energy_release_massive": (["energy", "release", "impact", "heavy"], 1.00, "energy_release"),
    "energy_beam_heavy": (["energy", "beam", "laser", "ranged"], 1.25, "beam"),
    "energy_beam_impact": (["energy", "beam", "impact", "heavy"], 1.05, "beam_impact"),
    "low_frequency_rumble": (["rumble", "pressure", "environment"], 1.50, "rumble"),
    "dash_fast": (["movement", "dash", "whoosh"], .42, "fast_whoosh"),
    "dash_extreme": (["movement", "dash", "whoosh", "heavy"], .62, "extreme_whoosh"),
    "air_rush": (["movement", "wind", "whoosh"], .70, "wind"),
    "landing_light": (["movement", "landing", "impact"], .35, "impact"),
    "landing_heavy": (["movement", "landing", "impact", "heavy"], .62, "heavy_impact"),
    "slide": (["movement", "slide"], .75, "slide"),
    "helicopter_idle": (["vehicle", "helicopter", "rotor", "engine"], 2.20, "helicopter"),
    "helicopter_pass": (["vehicle", "helicopter", "rotor", "flyover"], 2.00, "helicopter_pass"),
    "helicopter_attack": (["vehicle", "helicopter", "rotor", "weapon"], 1.70, "helicopter_attack"),
    "helicopter_takeoff": (["vehicle", "helicopter", "rotor", "engine"], 1.80, "helicopter_takeoff"),
    "helicopter_crash": (["vehicle", "helicopter", "crash", "explosion"], 2.30, "vehicle_crash"),
    "car_engine": (["vehicle", "car", "engine"], 1.80, "engine"),
    "car_pass": (["vehicle", "car", "movement"], 1.20, "vehicle_pass"),
    "tire_screech": (["vehicle", "tire", "skid"], .90, "screech"),
    "car_crash": (["vehicle", "car", "crash", "impact"], 1.30, "vehicle_crash"),
    "vehicle_horn": (["vehicle", "horn"], .65, "horn"),
    "door_open": (["door", "everyday"], .45, "door_open"),
    "door_close": (["door", "everyday"], .45, "door_close"),
    "door_knock": (["door", "everyday"], .50, "knock"),
    "metal_clang": (["metal", "impact", "everyday"], .48, "metal_clang"),
    "chain_rattle": (["chain", "metal", "everyday"], .85, "chain"),
    "switch_click": (["switch", "click", "everyday"], .12, "click"),
    "electronic_beep": (["electronic", "beep", "everyday"], .25, "beep"),
    "alarm": (["alarm", "electronic", "emergency"], 1.30, "alarm"),
    "siren": (["siren", "emergency", "vehicle"], 1.70, "siren"),
    "footstep_soft": (["footstep", "movement", "everyday"], .22, "footstep"),
    "footstep_stone": (["footstep", "stone", "movement"], .24, "footstep_stone"),
    "footstep_metal": (["footstep", "metal", "movement"], .25, "footstep_metal"),
    "running_steps": (["footstep", "running", "movement"], .80, "running"),
    "heavy_running": (["footstep", "running", "heavy", "movement"], .85, "heavy_running"),
    "rain": (["rain", "weather", "environment"], 2.50, "rain"),
    "wind": (["wind", "weather", "environment"], 2.20, "wind"),
    "thunder": (["thunder", "weather", "lightning"], 1.80, "thunder"),
    "fire_crackle": (["fire", "environment"], 2.00, "fire_crackle"),
    "water_flow": (["water", "environment"], 2.20, "water"),
    "glass_tink": (["glass", "everyday"], .35, "glass_tink"),
    "cinematic_riser": (["cinematic", "riser", "tension"], 2.00, "riser"),
    "cinematic_stinger": (["cinematic", "stinger", "impact"], .90, "stinger"),
    "bass_drop": (["cinematic", "bass", "impact"], 1.10, "bass_drop"),
    "reverse_whoosh": (["cinematic", "reverse", "transition"], .85, "reverse"),
}


def _noise(rng: random.Random) -> float:
    return rng.uniform(-1.0, 1.0)


def _tone(freq: float, t: float) -> float:
    return math.sin(2.0 * math.pi * freq * t)


def _env(t: float, d: float, attack: float = .01, decay: float = .30) -> float:
    if t < attack:
        return t / max(attack, 1e-6)
    return math.exp(-(t - attack) / max(decay, 1e-6))


def _synth(kind: str, duration: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    n = max(1, int(duration * RATE))
    out: list[float] = []
    for i in range(n):
        t = i / RATE
        p = t / max(duration, 1e-6)
        e = _env(t, duration, decay=max(.06, duration * .28))
        if kind == "impact": value = _tone(70 + 40 * math.exp(-t * 10), t) * .65 + _noise(rng) * .45
        elif kind == "heavy_impact": value = _tone(48 + 55 * math.exp(-t * 7), t) * .75 + _noise(rng) * .55
        elif kind in {"whoosh", "fast_whoosh"}: value = _tone(180 + 2200 * p, t) * .45 + _noise(rng) * .35
        elif kind in {"heavy_whoosh", "extreme_whoosh"}: value = _tone(90 + 1100 * p, t) * .58 + _noise(rng) * .42
        elif kind == "metal_scrape": value = _noise(rng) * .55 + _tone(1500 + 900 * math.sin(t * 9), t) * .25
        elif kind == "gun": value = _noise(rng) * .95 + _tone(115, t) * .65
        elif kind == "ricochet": value = _tone(2600 + 900 * p, t) * .48 + _noise(rng) * .16
        elif kind == "glass": value = _noise(rng) * .45 + _tone(2300 + 1800 * math.sin(t * 17), t) * .32
        elif kind == "wood": value = _noise(rng) * .72 + _tone(120 + 80 * math.exp(-t * 7), t) * .45
        elif kind == "metal_break": value = _noise(rng) * .75 + _tone(700 + 600 * math.exp(-t * 5), t) * .35
        elif kind in {"collapse", "rubble"}: value = _noise(rng) * .78 * math.exp(-t * 1.5) + _tone(65 + 25 * math.sin(t * 11), t) * .32
        elif kind == "dust": value = _noise(rng) * .55 * math.exp(-t * 2.2)
        elif kind == "shockwave": value = _tone(40 + 800 * p, t) * .62 + _noise(rng) * .28
        elif kind == "energy_charge": value = _tone(120 + 1700 * p * p, t) * .38 + _tone(240 + 2500 * p, t) * .20
        elif kind == "energy_release": value = _tone(180 + 1700 * (1-p), t) * .48 + _noise(rng) * .22
        elif kind == "beam": value = _tone(500 + 2600 * (1-p), t) * .46 + _tone(70, t) * .28 + _noise(rng) * .12
        elif kind == "beam_impact": value = _tone(60 + 100 * math.exp(-t*5), t) * .65 + _noise(rng) * .65
        elif kind == "rumble": value = _tone(35 + 18 * math.sin(t*5), t) * .72 + _noise(rng) * .18
        elif kind == "helicopter": value = .42*_tone(34,t) + .22*_tone(68,t) + .12*_tone(136,t) + .16*_tone(115,t) + .10*_noise(rng)
        elif kind == "helicopter_pass":
            f = 38 + 28*math.sin(p*math.pi); value = .48*_tone(f,t) + .20*_tone(f*2,t) + .12*_noise(rng)
        elif kind == "helicopter_attack": value = .35*_tone(38,t) + .18*_tone(76,t) + .35*_tone(75,t) + _noise(rng)*(.55 if (i%1600)<500 else .06)
        elif kind == "helicopter_takeoff":
            f = 28 + 32*p; value = .55*_tone(f,t) + .18*_tone(f*2,t) + .10*_noise(rng)
        elif kind in {"vehicle_crash", "car_crash"}: value = .70*_tone(52+75*math.exp(-t*4),t) + .75*_noise(rng)
        elif kind == "engine": value = .55*_tone(55+12*math.sin(t*4),t) + .18*_tone(110,t) + .10*_noise(rng)
        elif kind == "vehicle_pass": value = .25*_tone(80+170*p,t) + .15*_noise(rng)
        elif kind == "screech": value = .45*_tone(900+1700*p,t) + .32*_noise(rng)
        elif kind == "horn": value = .50*_tone(420,t) + .22*_tone(560,t)
        elif kind == "door_open": value = .22*_noise(rng) + .18*_tone(180+500*p,t)
        elif kind == "door_close": value = .45*_noise(rng) + .45*_tone(75,t)
        elif kind == "knock": value = .50*_tone(120,t) + .30*_noise(rng)
        elif kind == "metal_clang": value = .45*_noise(rng) + .35*_tone(1800,t)
        elif kind == "chain": value = .45*_noise(rng) + .18*_tone(700+300*math.sin(t*18),t)
        elif kind == "click": value = .65*_noise(rng) + .20*_tone(2200,t)
        elif kind == "beep": value = .50*_tone(900,t)
        elif kind == "alarm": value = .40*_tone(700+450*(int(t*4)%2),t)
        elif kind == "siren": value = .50*_tone(450+500*math.sin(t*5),t)
        elif kind in {"footstep", "footstep_stone", "footstep_metal"}: value = .62*_noise(rng) + .22*_tone(70 if kind=="footstep" else 90,t)
        elif kind in {"running", "heavy_running"}: value = (.58*_noise(rng) + .25*_tone(65 if kind=="running" else 50,t)) * (1.0 if int(t*7)%2==0 else .18)
        elif kind == "slide": value = .35*_noise(rng) + .18*_tone(180+500*p,t)
        elif kind == "rain": value = .28*_noise(rng) + .08*_tone(2600+900*math.sin(t*4),t)
        elif kind == "wind": value = .28*_noise(rng) + .20*_tone(90+120*p,t)
        elif kind == "thunder": value = .70*_tone(45+80*math.exp(-t*3),t) + .65*_noise(rng)
        elif kind == "fire_crackle": value = _noise(rng)*(.55 if rng.random()<.025 else .12) + .18*_tone(85+45*math.sin(t*12),t)
        elif kind == "water": value = .18*_noise(rng) + .12*_tone(420+160*math.sin(t*3),t)
        elif kind == "glass_tink": value = .38*_tone(2800+900*p,t) + .12*_noise(rng)
        elif kind == "riser": value = .38*_tone(80+900*p*p,t) + .08*_noise(rng)
        elif kind == "stinger": value = .40*_tone(1000+600*math.exp(-t*5),t) + .40*_tone(90,t) + .20*_noise(rng)
        elif kind == "bass_drop": value = .78*_tone(42+130*math.exp(-t*7),t) + .30*_noise(rng)
        elif kind == "reverse": value = .38*_tone(250+1700*(1-p),t) + .12*_noise(rng)
        else: value = _noise(rng)*.2
        out.append(value*e)
    return out


def _write(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, x))*32767)) for x in samples))


def ensure_expanded_library() -> list[dict[str, Any]]:
    LIBRARY.mkdir(parents=True, exist_ok=True)
    result=[]
    for name,(tags,duration,kind) in SFX.items():
        path=LIBRARY/f"{name}.wav"
        if not path.exists():
            seed=int(hashlib.sha256(name.encode()).hexdigest()[:8],16)
            _write(path,_synth(kind,duration,seed))
        result.append({"id":name,"filename":path.name,"url":f"/media/sfx/{name}.wav","tags":tags,"source":"generated-expanded"})
    return result
