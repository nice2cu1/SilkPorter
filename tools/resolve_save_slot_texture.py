"""Resolve the current save-slot atlas name using read-only Unity metadata.

The supplied PC Area_Art PNG already uses this Switch canvas layout. Verify
unmodified anchor sprites before updating the stale name from the old target
report. This does not use fuzzy runtime matching or write an AssetBundle.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import UnityPy
from PIL import Image, ImageChops, ImageDraw, ImageStat


ICON_PREFIXES = (
    "Select_Game_Crest_Spools__", "Hornet_Game_Select_", "select_game_HUD_",
    "completion__", "mode_select_",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def visible(image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    alpha = image.getchannel("A")
    return Image.merge("RGBA", tuple(
        ImageChops.multiply(channel, alpha) for channel in image.split()[:3]
    ) + (alpha,))


def rgba_rms(a: Image.Image, b: Image.Image) -> float:
    values = ImageStat.Stat(ImageChops.difference(visible(a), visible(b))).rms
    return (sum(value * value for value in values) / 4) ** 0.5


def resolve_save_slot_texture(workspace: Path, source: Path, previous_name: str) -> dict:
    bundle = (workspace / "romfs/Data/StreamingAssets/aa/Switch/"
              "atlases_assets_assets/sprites/_atlases/area_art.spriteatlas.bundle")
    if not bundle.is_file():
        raise ValueError(f"缺少存档图标原始图集：{bundle}")
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    env = UnityPy.load(str(bundle))
    atlas = next(obj.read() for obj in env.objects if obj.type.name == "SpriteAtlas")
    render_data = dict(atlas.m_RenderDataMap)
    targets = [obj for obj in env.objects if obj.type.name == "Texture2D"
               and obj.peek_name().startswith("sactx-1-2048x2048-ASTC 4x4-Area_Art-")]
    if len(targets) != 1:
        raise ValueError("Area_Art 目标纹理不唯一或格式/尺寸已变化")
    target = targets[0]
    texture = target.read()
    png = Image.open(source).convert("RGBA")
    if (texture.m_Width, texture.m_Height, int(texture.m_TextureFormat), texture.m_MipCount) != (2048, 2048, 48, 1):
        raise ValueError("Area_Art 目标 Texture2D 元数据不符合已核对的输入")
    if png.size != (2048, 2048):
        raise ValueError(f"Area_Art PNG 尺寸不符：{png.size}")
    sprites = {
        obj.path_id: obj for obj in env.objects if obj.type.name == "Sprite"
        and render_data[obj.read().m_RenderDataKey].texture.path_id == target.path_id
    }
    originals = {pid: obj.read().image.convert("RGBA").copy() for pid, obj in sprites.items()}
    # Only override UnityPy's decoded image cache to render the source through
    # current Switch meshes. Serialized data remains immutable.
    target.assets_file._cache[target.path_id] = png.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    rendered = {pid: obj.read().image.convert("RGBA").copy() for pid, obj in sprites.items()}
    rows = []
    for pid, obj in sprites.items():
        sprite = obj.read()
        data = render_data[sprite.m_RenderDataKey]
        rows.append({
            "name": sprite.m_Name, "pathId": pid,
            "icon": sprite.m_Name.startswith(ICON_PREFIXES),
            "originalToSkinVisibleRms": rgba_rms(originals[pid], rendered[pid]),
            "rect": [data.textureRect.x, data.textureRect.y,
                     data.textureRect.width, data.textureRect.height],
            "settingsRaw": int(data.settingsRaw),
            "renderDataKey": str(sprite.m_RenderDataKey),
        })
    anchors = [row for row in rows if not row["icon"]]
    # These anchors span backgrounds, borders and small UI sprites. They catch
    # atlas repacking; they do not replace PC binary metadata for new layouts.
    if len(anchors) < 32 or any(row["originalToSkinVisibleRms"] > 10 for row in anchors):
        raise ValueError("Area_Art PNG 与当前 Switch 布局无法确认；需要原版 PC SpriteAtlas 元数据重排")
    required = {"select_game_HUD_0001_health", "Select_Game_Crest_Spools__0000_hunter",
                "Hornet_Game_Select_Silk_01", "Hornet_Game_Select_Silk_02",
                "Hornet_Game_Select_Silk_03"}
    if not required <= {row["name"] for row in rows}:
        raise ValueError("当前图集中缺少已确认的存档状态 Sprite")

    output = workspace / "output/reports/runtime-save-slot" / source.parent.parent.name
    output.mkdir(parents=True, exist_ok=True)
    preview_names = ["Select_Game_Crest_Spools__0000_hunter", "select_game_HUD_0001_health",
                     "Hornet_Game_Select_Silk_01", "Hornet_Game_Select_thread_rod",
                     "Hornet_Game_Select_UI_0002_3", "Hornet_Game_Select_UI_0001_4"]
    canvas = Image.new("RGB", (900, len(preview_names) * 140 + 30), (36, 40, 46))
    draw = ImageDraw.Draw(canvas)
    draw.text((360, 8), "Original Switch", fill="white")
    draw.text((650, 8), "Skin through Switch mesh", fill="white")
    for i, name in enumerate(preview_names):
        pid = next(pid for pid, obj in sprites.items() if obj.peek_name() == name)
        y = 30 + i * 140
        draw.text((10, y + 12), name, fill="white")
        for col, image in enumerate((originals[pid], rendered[pid])):
            im = image.copy()
            factor = min(2, 240 / im.width, 110 / im.height)
            im = im.resize((round(im.width * factor), round(im.height * factor)), Image.Resampling.NEAREST)
            canvas.paste(im, (370 + col * 300, y + 20), im)
    canvas.save(output / "save-slot-icons.png")
    report = {
        "source": str(source), "sourceSha256": digest(source),
        "bundle": str(bundle), "bundleSha256": digest(bundle),
        "previousTextureName": previous_name, "currentTextureName": texture.m_Name,
        "targetPathId": target.path_id, "spriteRegionsChecked": len(rows),
        "anchorCount": len(anchors),
        "anchorMaxVisibleRms": max(row["originalToSkinVisibleRms"] for row in anchors),
        "evidence": "Current Switch metadata plus source PNG anchor-region and mesh rendering checks; no PC bundle metadata comparison",
        "unchangedOrMinorDifferenceIcons": [row["name"] for row in rows
            if row["icon"] and row["originalToSkinVisibleRms"] <= 10],
        "sprites": rows, "runtimeOnly": True, "hardwareValidation": "pending",
    }
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"name": texture.m_Name, "pathId": target.path_id,
            "validationReport": report_path.relative_to(workspace).as_posix(),
            "bindingEvidence": {"previousName": previous_name, "anchorCount": len(anchors),
                                "anchorMaxVisibleRms": report["anchorMaxVisibleRms"],
                                "bundleSha256": report["bundleSha256"]}}
