"""Read-only comparison of the skin map atlas against Switch sprite regions."""
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from build_repacked_sprite_remaps import load_atlas, texture_box, rotate, rms
from inspect_hero_death_assets import ROOT, SWITCH


def main():
    out = ROOT / "output/reports/hero-death-assets/fix-preview"
    out.mkdir(parents=True, exist_ok=True)
    source = ROOT / "pc-mods/丝之歌x星见雅皮肤2.0/XJY-Zycl_dhth/Texture2D/sactx-0-4096x4096-BC7-Hornet_Map-3c6e4b0a.png"
    env, _, _, textures, sprites, render = load_atlas(
        SWITCH / "atlases_assets_assets/sprites/_atlases/hornet_map.spriteatlas.bundle")
    skin = Image.open(source).convert("RGBA")
    original = next(iter(textures.values())).read().image.convert("RGBA")
    rows = []
    for key, obj in sprites.items():
        sprite = obj.read()
        data = render[key]
        box = texture_box(data.textureRect, original.height)
        raw_original = original.crop(box)
        raw_skin = skin.crop(box)
        error = rms(raw_original, raw_skin)
        rows.append({"name": sprite.m_Name, "box": box, "rms": error,
                     "rotation": (data.settingsRaw >> 2) & 15})
        if sprite.m_Name in {"Shade_Pin", "Map_shade_fader", "pin_stag_station", "Tut_01"}:
            rotate(raw_skin, data.settingsRaw).save(out / f"skin-{sprite.m_Name}.png")
            rotate(raw_original, data.settingsRaw).save(out / f"original-{sprite.m_Name}.png")
    result = {"source": str(source), "spriteCount": len(rows),
              "rmsUnder10": sum(row["rms"] < 10 for row in rows),
              "medianRms": sorted(row["rms"] for row in rows)[len(rows)//2],
              "rows": rows}
    (out / "map-layout-check.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}))
    print(json.dumps([row for row in rows if row["name"] == "Shade_Pin"]))


if __name__ == "__main__":
    main()
