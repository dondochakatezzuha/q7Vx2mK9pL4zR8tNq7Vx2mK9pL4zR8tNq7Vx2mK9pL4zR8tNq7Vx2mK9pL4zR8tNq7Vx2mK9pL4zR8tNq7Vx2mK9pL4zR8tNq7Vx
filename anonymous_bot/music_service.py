"""Shared music library URL abstraction.

The application code stays private in BOT. Shared music is hosted separately
in the public audio repository so browsers can stream individual tracks
without receiving the private BOT repository or downloading the whole library.

Set MUSIC_PUBLIC_BASE_URL to override the default public repository root.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin


AUDIO_EXTENSIONS = {".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac"}
DEFAULT_PUBLIC_BASE_URL = "https://raw.githubusercontent.com/dondochakatezzuha/x7Qm2L9vK4x7Qm2L9vK4/main/music"


def cloud_enabled() -> bool:
    return True


def public_base_url() -> str:
    return (os.getenv("MUSIC_PUBLIC_BASE_URL") or DEFAULT_PUBLIC_BASE_URL).strip().rstrip("/")


def object_key(filename: str) -> str:
    """Normalize a track path into a safe public-repository key."""
    parts = [part for part in Path(str(filename)).as_posix().split("/") if part not in {"", ".", ".."}]
    return "/".join(parts)


def stream_url(filename: str, local_base: str = "/media/audio/") -> str:
    """Return the public shared stream URL for one requested track."""
    key = object_key(filename)
    if cloud_enabled():
        return urljoin(public_base_url() + "/", quote(key, safe="/"))
    return local_base.rstrip("/") + "/" + quote(key, safe="/")


def track_id(filename: str) -> str:
    return "library-" + hashlib.sha256(object_key(filename).encode("utf-8")).hexdigest()[:16]


def make_track(filename: str, name: str | None = None, tags: list[str] | None = None, source: str = "public-audio-repo") -> dict[str, Any]:
    key = object_key(filename)
    return {
        "id": track_id(key),
        "name": name or Path(key).stem,
        "filename": key,
        "url": stream_url(key),
        "tags": list(dict.fromkeys(str(tag).strip() for tag in (tags or []) if str(tag).strip())),
        "source": source,
    }


def build_manifest(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a small manifest; audio bytes remain outside the private repo."""
    clean = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        filename = object_key(str(track.get("filename") or ""))
        clean.append({
            "id": str(track.get("id") or track_id(filename)),
            "name": str(track.get("name") or "Untitled"),
            "filename": filename,
            "url": str(track.get("url") or stream_url(filename)),
            "tags": [str(x) for x in (track.get("tags") or []) if str(x).strip()],
            "character": track.get("character"),
            "npc": track.get("npc"),
            "source": str(track.get("source") or "public-audio-repo"),
        })
    return {"version": 1, "storage": "public-github-audio", "tracks": clean}


def write_manifest(path: str | Path, tracks: list[dict[str, Any]]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_manifest(tracks), ensure_ascii=False, indent=2), encoding="utf-8")
    return target
