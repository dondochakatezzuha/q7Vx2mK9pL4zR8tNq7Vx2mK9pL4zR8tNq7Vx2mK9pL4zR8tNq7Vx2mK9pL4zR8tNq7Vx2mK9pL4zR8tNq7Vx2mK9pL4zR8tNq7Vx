"""Backward-compatible import shim.

The Gemini GM assistant now lives in ``features.gm.assistant``.
"""

from .gm.assistant import *  # noqa: F401,F403
