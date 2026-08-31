import os
import re
from pathlib import Path

ART_DIR = Path(__file__).resolve().parent.parent / "data" / "item_art"
ART_DIR.mkdir(parents=True, exist_ok=True)


def build_art_prompt(item):
    category = item.get("category", "item")
    rarity = item.get("rarity", "Common")
    effect = item.get("effect") or "no special magical effect"
    description = item.get("description") or ""
    return (
        f"Dark fantasy RPG {category} concept art. Item name: {item.get('name','Unnamed')}. "
        f"Rarity: {rarity}. Effect: {effect}. Description: {description}. "
        "Show one clear centered item, dramatic but tasteful fantasy lighting, highly detailed, "
        "game inventory artwork, isolated composition, no text, no letters, no watermark."
    )


def generate_item_art(item):
    """Optional AI art. Without OPENAI_API_KEY, returns None and the bot uses its local card art."""
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        result = client.images.generate(
            model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
            prompt=build_art_prompt(item),
            size="1024x1024",
        )
        data = getattr(result.data[0], "b64_json", None)
        if not data:
            return None
        import base64
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", item.get("name", "item"))[:60]
        path = ART_DIR / f"{safe}_{item.get('id','item')}.png"
        path.write_bytes(base64.b64decode(data))
        return str(path)
    except Exception as exc:
        print(f"[item_art] AI image generation unavailable: {exc}")
        return None
