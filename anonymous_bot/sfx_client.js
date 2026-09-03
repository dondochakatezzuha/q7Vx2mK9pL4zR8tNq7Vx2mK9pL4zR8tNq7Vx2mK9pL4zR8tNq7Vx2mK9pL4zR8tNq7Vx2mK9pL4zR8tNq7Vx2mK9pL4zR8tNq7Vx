/* Action SFX mixer for the Regnum of Regalia web client.
 *
 * It is deliberately separate from the music player. Music transitions should
 * crossfade; SFX are short-lived voices that can overlap music. Important SFX
 * temporarily duck the music bus instead of replacing it.
 *
 * The existing web client can feed this module with:
 *   window.RoRSFX.handle({ asset: { url: '/media/sfx/explosion_large.wav' }, intensity: .9 })
 */
(() => {
  let ctx = null;
  let musicGain = null;
  let sfxGain = null;
  const active = new Set();
  const cache = new Map();
  const MAX_VOICES = 12;
  const DUCK = { current: 1, timer: null };

  function ensureContext() {
    if (!ctx) {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      musicGain = ctx.createGain();
      sfxGain = ctx.createGain();
      musicGain.gain.value = 1;
      sfxGain.gain.value = 1;
      musicGain.connect(ctx.destination);
      sfxGain.connect(ctx.destination);
    }
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    return ctx;
  }

  async function load(url) {
    if (cache.has(url)) return cache.get(url);
    const promise = fetch(url, { credentials: 'same-origin' })
      .then(r => { if (!r.ok) throw new Error(`SFX HTTP ${r.status}`); return r.arrayBuffer(); })
      .then(bytes => ensureContext().decodeAudioData(bytes));
    cache.set(url, promise);
    try { return await promise; } catch (e) { cache.delete(url); throw e; }
  }

  function rampMusic(value, seconds = 0.12) {
    if (!musicGain || !ctx) return;
    const now = ctx.currentTime;
    musicGain.gain.cancelScheduledValues(now);
    musicGain.gain.setValueAtTime(musicGain.gain.value, now);
    musicGain.gain.linearRampToValueAtTime(value, now + seconds);
  }

  function duckMusic(amount, seconds = 0.16, hold = 0.35) {
    if (!musicGain || !ctx) return;
    const target = Math.max(0.18, Math.min(1, amount));
    rampMusic(target, seconds);
    clearTimeout(DUCK.timer);
    DUCK.timer = setTimeout(() => rampMusic(1, 0.28), Math.max(80, hold * 1000));
  }

  async function play(event) {
    if (!event || !event.asset || !event.asset.url) return false;
    if (active.size >= MAX_VOICES) return false;
    const audioCtx = ensureContext();
    try {
      const buffer = await load(event.asset.url);
      if (active.size >= MAX_VOICES) return false;
      const source = audioCtx.createBufferSource();
      const gain = audioCtx.createGain();
      const intensity = Math.max(0.15, Math.min(1, Number(event.intensity ?? 0.5)));
      gain.gain.value = 0.45 + intensity * 0.4;
      source.buffer = buffer;
      source.connect(gain).connect(sfxGain);
      active.add(source);
      source.onended = () => active.delete(source);

      // Major impacts/explosions make the soundtrack step back briefly.
      if (intensity >= 0.72 || /explosion|catastrophic|impact|critical/i.test(String(event.event || ''))) {
        duckMusic(intensity >= 0.9 ? 0.35 : 0.55, 0.10, Math.min(0.9, buffer.duration * 0.55));
      }
      source.start();
      return true;
    } catch (error) {
      console.warn('[RoR SFX]', error);
      return false;
    }
  }

  // The music player can connect its existing output through this bus later.
  function connectMusicNode(node) {
    ensureContext();
    if (!node) return;
    try { node.disconnect(); } catch (_) {}
    node.connect(musicGain);
  }

  window.RoRSFX = Object.freeze({ play, handle: play, connectMusicNode, preload: load });
})();
