"""Backward-compatible exports for the player dashboard.

The /main player UI now lives in :mod:`features.main_ui` so the main
character dashboard is isolated from the rest of the bot's feature modules.
Existing imports of ``features.ui.MainView`` continue to work.
"""
from .main_ui import *
