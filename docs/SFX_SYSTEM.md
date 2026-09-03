# Action SFX System

The project now has a separate action-SFX layer from the soundtrack.

## Pipeline

```text
Discord / web roleplay event
        |
        v
semantic event detector
        |
        +--> family: explosion / laser / magic / sword / impact / ...
        +--> intensity
        +--> supporting tags
        |
        v
starter/tag-based SFX selector
        |
        v
browser SFX mixer
        |
        +--> SFX plays immediately
        +--> important SFX temporarily duck music
        +--> music itself remains responsible for crossfades
```

`anonymous_bot/sfx_engine.py` contains the semantic detector, weighted tag matcher, and a standard-library WAV generator. The generator creates a small starter pack in `campaign_data/web_sfx/` without committing binary audio files to GitHub.

`anonymous_bot/sfx_client.js` contains the browser mixer. It keeps short SFX voices separate from the soundtrack, limits simultaneous voices, caches decoded audio, and ducks music for important impacts/explosions instead of replacing the current song.

## Starter families

- Sword slash / heavy sword / blade clash
- Heavy impact
- Small / large / catastrophic explosion
- Laser fire / heavy laser
- Magic cast / magic burst
- Fire burst
- Lightning strike
- Dash / teleport
- Shield block / parry
- Ground slam

## Detection philosophy

Do not build an enormous brittle dictionary of every possible word. The detector uses semantic families and accepts optional structured event data. Structured fields such as `event`, `action`, `weapon`, `ability`, `element`, `impact`, and `effect` receive stronger weighting than individual prose words.

Example:

`The fireball crashes into the fortress wall, causing a massive explosion.`

can resolve toward an explosion with fire/impact/ground context and high intensity.

A future AI/game event layer should pass structured events when available, but prose detection remains useful as a fallback.

## Music behavior contract

Music and SFX are different buses:

- Music state changes should crossfade rather than hard-cut.
- Weapon/magic/explosion SFX should be short-lived voices over the current music.
- Major SFX should temporarily duck the music.
- SFX must never start a second full music track.
- A voice limit prevents runaway combat scenes from creating unbounded simultaneous audio.

The browser implementation uses the Web Audio API's audio graph and gain nodes for this separation and volume control. See MDN's Web Audio API documentation for the underlying browser audio model.

## Integration note

The new engine and browser mixer are intentionally isolated from the existing large single-page client until the current music playback path is wired to the SFX bus. This keeps the starter implementation safe to test before changing the existing live audio path.
