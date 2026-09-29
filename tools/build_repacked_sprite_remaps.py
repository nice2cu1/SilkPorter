"""为皮肤输入构建经过验证的 PC 到 Switch SpriteAtlas 纹理重排结果。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import UnityPy
from PIL import Image, ImageChops, ImageStat
from UnityPy.export.SpriteHelper import mask_sprite
from UnityPy.helpers.MeshHelper import MeshHandler


PORTER_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_CANDIDATE = PORTER_ROOT.parent
DEFAULT_WORKSPACE = (
    WORKSPACE_CANDIDATE
    if (WORKSPACE_CANDIDATE / "SilkRuntime").is_dir()
    else PORTER_ROOT
)
ROOT = Path(os.environ.get("SILK_WORKSPACE_ROOT", DEFAULT_WORKSPACE)).resolve()
PC_ROOT = ROOT / "samples" / "pc-original"
SWITCH_ROOT = ROOT / "romfs" / "Data" / "StreamingAssets" / "aa" / "Switch" / "atlases_assets_assets" / "sprites" / "_atlases"
SKIN_ROOT = ROOT / "pc-mods" / "丝之歌x星见雅皮肤2.0" / "XJY-Zycl_dhth" / "Texture2D"
OUTPUT_ROOT = ROOT / "output" / "diagnostics" / "repacked-sprite-remaps"
REPORT = ROOT / "output" / "reports" / "runtime-repacked-sprite-textures.json"

SOURCE_FILES = {
    "heart_deaths": ["sactx-0-2048x2048-DXT5_BC3-Heart_Deaths-9bc2e886.png"],
    "memory": ["sactx-0-4096x8192-BC7-Memory-17577ef2.png"],
    "peak": ["sactx-0-4096x8192-BC7-Peak-10dadac9.png"],
    "core": ["sactx-0-8192x4096-BC7-Core-d6e1b7e8.png"],
    "beast_slash": ["sactx-2-1024x2048-BC7-Beast_Slash-1bcd5d79.png"],
    "abyss_last_dive": [
        "sactx-0-2048x2048-BC7-Abyss_Last_Dive-bbfc1606.png",
        "sactx-1-2048x2048-BC7-Abyss_Last_Dive-bbfc1606.png",
        "sactx-2-2048x2048-BC7-Abyss_Last_Dive-bbfc1606.png",
        "sactx-3-2048x2048-BC7-Abyss_Last_Dive-bbfc1606.png",
    ],
}
INDEX_RE = re.compile(r"^sactx-(\d+)-")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_atlas(path: Path):
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    env = UnityPy.load(str(path))
    atlas_obj = next(obj for obj in env.objects if obj.type.name == "SpriteAtlas")
    atlas = atlas_obj.read()
    render = dict(atlas.m_RenderDataMap)
    sprites = {
        obj.read().m_RenderDataKey: obj
        for obj in env.objects
        if obj.type.name == "Sprite"
    }
    textures = {
        obj.path_id: obj
        for obj in env.objects
        if obj.type.name == "Texture2D"
    }
    return env, atlas_obj, atlas, textures, sprites, render


def texture_box(rect, height: int) -> tuple[int, int, int, int]:
    return (
        round(rect.x),
        round(height - (rect.y + rect.height)),
        round(rect.x + rect.width),
        round(height - rect.y),
    )


def rotate(image: Image.Image, settings_raw: int) -> Image.Image:
    rotation = (int(settings_raw) >> 2) & 0xF
    if rotation == 1:
        return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if rotation == 2:
        return image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if rotation == 3:
        return image.transpose(Image.Transpose.ROTATE_180)
    if rotation == 4:
        return image.transpose(Image.Transpose.ROTATE_270)
    if rotation != 0:
        raise ValueError(f"不支持的 SpritePackingRotation：{rotation}")
    return image


def inverse_rotate(image: Image.Image, settings_raw: int) -> Image.Image:
    rotation = (int(settings_raw) >> 2) & 0xF
    if rotation == 4:
        return image.transpose(Image.Transpose.ROTATE_90)
    return rotate(image, settings_raw)


def texture_index(texture) -> int:
    match = INDEX_RE.match(texture.m_Name)
    if match is None:
        raise ValueError(f"无法从 {texture.m_Name} 确定 sactx 索引")
    return int(match.group(1))


def rms(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a.convert("RGBA"), b.convert("RGBA"))
    values = ImageStat.Stat(diff).rms
    return (sum(value * value for value in values) / 4) ** 0.5


def validate_reopened(
    destination: Path,
    expected_display: dict,
    expected_alphas: dict | None = None,
) -> dict[str, object]:
    _env, _atlas_obj, _atlas, _textures, sprites, _render = load_atlas(destination)
    values = []
    alpha_values = []
    for key, expected in expected_display.items():
        sprite = sprites[key].read()
        if sprite.image.size != expected.size:
            raise ValueError(f"{sprite.m_Name}：重新打开后的尺寸 {sprite.image.size} != {expected.size}")
        got = sprite.image.convert("RGBA")
        mask = got.getchannel("A")
        visible_got = Image.composite(got, Image.new("RGBA", got.size), mask)
        visible_expected = Image.composite(expected, Image.new("RGBA", expected.size), mask)
        values.append(rms(visible_got, visible_expected))
        expected_alpha = (expected_alphas or {}).get(key)
        if expected_alpha is not None:
            expected_alpha = expected_alpha.convert("L")
            if expected_alpha.size != got.size:
                raise ValueError(f"{sprite.m_Name}：alpha 期望尺寸与重新打开的 Sprite 不一致")
            actual_alpha = got.getchannel("A")
            alpha_diff = ImageChops.difference(actual_alpha, expected_alpha)
            alpha_rms = ImageStat.Stat(alpha_diff).rms[0]
            hard_opaque_missing = sum(
                expected_value >= 128 and actual_value < 16
                for expected_value, actual_value in zip(
                    expected_alpha.getdata(), actual_alpha.getdata()
                )
            )
            alpha_values.append({
                "name": sprite.m_Name,
                "alphaRms": alpha_rms,
                "expectedVisiblePixels": sum(value > 0 for value in expected_alpha.getdata()),
                "actualVisiblePixels": sum(value > 0 for value in actual_alpha.getdata()),
                "hardOpaqueMissingPixels": hard_opaque_missing,
            })
    return {
        "spriteCount": len(values),
        "maskedRmsMean": sum(values) / len(values) if values else 0,
        "maskedRmsMax": max(values) if values else 0,
        "maskedRmsUnder10": sum(value < 10 for value in values),
        "alphaValidation": alpha_values,
        "alphaRmsMax": max((row["alphaRms"] for row in alpha_values), default=0),
        "hardOpaqueMissingPixels": sum(row["hardOpaqueMissingPixels"] for row in alpha_values),
    }


def load_source_images(name: str, skin_root: Path = SKIN_ROOT) -> dict[int, Image.Image]:
    result: dict[int, Image.Image] = {}
    for filename in SOURCE_FILES[name]:
        path = skin_root / filename
        if not path.is_file():
            raise SystemExit(f"皮肤源文件不存在：{path}")
        match = INDEX_RE.match(filename)
        if match is None:
            raise SystemExit(f"皮肤源文件没有 sactx 索引：{path}")
        result[int(match.group(1))] = Image.open(path).convert("RGBA")
    return result


def tight_mesh_mask(sprite_obj):
    sprite = sprite_obj.read()
    mesh = MeshHandler(sprite.m_RD, sprite.object_reader.version)
    mesh.process()
    if not mesh.m_Vertices or not any(mesh.get_triangles()):
        raise ValueError(f"{sprite.m_Name}：Sprite 网格缺少顶点或三角形")
    opaque = Image.new("RGBA", sprite.image.size, (255, 255, 255, 255))
    coverage = mask_sprite(sprite, mesh, opaque).getchannel("A")
    coverage = coverage.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    actual_alpha = sprite.image.convert("RGBA").getchannel("A")
    outside = ImageChops.multiply(actual_alpha, ImageChops.invert(coverage))
    if outside.getbbox() is not None:
        raise ValueError(f"{sprite.m_Name}：解码 Sprite alpha 超出网格范围")
    return sprite, mesh, coverage


def build_atlas(
    name: str,
    skin_root: Path = SKIN_ROOT,
    output_root: Path = OUTPUT_ROOT,
    sprite_names: set[str] | None = None,
) -> dict[str, object]:
    pc_path = PC_ROOT / f"{name}.spriteatlas.bundle"
    switch_path = SWITCH_ROOT / f"{name}.spriteatlas.bundle"
    if not pc_path.is_file() or not switch_path.is_file():
        raise SystemExit(f"缺少 {name} 对应的 PC/Switch Bundle")
    _pc_env, _pc_atlas_obj, _pc_atlas, _pc_textures, pc_sprites, pc_render = load_atlas(pc_path)
    _sw_env, _sw_atlas_obj, _sw_atlas, sw_textures, sw_sprites, sw_render = load_atlas(switch_path)
    if set(pc_sprites) != set(sw_sprites) or set(pc_render) != set(sw_render):
        raise ValueError(f"{name}：PC/Switch 的 RenderDataKey 集合不同")

    source_images = load_source_images(name, Path(skin_root).resolve())
    target_canvases: dict[int, Image.Image] = {}
    target_objects: dict[int, object] = {}
    target_names: dict[int, str] = {}
    for path_id, obj in sw_textures.items():
        texture = obj.read()
        target_canvases[path_id] = texture.image.convert("RGBA").copy()
        target_objects[path_id] = obj
        target_names[path_id] = texture.m_Name

    expected_display: dict[object, Image.Image] = {}
    expected_masks: dict[object, Image.Image] = {}
    alpha_validation: dict[object, Image.Image] = {}
    validation_targets: dict[object, tuple[int, tuple[int, int, int, int], int]] = {}
    rows: list[dict[str, object]] = []
    source_counts: dict[int, int] = {}
    skipped_source_counts: dict[int, int] = {}
    target_counts: dict[str, int] = {}
    for key in sorted(pc_sprites, key=str):
        pc_data = pc_render[key]
        sw_data = sw_render[key]
        pc_sprite = pc_sprites[key].read()
        sw_sprite = sw_sprites[key].read()
        if pc_sprite.m_Name != sw_sprite.m_Name:
            raise ValueError(f"{name}：RenderDataKey {key} 的名称不匹配")
        if sprite_names is not None and sw_sprite.m_Name not in sprite_names:
            continue
        pc_texture = pc_data.texture.deref_parse_as_object()
        pc_index = texture_index(pc_texture)
        source_image = source_images.get(pc_index)
        if source_image is None:
            skipped_source_counts[pc_index] = skipped_source_counts.get(pc_index, 0) + 1
            continue
        source_counts[pc_index] = source_counts.get(pc_index, 0) + 1
        target_texture = sw_data.texture.deref_parse_as_object()
        target_path_id = sw_data.texture.path_id
        if target_path_id not in target_canvases:
            raise ValueError(f"{name}：目标纹理不在 Bundle 中：{target_texture.m_Name}")
        target_counts[target_texture.m_Name] = target_counts.get(target_texture.m_Name, 0) + 1

        pc_box = texture_box(pc_data.textureRect, source_image.height)
        source_display = rotate(source_image.crop(pc_box), pc_data.settingsRaw)
        target_image = target_canvases[target_path_id]
        sw_box = texture_box(sw_data.textureRect, target_image.height)
        target_size = (sw_box[2] - sw_box[0], sw_box[3] - sw_box[1])
        if target_size[0] <= 0 or target_size[1] <= 0:
            raise ValueError(f"{name}/{sw_sprite.m_Name}：目标区域无效：{sw_box}")
        if sw_sprite.image.size != target_size:
            raise ValueError(f"{name}/{sw_sprite.m_Name}：Sprite.image 尺寸不匹配")
        if source_display.size != target_size:
            source_display = source_display.resize(target_size, Image.Resampling.LANCZOS)
        expected_display[key] = source_display.copy()
        source_display_copy = source_display.copy()
        if sw_sprite.m_Name == "Hornet_death_pieces_0000s_0007_back_thread":
            pc_mesh_sprite, pc_mesh, pc_mask = tight_mesh_mask(pc_sprites[key])
            sw_mesh_sprite, sw_mesh, sw_mask = tight_mesh_mask(sw_sprites[key])
            if (pc_mesh.m_Vertices != sw_mesh.m_Vertices or
                    pc_mesh_sprite.m_PixelsToUnits != sw_mesh_sprite.m_PixelsToUnits or
                    pc_mask.size != sw_mask.size or pc_mask.tobytes() != sw_mask.tobytes()):
                raise ValueError(f"{name}/{sw_sprite.m_Name}：PC/Switch Sprite 网格不一致")
            target_mask_display = sw_mask
            expected_alpha = ImageChops.multiply(
                source_display_copy.getchannel("A"), target_mask_display
            )
            alpha_validation[key] = expected_alpha
            rows_mask_method = "SpriteRenderData tight-mesh polygon"
        elif sprite_names is not None:
            _sprite, _mesh, target_mask_display = tight_mesh_mask(sw_sprites[key])
            rows_mask_method = "SpriteRenderData tight-mesh polygon"
        else:
            target_mask_display = sw_sprite.image.getchannel("A")
            rows_mask_method = "original Switch Sprite alpha"
        target_crop = inverse_rotate(source_display_copy, sw_data.settingsRaw)
        target_mask = inverse_rotate(target_mask_display, sw_data.settingsRaw)
        if target_crop.size != target_mask.size:
            raise ValueError(f"{name}/{sw_sprite.m_Name}：补丁与遮罩尺寸不匹配")
        # Tight-packed sprites may share rectangular bounds. Copy all RGBA
        # channels under the target geometry, never its entire bounding box.
        # Using geometry rather than old alpha permits newly visible skin pixels.
        target_canvases[target_path_id].paste(
            target_crop, (sw_box[0], sw_box[1]), target_mask
        )
        expected_masks[key] = target_mask_display
        validation_targets[key] = (target_path_id, sw_box, int(sw_data.settingsRaw))
        rows.append({
            "name": sw_sprite.m_Name,
            "renderDataKey": [str(key[0]), key[1]],
            "sourceTexture": pc_texture.m_Name,
            "sourceTextureSize": [pc_texture.m_Width, pc_texture.m_Height],
            "targetTexture": target_texture.m_Name,
            "targetTextureSize": [target_texture.m_Width, target_texture.m_Height],
            "sourceRect": list(pc_box),
            "targetRect": list(sw_box),
            "sourceSettingsRaw": int(pc_data.settingsRaw),
            "targetSettingsRaw": int(sw_data.settingsRaw),
            "resized": list(source_display.size),
            "maskMethod": rows_mask_method,
        })

    if sprite_names is not None:
        boxes_by_texture: dict[int, list[tuple[int, int, int, int, object]]] = {}
        for key, (path_id, box, _settings_raw) in validation_targets.items():
            boxes_by_texture.setdefault(path_id, []).append((*box, key))
        for boxes in boxes_by_texture.values():
            for index, (left, top, right, bottom, key) in enumerate(boxes):
                for other_left, other_top, other_right, other_bottom, other_key in boxes[index + 1:]:
                    if (left < other_right and other_left < right and
                            top < other_bottom and other_top < bottom):
                        raise ValueError(
                            f"{name}：Sprite 目标矩形重叠，不能安全生成整图 PNG："
                            f"{sw_sprites[key].read().m_Name} 与 "
                            f"{sw_sprites[other_key].read().m_Name}"
                        )

    output_dir = Path(output_root).resolve() / name
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = []
    for path_id, target_name in sorted(target_names.items(), key=lambda item: item[1].casefold()):
        count = target_counts.get(target_name, 0)
        if count == 0:
            continue
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", target_name)
        output_path = output_dir / f"{safe_name}.png"
        target_canvases[path_id].save(output_path)
        generated.append({
            "pathId": path_id,
            "name": target_name,
            "png": str(output_path),
            "width": target_canvases[path_id].width,
            "height": target_canvases[path_id].height,
            "format": int(target_objects[path_id].read().m_TextureFormat),
            "patchedSpriteCount": count,
            "sha256": sha256(output_path),
        })
    if not generated:
        raise ValueError(f"{name}：没有目标纹理接收到皮肤补丁")
    if sprite_names is not None:
        found_names = {str(row["name"]) for row in rows}
        missing_names = sprite_names - found_names
        if missing_names:
            raise ValueError(
                f"{name}：缺少要求的 Sprite 重排：{', '.join(sorted(missing_names))}"
            )

    color_errors = []
    alpha_errors = []
    original_canvases = {
        path_id: obj.read().image.convert("RGBA").copy()
        for path_id, obj in sw_textures.items()
    }
    region_masks = {
        path_id: Image.new("L", canvas.size, 0)
        for path_id, canvas in target_canvases.items()
    }
    for key, expected in expected_display.items():
        path_id, box, settings_raw = validation_targets[key]
        if sprite_names is None:
            region_masks[path_id].paste(255, box)
        else:
            packed_mask = inverse_rotate(expected_masks[key], settings_raw)
            mask_canvas = Image.new("L", region_masks[path_id].size, 0)
            mask_canvas.paste(packed_mask, box[:2])
            region_masks[path_id] = ImageChops.lighter(region_masks[path_id], mask_canvas)
        got = rotate(target_canvases[path_id].crop(box), settings_raw).convert("RGBA")
        mask = expected_masks[key]
        visible_got = Image.composite(got, Image.new("RGBA", got.size), mask)
        visible_expected = Image.composite(expected, Image.new("RGBA", expected.size), mask)
        color_error = rms(visible_got, visible_expected)
        color_errors.append(color_error)
        if color_error > 10:
            raise ValueError(f"{name}/{sw_sprites[key].read().m_Name}：运行时图集 PNG 重映射 RMS 超限 {color_error}")
        if key in alpha_validation:
            actual_alpha = ImageChops.multiply(got.getchannel("A"), mask)
            expected_alpha = alpha_validation[key]
            alpha_error = ImageStat.Stat(ImageChops.difference(actual_alpha, expected_alpha)).rms[0]
            alpha_errors.append(alpha_error)
            hard_missing = sum(
                expected_value >= 128 and actual_value < 16
                for expected_value, actual_value in zip(expected_alpha.getdata(), actual_alpha.getdata())
            )
            if alpha_error > 10 or hard_missing:
                raise ValueError(f"{name}/{sw_sprites[key].read().m_Name}：运行时图集 alpha 重映射失败 RMS={alpha_error} missing={hard_missing}")
    unchanged_pixel_counts = {}
    for path_id, canvas in target_canvases.items():
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", target_names[path_id])
        saved = Image.open(output_dir / f"{safe_name}.png").convert("RGBA")
        if saved.size != canvas.size or ImageChops.difference(saved, canvas).getbbox(alpha_only=False) is not None:
            raise ValueError(f"{name}/{target_names[path_id]}：保存的运行时 PNG 与构建图集不一致")
        outside = ImageChops.invert(region_masks[path_id])
        changed = ImageChops.difference(original_canvases[path_id], canvas)
        outside_changes = Image.composite(changed, Image.new("RGBA", canvas.size), outside)
        if outside_changes.getbbox(alpha_only=False) is not None:
            raise ValueError(f"{name}/{target_names[path_id]}：图集目标覆盖范围以外像素发生变化")
        unchanged_pixel_counts[target_names[path_id]] = (
            canvas.width * canvas.height - sum(value > 0 for value in region_masks[path_id].getdata())
        )
    non_target_checked = 0
    unresolved_checked = 0
    if sprite_names is not None:
        before_sprites = {
            key: obj.read().image.convert("RGBA").copy()
            for key, obj in sw_sprites.items() if key not in expected_display
        }
        # Re-render through the original meshes using only a decoded-image
        # cache override. No serialized object or AssetBundle is written.
        flipped = {path_id: canvas.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                   for path_id, canvas in target_canvases.items()}
        for obj in sw_sprites.values():
            obj.assets_file._cache.update(flipped)
        for key, before in before_sprites.items():
            after = sw_sprites[key].read().image.convert("RGBA")
            if ImageChops.difference(before, after).getbbox(alpha_only=False) is not None:
                raise ValueError(f"{name}：非目标 Sprite 被更改：{sw_sprites[key].read().m_Name}")
            non_target_checked += 1
        for key in set(sw_render) - set(sw_sprites):
            data = sw_render[key]
            path_id = data.texture.path_id
            canvas = target_canvases[path_id]
            box = texture_box(data.textureRect, canvas.height)
            difference = ImageChops.difference(original_canvases[path_id].crop(box), canvas.crop(box))
            if difference.getbbox(alpha_only=False) is not None:
                raise ValueError(f"{name}：没有 Sprite 对象的 RenderData 区域被更改：{key}")
            unresolved_checked += 1
    validation = {
        "spriteCount": len(color_errors),
        "maskedRmsMean": sum(color_errors) / len(color_errors) if color_errors else 0,
        "maskedRmsMax": max(color_errors, default=0),
        "maskedRmsUnder10": sum(value < 10 for value in color_errors),
        "alphaRmsMax": max(alpha_errors, default=0),
        "nonTargetPixelsUnchanged": True,
        "nonTargetSpritesChecked": non_target_checked,
        "renderEntriesWithoutSpriteChecked": unresolved_checked,
        "regionValidation": "tight mesh geometry" if sprite_names is not None else "rectangles",
        "pngRoundTripExact": True,
        "unchangedPixelCountByTexture": unchanged_pixel_counts,
    }
    print(
        f"{name}：已生成运行时 Sprite 纹理={len(rows)}，目标纹理={len(generated)}，"
        f"RMS 均值={validation['maskedRmsMean']:.3f}，低于 10={validation['maskedRmsUnder10']}，"
        f"源纹理计数={source_counts}，跳过={skipped_source_counts}"
    )
    return {
        "name": name,
        "pcBundle": str(pc_path),
        "switchBundle": str(switch_path),
        "sourceTextures": [str(Path(skin_root).resolve() / item) for item in SOURCE_FILES[name]],
        "spriteCount": len(rows),
        "generated": generated,
        "sourceSpriteCounts": source_counts,
        "skippedSourceSpriteCounts": skipped_source_counts,
        "targetSpriteCounts": target_counts,
        "rows": rows,
        "validation": validation,
    }


def main() -> int:
    results = [build_atlas(name) for name in SOURCE_FILES]
    entries = []
    for result in results:
        for generated in result["generated"]:
            entries.append({
                "semanticKey": result["name"],
                "targetName": generated["name"],
                "sourcePng": generated["png"],
                "dimensions": [generated["width"], generated["height"]],
                "format": generated["format"],
                "sourceKind": "经 RenderDataKey 映射生成的运行时 PNG",
                "evidenceReport": str(REPORT),
                "validation": result["validation"],
            })
    report = {
        "version": 3,
        "description": "根据 PC/Switch SpriteAtlas RenderDataKey 元数据生成与 SilkRuntime SpriteTexture Hook 配套的 PNG；不重建或输出 AssetBundle。",
        "atlases": results,
        "entries": entries,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"报告：{REPORT}")
    print(f"生成的目标纹理：{len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
