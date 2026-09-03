"""Standalone local-only audio host for the Anonymous RPG Bot.

This process exposes the complete audio library on the local machine without
putting audio binaries in GitHub or requiring cloud storage. It is intentionally
separate from the production web app so development can start with the local
library first.

Endpoints:
    GET /api/library       -> complete local music + SFX manifest
    GET /media/audio/...   -> local music file
    GET /media/sfx/...     -> local SFX file
    GET /health            -> simple health check

Run from the repository root with:
    python -m anonymous_bot.local_audio_host

Environment variables:
    LOCAL_AUDIO_HOST (default: 127.0.0.1)
    LOCAL_AUDIO_PORT (default: 18500)
"""
from __future__ import annotations

import http.server
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .local_audio_library import LOCAL_MUSIC_DIR, LOCAL_SFX_DIR, build_local_library

HOST = os.getenv("LOCAL_AUDIO_HOST", "127.0.0.1")
PORT = int(os.getenv("LOCAL_AUDIO_PORT", "18500"))


class LocalAudioHandler(http.server.BaseHTTPRequestHandler):
    server_version = "AnonymousRPG-LocalAudio/1.0"

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _safe_file(self, root: Path, relative: str) -> Path | None:
        try:
            candidate = (root / unquote(relative)).resolve()
            root_resolved = root.resolve()
            candidate.relative_to(root_resolved)
        except (OSError, ValueError):
            return None
        return candidate if candidate.is_file() else None

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path

        if path == "/health":
            self._send_bytes(200, b'{"ok":true,"mode":"local"}', "application/json; charset=utf-8")
            return

        if path == "/api/library":
            payload = json.dumps(build_local_library(), ensure_ascii=False).encode("utf-8")
            self._send_bytes(200, payload, "application/json; charset=utf-8")
            return

        if path.startswith("/media/audio/"):
            file_path = self._safe_file(LOCAL_MUSIC_DIR, path[len("/media/audio/"):])
        elif path.startswith("/media/sfx/"):
            file_path = self._safe_file(LOCAL_SFX_DIR, path[len("/media/sfx/"):])
        else:
            self._send_bytes(404, b"Not found", "text/plain; charset=utf-8")
            return

        if file_path is None:
            self._send_bytes(404, b"Audio file not found", "text/plain; charset=utf-8")
            return

        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        try:
            body = file_path.read_bytes()
        except OSError:
            self._send_bytes(500, b"Unable to read audio file", "text/plain; charset=utf-8")
            return
        self._send_bytes(200, body, content_type)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[local-audio] {self.address_string()} - {fmt % args}")


def serve(host: str = HOST, port: int = PORT) -> None:
    with http.server.ThreadingHTTPServer((host, port), LocalAudioHandler) as server:
        print(f"Local audio host: http://{host}:{port}")
        print(f"Library manifest: http://{host}:{port}/api/library")
        print(f"Music: {LOCAL_MUSIC_DIR}")
        print(f"SFX: {LOCAL_SFX_DIR}")
        server.serve_forever()


if __name__ == "__main__":
    serve()
