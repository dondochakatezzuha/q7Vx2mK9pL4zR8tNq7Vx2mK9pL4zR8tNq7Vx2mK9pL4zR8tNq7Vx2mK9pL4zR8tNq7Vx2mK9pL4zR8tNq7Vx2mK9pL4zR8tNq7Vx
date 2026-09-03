"""Dynamic layered SFX orchestration.

Recipes use original/generated sound identities and are designed to stack into
cinematic scenes instead of playing one flat sound per event.
"""
from __future__ import annotations
import random
from typing import Any
from .sfx_expansion import ensure_expanded_library
from .sfx_specials import ensure_special_library

ACTION_RECIPES: dict[str, list[dict[str, Any]]] = {
    "explosion": [{"id":"explosion_large","offset":0.00,"volume":1.00},{"id":"heavy_impact","offset":0.02,"volume":0.75},{"id":"shockwave","offset":0.20,"volume":0.70},{"id":"rubble_fall","offset":0.45,"volume":0.55}],
    "catastrophic_explosion": [{"id":"explosion_catastrophic","offset":0.00,"volume":1.00},{"id":"heavy_impact","offset":0.04,"volume":0.90},{"id":"shockwave","offset":0.18,"volume":0.90},{"id":"ground_crack","offset":0.30,"volume":0.75},{"id":"rubble_fall","offset":0.60,"volume":0.70}],
    "laser": [{"id":"laser_charge","offset":-0.65,"volume":0.65},{"id":"laser_fire","offset":0.00,"volume":0.95}],
    "laser_wall_break": [{"id":"laser_charge","offset":-0.65,"volume":0.65},{"id":"laser_heavy","offset":0.00,"volume":1.00},{"id":"wall_break","offset":0.12,"volume":0.85},{"id":"rubble_fall","offset":0.45,"volume":0.65}],
    "cero": [{"id":"cero_charge","offset":-0.90,"volume":0.70},{"id":"cero_charge","offset":-0.35,"volume":0.82},{"id":"cero_blast","offset":0.00,"volume":1.00},{"id":"shockwave","offset":0.22,"volume":0.72}],
    "cero_impact": [{"id":"cero_blast","offset":0.00,"volume":1.00},{"id":"explosion_large","offset":0.10,"volume":0.75},{"id":"wall_break","offset":0.18,"volume":0.70},{"id":"rubble_fall","offset":0.48,"volume":0.55}],
    "energy_sword_wave": [{"id":"energy_charge_deep","offset":-0.70,"volume":0.62},{"id":"energy_release_massive","offset":0.00,"volume":1.00},{"id":"sword_slash","offset":0.04,"volume":0.72},{"id":"shockwave_heavy","offset":0.16,"volume":0.60}],
    "spiritual_pressure": [{"id":"low_frequency_rumble","offset":0.00,"volume":0.85},{"id":"energy_release_massive","offset":0.35,"volume":0.45}],
    "berserker_roar": [{"id":"vocal_berserker_roar","offset":0.00,"volume":1.00},{"id":"heavy_impact","offset":0.02,"volume":0.35},{"id":"shockwave","offset":0.10,"volume":0.42}],
    "monster_roar": [{"id":"vocal_monster_roar","offset":0.00,"volume":1.00},{"id":"rubble_fall","offset":0.08,"volume":0.30}],
    "transformation_roar": [{"id":"charge_up","offset":-1.00,"volume":0.65},{"id":"vocal_berserker_roar","offset":-0.05,"volume":0.90},{"id":"energy_burst","offset":0.00,"volume":1.00},{"id":"shockwave","offset":0.13,"volume":0.72}],
    "time_stop": [{"id":"time_stop","offset":0.00,"volume":1.00}],
    "time_reverse": [{"id":"time_reverse","offset":0.00,"volume":1.00}],
    "time_stop_release": [{"id":"time_stop_release","offset":0.00,"volume":0.90},{"id":"shockwave","offset":0.10,"volume":0.70}],
    "ground_slam": [{"id":"whoosh_heavy","offset":-0.25,"volume":0.70},{"id":"ground_slam","offset":0.00,"volume":1.00},{"id":"ground_crack","offset":0.08,"volume":0.90},{"id":"rubble_fall","offset":0.35,"volume":0.55},{"id":"shockwave","offset":0.16,"volume":0.65}],
    "sword_ground_strike": [{"id":"sword_slash","offset":-0.20,"volume":0.70},{"id":"sword_heavy","offset":0.00,"volume":0.85},{"id":"ground_crack","offset":0.04,"volume":0.90},{"id":"rubble_fall","offset":0.30,"volume":0.55}],
    "magic_blast": [{"id":"magic_charged","offset":-0.70,"volume":0.60},{"id":"magic_burst","offset":0.00,"volume":0.90},{"id":"shockwave","offset":0.12,"volume":0.55}],
    "fireball_impact": [{"id":"magic_charged","offset":-0.65,"volume":0.55},{"id":"fire_burst","offset":0.00,"volume":0.90},{"id":"explosion_large","offset":0.08,"volume":0.95},{"id":"shockwave","offset":0.25,"volume":0.65},{"id":"rubble_fall","offset":0.45,"volume":0.50}],
    "teleport": [{"id":"teleport","offset":0.00,"volume":0.90}],
    "dash": [{"id":"dash_whoosh","offset":0.00,"volume":0.85}],
    "critical_hit": [{"id":"sword_slash","offset":-0.15,"volume":0.75},{"id":"heavy_impact","offset":0.00,"volume":1.00},{"id":"shockwave","offset":0.08,"volume":0.60}],
    "block": [{"id":"shield_block","offset":0.00,"volume":0.90}],
    "parry": [{"id":"parry","offset":0.00,"volume":0.95}],
    "heal": [{"id":"heal","offset":0.00,"volume":0.80}],
    "transformation": [{"id":"charge_up","offset":-0.90,"volume":0.70},{"id":"energy_burst","offset":0.00,"volume":0.95},{"id":"shockwave","offset":0.12,"volume":0.65}],
    "helicopter_takeoff": [{"id":"helicopter_takeoff","offset":0.00,"volume":0.90},{"id":"helicopter_idle","offset":0.25,"volume":0.75}],
    "helicopter_pass": [{"id":"helicopter_pass","offset":0.00,"volume":0.90}],
    "helicopter_attack": [{"id":"helicopter_attack","offset":0.00,"volume":0.90},{"id":"energy_beam_heavy","offset":0.18,"volume":0.35}],
    "helicopter_crash": [{"id":"helicopter_crash","offset":0.00,"volume":1.00},{"id":"debris_burst","offset":0.20,"volume":0.75},{"id":"fire_crackle","offset":0.55,"volume":0.40}],
    "car_crash": [{"id":"car_crash","offset":0.00,"volume":1.00},{"id":"metal_break","offset":0.08,"volume":0.75},{"id":"glass_break","offset":0.12,"volume":0.70}],
    "vehicle_pass": [{"id":"car_pass","offset":0.00,"volume":0.80}],
    "wall_break": [{"id":"wall_break","offset":0.00,"volume":0.90},{"id":"debris_burst","offset":0.12,"volume":0.75},{"id":"dust_burst","offset":0.28,"volume":0.50}],
    "weapon_swing": [{"id":"sword_slash","offset":0.00,"volume":0.80}],
    "weapon_impact": [{"id":"heavy_impact","offset":0.00,"volume":0.90},{"id":"body_hit","offset":0.03,"volume":0.55}],
}

