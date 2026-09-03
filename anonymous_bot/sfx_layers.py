"""Dynamic layered SFX orchestration.

Keeps action audio compositional: one narrative event can produce several
simultaneous/sequenced layers. Vocal gender is explicit for players and
randomized only for anonymous NPCs.
"""
from __future__ import annotations

import random
from typing import Any


# Ordered layer recipes. The existing sfx_engine resolves each sound id to a
# generated WAV. These recipes only decide what should play together and when.
ACTION_RECIPES: dict[str, list[dict[str, Any]]] = {
    "explosion": [
        {"id": "explosion_large", "offset": 0.00, "volume": 1.00},
        {"id": "heavy_impact", "offset": 0.02, "volume": 0.75},
        {"id": "shockwave", "offset": 0.20, "volume": 0.70},
        {"id": "rubble_fall", "offset": 0.45, "volume": 0.55},
    ],
    "catastrophic_explosion": [
        {"id": "explosion_catastrophic", "offset": 0.00, "volume": 1.00},
        {"id": "heavy_impact", "offset": 0.04, "volume": 0.90},
        {"id": "shockwave", "offset": 0.18, "volume": 0.90},
        {"id": "ground_crack", "offset": 0.30, "volume": 0.75},
        {"id": "rubble_fall", "offset": 0.60, "volume": 0.70},
    ],
    "laser": [
        {"id": "laser_charge", "offset": -0.65, "volume": 0.65},
        {"id": "laser_fire", "offset": 0.00, "volume": 0.95},
    ],
    "laser_wall_break": [
        {"id": "laser_charge", "offset": -0.65, "volume": 0.65},
        {"id": "laser_heavy", "offset": 0.00, "volume": 1.00},
        {"id": "wall_break", "offset": 0.12, "volume": 0.85},
        {"id": "rubble_fall", "offset": 0.45, "volume": 0.65},
    ],
    "time_stop": [
        {"id": "time_stop", "offset": 0.00, "volume": 1.00},
    ],
    "time_reverse": [
        {"id": "time_reverse", "offset": 0.00, "volume": 1.00},
    ],
    "time_stop_release": [
        {"id": "time_stop_release", "offset": 0.00, "volume": 0.90},
        {"id": "shockwave", "offset": 0.10, "volume": 0.70},
    ],
    "ground_slam": [
        {"id": "whoosh_heavy", "offset": -0.25, "volume": 0.70},
        {"id": "ground_slam", "offset": 0.00, "volume": 1.00},
        {"id": "ground_crack", "offset": 0.08, "volume": 0.90},
        {"id": "rubble_fall", "offset": 0.35, "volume": 0.55},
        {"id": "shockwave", "offset": 0.16, "volume": 0.65},
    ],
    "sword_ground_strike": [
        {"id": "sword_slash", "offset": -0.20, "volume": 0.70},
        {"id": "sword_heavy", "offset": 0.00, "volume": 0.85},
        {"id": "ground_crack", "offset": 0.04, "volume": 0.90},
        {"id": "rubble_fall", "offset": 0.30, "volume": 0.55},
    ],
    "magic_blast": [
        {"id": "magic_charged", "offset": -0.70, "volume": 0.60},
        {"id": "magic_burst", "offset": 0.00, "volume": 0.90},
        {"id": "shockwave", "offset": 0.12, "volume": 0.55},
    ],
    "fireball_impact": [
        {"id": "magic_charged", "offset": -0.65, "volume": 0.55},
        {"id": "fire_burst", "offset": 0.00, "volume": 0.90},
        {"id": "explosion_large", "offset": 0.08, "volume": 0.95},
        {"id": "shockwave", "offset": 0.25, "volume": 0.65},
        {"id": "rubble_fall", "offset": 0.45, "volume": 0.50},
    ],
    "teleport": [
        {"id": "teleport", "offset": 0.00, "volume": 0.90},
    ],
    "dash": [
        {"id": "dash_whoosh", "offset": 0.00, "volume": 0.85},
    ],
    "critical_hit": [
        {"id": "sword_slash", "offset": -0.15, "volume": 0.75},
        {"id": "heavy_impact", "offset": 0.00, "volume": 1.00},
        {"id": "shockwave", "offset": 0.08, "volume": 0.60},
    ],
    "block": [{"id": "shield_block", "offset": 0.00, "volume": 0.90}],
    "parry": [{"id": "parry", "offset": 0.00, "volume": 0.95}],
    "heal": [{"id": "heal", "offset": 0.00, "volume": 0.80}],
    "transformation": [
        {"id": "charge_up", "offset": -0.90, "volume": 0.70},
        {"id": "energy_burst", "offset": 0.00, "volume": 0.95},
        {"id": "shockwave", "offset": 0.12, "volume": 0.65},
    ],
}

VOCAL_SFX: dict[str, dict[str, str]] = {
    "pain": {"male": "vocal_pain_male", "female": "vocal_pain_female"},
    "scream": {"male": "vocal_scream_male", "female": "vocal_scream_female"},
    "battle_cry": {"male": "vocal_battle_cry_male", "female": "vocal_battle_cry_female"},
    "death": {"male": "vocal_death_male", "female": "vocal_death_female"},
    "effort": {"male": "vocal_effort_male", "female": "vocal_effort_female"},
    "gasp": {"male": "vocal_gasp_male", "female": "vocal_gasp_female"},
}


def resolve_gender(actor: dict[str, Any] | None = None, *, rng: random.Random | None = None) -> str:
    """Return explicit player gender; anonymous NPCs are randomized."""
    actor = actor or {}
    gender = str(actor.get("gender") or actor.get("voice_gender") or "").strip().casefold()
    if gender in {"male", "m", "man"}:
        return "male"
    if gender in {"female", "f", "woman"}:
        return "female"
    # Only entities without a declared gender are randomized. This covers
    # generated/random NPCs without overriding a female or male player.
    if actor.get("is_player") or actor.get("player_id") or actor.get("user_id"):
        return "male"
    return (rng or random).choice(("male", "female"))


def build_soundscape(action: str, *, actor: dict[str, Any] | None = None,
                     vocal: str | None = None, rng: random.Random | None = None) -> list[dict[str, Any]]:
    """Build layered playback events for an action.

    Returned events are intentionally declarative so the browser/client can
    schedule overlapping SFX without blocking the game narration.
    """
    layers = [dict(layer) for layer in ACTION_RECIPES.get(action, [])]
    if vocal:
        gender = resolve_gender(actor, rng=rng)
        voice_id = VOCAL_SFX.get(vocal, {}).get(gender)
        if voice_id:
            layers.append({"id": voice_id, "offset": 0.00, "volume": 0.80,
                           "gender": gender, "vocal": vocal})
    return layers


def enrich_action(action: str, *, actor: dict[str, Any] | None = None,
                   vocal: str | None = None, rng: random.Random | None = None) -> dict[str, Any]:
    """Return a serializable action event for the web audio client."""
    return {
        "action": action,
        "layers": build_soundscape(action, actor=actor, vocal=vocal, rng=rng),
        "layered": True,
    }
