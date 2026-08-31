# Web Client Regression Policy

This project is now treated as a single evolving web client, not a sequence of replacement demos.

## Rule
When a new feature is added or an existing feature is fixed, the current working behavior and layout are preserved unless the requested change explicitly replaces it.

## Protected behavior
- Game-page GM quick-control panel stays on Game.
- GM Chat is GM-only and exists only on the GM Chat page.
- General uses Discord channel `1535189087282008114`.
- Game uses Discord channel `1535189087282008118`.
- Character navigation remains `Character` and has no Age field.
- Player pages never expose GM controls or GM-only data.
- Danger percentage/explanation is hidden from players; only the visual effect is shown.
- Current neutral black/gray/red visual palette is preserved.
- Discord OAuth/session and tester-GM authorization are preserved.

## Required check
Run:

    python tests/web_regression_check.py

before packaging a new web build.
