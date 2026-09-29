"""Build runtime-only Sprite texture PNGs for the death-cocoon skin assets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import UnityPy
from PIL import Image

from build_repacked_sprite_remaps import build_atlas, load_atlas, rms, texture_box


DIRECT_TEXTURES = {
    "Hornet_death_pieces_0000s_0001_6": (1800784606295194707, 105, 216, 48),
    "Hornet_death_cocoon_particle_chunks": (8429042907869783626, 76, 331, 48),
    "Hornet_death_spiders": (-1644381266957573592, 25, 100, 4),
}
MAP_TEXTURE_ID = -8895439953096435575
MAP_TEXTURE_NAME = "sactx-0-4096x4096-ASTC 6x6-Hornet_Map-8e07642c"
CORE_DEATH_SPRITES = {
    "Hornet_death_pieces_0000s_0000_death_spider_core",
    "Hornet_death_pieces_0000s_0007_back_thread",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_png(skin_dir: Path, pattern: str) -> Path | None:
    matches = list((skin_dir / "Texture2D").glob(pattern))
    if len(matches) > 1:
        raise ValueError(f"皮肤图片匹配不唯一：{pattern}")
    return matches[0] if matches else None


def resource_filename(name: str) -> str:
    encoded = ["r_"]
    for byte in name.encode("utf-8"):
        if (ord("A") <= byte <= ord("Z") or ord("a") <= byte <= ord("z")
                or ord("0") <= byte <= ord("9") or byte == ord("-")):
            encoded.append(chr(byte))
        else:
            encoded.append(f"_{byte:02X}")
    return "".join(encoded) + ".png"


def append_texture(rows: list[dict], name: str, image: Image.Image,
                   texture_format: int, output_dir: Path, semantic_key: str,
                   source: Path | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / resource_filename(name)
    image.convert("RGBA").save(path)
    reopened = Image.open(path).convert("RGBA")
    if reopened.size != image.size or reopened.tobytes() != image.convert("RGBA").tobytes():
        raise ValueError(f"{name}：输出 PNG 重开后像素不一致")
    row = {
        "name": name,
        "source": path,
        "width": image.width,
        "height": image.height,
        "format": texture_format,
        "semanticKey": semantic_key,
        "size": path.stat().st_size,
        "sha256": digest(path),
        "sourceInput": str(source) if source else None,
    }
    rows.append(row)
    return row


def build_runtime_death_textures(workspace: Path, skin_dir: Path) -> dict:
    workspace = Path(workspace).resolve()
    skin_dir = Path(skin_dir).resolve()
    output_root = (workspace / "output" / "hero-death-runtime-assets" / skin_dir.name).resolve()
    if not output_root.is_relative_to(workspace / "output"):
        raise ValueError("死亡资源 PNG 只能写入工作区 output")
    output_root.mkdir(parents=True, exist_ok=True)
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    texture_rows: list[dict] = []
    input_hashes: dict[str, str] = {}

    hero_bundle = workspace / "romfs/Data/StreamingAssets/aa/Switch/herostatic_assets_all.bundle"
    hero_env = UnityPy.load(str(hero_bundle))
    hero_objects = {obj.path_id: obj for obj in hero_env.objects}
    for name, (path_id, width, height, texture_format) in DIRECT_TEXTURES.items():
        source = find_png(skin_dir, f"{name}.png")
        if source is None:
            continue
        texture = hero_objects[path_id].read()
        actual = (texture.m_Name, texture.m_Width, texture.m_Height,
                  int(texture.m_TextureFormat), texture.m_MipCount)
        expected = (name, width, height, texture_format, 1)
        if actual != expected:
            raise ValueError(f"死亡 Sprite Texture2D 元数据不符：{actual} != {expected}")
        image = Image.open(source).convert("RGBA")
        if name == "Hornet_death_spiders" and image.size == (37, 148):
            image = image.resize((25, 100), Image.Resampling.LANCZOS)
        if image.size != (width, height):
            raise ValueError(f"{name}：输入尺寸 {image.size}，目标 {(width, height)}")
        input_hashes[str(source)] = digest(source)
        append_texture(texture_rows, name, image, texture_format,
                       output_root / "sprite", f"death:{name}", source)

    map_source = find_png(skin_dir, "sactx-0-4096x4096-BC7-Hornet_Map-*.png")
    map_mapping = None
    if map_source is not None:
        map_bundle = (workspace / "romfs/Data/StreamingAssets/aa/Switch/"
                      "atlases_assets_assets/sprites/_atlases/hornet_map.spriteatlas.bundle")
        env, _, _, textures, sprites, render = load_atlas(map_bundle)
        target_obj = textures[MAP_TEXTURE_ID]
        target_texture = target_obj.read()
        if (target_texture.m_Name, target_texture.m_Width, target_texture.m_Height,
                int(target_texture.m_TextureFormat)) != (MAP_TEXTURE_NAME, 4096, 4096, 50):
            raise ValueError("Hornet_Map Switch Texture2D 元数据发生变化")
        target = target_texture.image.convert("RGBA").copy()
        skin = Image.open(map_source).convert("RGBA")
        if skin.size != target.size:
            raise ValueError("Hornet_Map 皮肤 PNG 与 Switch 图集尺寸不一致")
        errors = []
        selected = []
        for key, sprite_obj in sprites.items():
            data = render[key]
            if data.texture.path_id != target_obj.path_id:
                raise ValueError("Hornet_Map Sprite 引用了非目标纹理")
            box = texture_box(data.textureRect, target.height)
            errors.append(rms(target.crop(box), skin.crop(box)))
            if sprite_obj.read().m_Name == "Shade_Pin":
                selected.append((key, box))
        close = sum(error < 10 for error in errors)
        median = sorted(errors)[len(errors) // 2]
        if len(selected) != 1 or close / len(errors) < 0.99 or median > 5:
            raise ValueError(
                f"Hornet_Map 运行时 UV 映射未通过：{close}/{len(errors)}，median={median}"
            )
        key, box = selected[0]
        target.paste(skin.crop(box), box[:2])
        input_hashes[str(map_source)] = digest(map_source)
        map_row = append_texture(
            texture_rows, MAP_TEXTURE_NAME, target, 50, output_root / "sprite",
            "death-map:Shade_Pin", map_source,
        )
        map_mapping = {
            "name": "Shade_Pin",
            "spriteName": sprites[key].read().m_Name,
            "targetPathId": MAP_TEXTURE_ID,
            "targetTexture": MAP_TEXTURE_NAME,
            "renderDataKey": [str(key[0]), key[1]],
            "targetRect": list(box),
            "coordinateMatches": len(errors),
            "rmsUnder10": close,
            "medianRms": median,
            "outputPng": str(map_row["source"]),
        }

    core_source = find_png(skin_dir, "sactx-0-8192x4096-BC7-Core-*.png")
    core_result = None
    if core_source is not None:
        core_root = output_root / "sprite-atlas"
        core_result = build_atlas(
            "core", skin_dir / "Texture2D", core_root, CORE_DEATH_SPRITES
        )
        if len(core_result["generated"]) != 1:
            raise ValueError("Core 运行时图集必须只生成一张 Switch 目标纹理")
        for generated in core_result["generated"]:
            generated_path = Path(generated["png"])
            texture_rows.append({
                "name": generated["name"],
                "source": generated_path,
                "width": generated["width"],
                "height": generated["height"],
                "format": generated["format"],
                "semanticKey": "death-core:RenderDataKey-remap",
                "size": generated_path.stat().st_size,
                "sha256": generated["sha256"],
                "sourceInput": str(core_source),
            })
        input_hashes[str(core_source)] = digest(core_source)

    report = {
        "version": 1,
        "skinName": skin_dir.name,
        "textureCount": len(texture_rows),
        "spriteTextures": [
            {key: value for key, value in row.items() if key != "source"}
            | {"source": str(row["source"])}
            for row in texture_rows
        ],
        "mapMapping": map_mapping,
        "coreMapping": None if core_result is None else {
            "sourceSpriteCount": core_result["spriteCount"],
            "generated": [
                {key: value for key, value in row.items() if key != "png"}
                for row in core_result["generated"]
            ],
            "spriteMappings": [
                row for row in core_result["rows"]
                if row["name"].startswith("Hornet_death_pieces_")
            ],
            "validation": core_result["validation"],
        },
        "inputHashes": input_hashes,
        "runtimeOnly": True,
        "bundleOutput": False,
    }
    report_path = output_root / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return {"spriteTextures": texture_rows, "report": report, "reportPath": report_path}
