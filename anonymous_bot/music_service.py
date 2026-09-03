"""Shared Project Brain music library abstraction.

The application code stays in GitHub, while actual audio can live in an
S3-compatible object store (Cloudflare R2, S3, Backblaze B2, etc.).

The service deliberately supports two modes:
  * cloud: MUSIC_PUBLIC_BASE_URL points at the object/CDN URL;
  * local: falls back to the existing campaign_data/web_audio library.

Cloud mode does not download the library to the client machine. The browser
receives a URL for the requested track and streams it directly from storage.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin


AUDIO_EXTENSIONS = {".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac"}


def cloud_enabled() -> bool:
    return bool((os.getenv("MUSIC_PUBLIC_BASE_URL") or "").strip())


def public_base_url() -> str:
    return (os.getenv("MUSIC_PUBLIC_BASE_URL") or "").strip().rstrip("/")


def object_key(filename: str) -> str:
    """Normalize a track path into a safe object key."""
    parts = [part for part in Path(str(filename)).as_posix().split("/") if part not in {"", ".", ".."}]
    return "/".join(parts)


def stream_url(filename: str, local_base: str = "/media/audio/") -> str:
    """Return a remote stream URL when cloud hosting is configured."""
    key = object_key(filename)
    if cloud_enabled():
        return urljoin(public_base_url() + "/", quote(key, safe="/"))
    return local_base.rstrip("/") + "/" + quote(key, safe="/")


def track_id(filename: str) -> str:
    return "library-" + hashlib.sha256(object_key(filename).encode("utf-8")).hexdigest()[:16]


def make_track(filename: str, name: str | None = None, tags: list[str] | None = None, source: str = "cloud-library") -> dict[str, Any]:
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
    """Build a small manifest safe to commit to GitHub (no audio bytes)."""
    clean = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        clean.append({
            "id": str(track.get("id") or track_id(str(track.get("filename") or ""))),
            "name": str(track.get("name") or "Untitled"),
            "filename": object_key(str(track.get("filename") or "")),
            "url": str(track.get("url") or stream_url(str(track.get("filename") or ""))),
            "tags": [str(x) for x in (track.get("tags") or []) if str(x).strip()],
            "character": track.get("character"),
            "npc": track.get("npc"),
            "source": str(track.get("source") or "cloud-library"),
        })
    return {"version": 1, "storage": "cloud" if cloud_enabled() else "local", "tracks": clean}


def write_manifest(path: str | Path, tracks: list[dict[str, Any]]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_manifest(tracks), ensure_ascii=False, indent=2), encoding="utf-8")
    return target