VOCAL_SFX: dict[str, dict[str, str]] = {
    "pain":{"male":"vocal_pain_male","female":"vocal_pain_female"},
    "scream":{"male":"vocal_scream_male","female":"vocal_scream_female"},
    "battle_cry":{"male":"vocal_battle_cry_male","female":"vocal_battle_cry_female"},
    "death":{"male":"vocal_death_male","female":"vocal_death_female"},
    "effort":{"male":"vocal_effort_male","female":"vocal_effort_female"},
    "gasp":{"male":"vocal_gasp_male","female":"vocal_gasp_female"},
    "berserker_roar":{"male":"vocal_berserker_roar","female":"vocal_berserker_roar"},
    "monster_roar":{"male":"vocal_monster_roar","female":"vocal_monster_roar"},
}

def resolve_gender(actor: dict[str, Any] | None = None, *, rng: random.Random | None = None) -> str:
    actor = actor or {}
    gender = str(actor.get("gender") or actor.get("voice_gender") or "").strip().casefold()
    if gender in {"male","m","man"}: return "male"
    if gender in {"female","f","woman"}: return "female"
    return (rng or random).choice(("male","female"))

def build_soundscape(action: str, *, actor: dict[str, Any] | None = None, vocal: str | None = None, rng: random.Random | None = None) -> list[dict[str, Any]]:
    ensure_expanded_library()
    ensure_special_library()
    layers = [dict(layer) for layer in ACTION_RECIPES.get(action, [])]
    if vocal:
        gender = resolve_gender(actor, rng=rng)
        voice_id = VOCAL_SFX.get(vocal, {}).get(gender)
        if voice_id: layers.append({"id":voice_id,"offset":0.00,"volume":0.80,"gender":gender,"vocal":vocal})
    return layers

def enrich_action(action: str, *, actor: dict[str, Any] | None = None, vocal: str | None = None, rng: random.Random | None = None) -> dict[str, Any]:
    return {"action":action,"layers":build_soundscape(action, actor=actor, vocal=vocal, rng=rng),"layered":True}
