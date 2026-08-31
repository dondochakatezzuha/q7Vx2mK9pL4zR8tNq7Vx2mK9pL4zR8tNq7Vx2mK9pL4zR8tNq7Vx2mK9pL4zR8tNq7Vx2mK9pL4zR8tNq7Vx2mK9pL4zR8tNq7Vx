from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

RARITY_TONE = {
    "Common": (140, 140, 140), "Uncommon": (80, 180, 110), "Rare": (75, 145, 235),
    "Legendary": (230, 170, 60), "Mythic": (200, 80, 220), "Ancient": (190, 120, 70),
    "Ancestral": (180, 100, 150), "Eternal": (100, 210, 210), "Abyssal": (90, 60, 150),
    "Cosmic": (100, 80, 220), "Astral": (120, 170, 255), "Galactic": (160, 100, 230),
    "Nebula": (220, 100, 190), "Stellar": (245, 210, 100), "Void": (70, 70, 90),
    "Temporal": (90, 200, 170), "Paradox": (200, 90, 120), "Singularity": (110, 110, 130),
    "Dimensional": (80, 180, 220), "Ascended": (220, 220, 130), "Sovereign": (220, 170, 70),
    "Immortal": (230, 230, 230), "Divine": (255, 215, 110), "Transcendent": (200, 180, 255),
    "Zenith": (255, 245, 190),
}

def _font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for p in paths:
        try: return ImageFont.truetype(p, size)
        except OSError: pass
    return ImageFont.load_default()


def make_item_card(item):
    width, height = 1000, 600
    rarity = item.get("rarity", "Common")
    accent = RARITY_TONE.get(rarity, (120, 140, 180))
    img = Image.new("RGB", (width, height), (18, 20, 28))
    draw = ImageDraw.Draw(img)
    # Clean RPG card: this is deliberately procedural, so it requires no external image service.
    for y in range(height):
        t = y / height
        c = tuple(int(18 + accent[i] * 0.10 * t) for i in range(3))
        draw.line((0, y, width, y), fill=c)
    draw.rounded_rectangle((28, 28, width-28, height-28), radius=28, outline=accent, width=5)
    draw.text((60, 55), item.get("name", "Unnamed Weapon"), font=_font(42, True), fill=(245,245,245))
    draw.text((62, 112), f"{rarity}  •  {item.get('category', 'item').upper()}", font=_font(24, True), fill=accent)

    # Prefer AI-generated artwork when available; otherwise use the local silhouette.
    art_path = item.get("art_path")
    if art_path and Path(art_path).exists():
        try:
            art = Image.open(art_path).convert("RGBA")
            art.thumbnail((330, 330))
            img.paste(art, (85, 190), art)
        except Exception:
            art_path = None
    cx, cy = 250, 360
    if not art_path:
        draw.polygon([(cx-100, cy+70), (cx+85, cy-120), (cx+120, cy-85), (cx-65, cy+105)], fill=accent)
        draw.rectangle((cx-125, cy+65, cx-30, cy+92), fill=(80,70,60))
        draw.ellipse((cx-10, cy-155, cx+35, cy-110), fill=(235,235,235))
        draw.line((cx-55, cy+130, cx+95, cy-20), fill=(235,235,235), width=8)

    stats = item.get("stats", {})
    lines = [
        f"Attack   {stats.get('attack',0)}     Speed   {stats.get('speed',0)}",
        f"Crit     {stats.get('crit',0)}%    Accuracy {stats.get('accuracy',0)}%",
        f"Defense  {stats.get('defense',0)}     Penetration {stats.get('penetration',0)}",
    ]
    draw.text((430, 230), "STATS", font=_font(26, True), fill=accent)
    yy = 275
    for line in lines:
        draw.text((430, yy), line, font=_font(22), fill=(230,230,230)); yy += 48
    draw.text((430, 410), "EFFECT", font=_font(24, True), fill=accent)
    effect = str(item.get("effect", ""))[:75]
    draw.text((430, 450), effect, font=_font(20), fill=(235,235,235))

    out = BytesIO()
    img.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out
