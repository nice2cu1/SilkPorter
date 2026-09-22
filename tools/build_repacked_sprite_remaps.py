"""为皮肤输入构建经过验证的 PC 到 Switch SpriteAtlas 纹理重排结果。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import UnityPy
from PIL import Image, ImageChops, ImageStat

from rebuild_repacked_bundle import patch_bundle


PORTER_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_CANDIDATE = PORTER_ROOT.parent
DEFAULT_WORKSPACE = (
    WORKSPACE_CANDIDATE
    if (WORKSPACE_CANDIDATE / "SilkRuntime").is_dir()
    else PORTER_ROOT
)
ROOT = Path(os.environ.get("SILK_WORKSPACE_ROOT", DEFAULT_WORKSPACE)).resolve()
TITLE_ID = "010013C00E930000"
PC_ROOT = ROOT / "samples" / "pc-original"
SWITCH_ROOT = ROOT / "romfs" / "Data" / "StreamingAssets" / "aa" / "Switch" / "atlases_assets_assets" / "sprites" / "_atlases"
SKIN_ROOT = ROOT / "pc-mods" / "丝之歌x星见雅皮肤2.0" / "XJY-Zycl_dhth" / "Texture2D"
OUTPUT_ROOT = ROOT / "output" / "diagnostics" / "repacked-sprite-remaps"
REPORT = ROOT / "output" / "reports" / "runtime-repacked-sprite-textures.json"
STATIC_ROOT = ROOT / "dist" / "diagnostics" / "repacked-sprite-uv-remaps"

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


def validate_reopened(destination: Path, expected_display: dict) -> dict[str, object]:
    _env, _atlas_obj, _atlas, _textures, sprites, _render = load_atlas(destination)
    values = []
    for key, expected in expected_display.items():
        sprite = sprites[key].read()
        if sprite.image.size != expected.size:
            raise ValueError(f"{sprite.m_Name}：重新打开后的尺寸 {sprite.image.size} != {expected.size}")
        got = sprite.image.convert("RGBA")
        mask = got.getchannel("A")
        visible_got = Image.composite(got, Image.new("RGBA", got.size), mask)
        visible_expected = Image.composite(expected, Image.new("RGBA", expected.size), mask)
        values.append(rms(visible_got, visible_expected))
    return {
        "spriteCount": len(values),
        "maskedRmsMean": sum(values) / len(values) if values else 0,
        "maskedRmsMax": max(values) if values else 0,
        "maskedRmsUnder10": sum(value < 10 for value in values),
    }


def load_source_images(name: str) -> dict[int, Image.Image]:
    result: dict[int, Image.Image] = {}
    for filename in SOURCE_FILES[name]:
        path = SKIN_ROOT / filename
        if not path.is_file():
            raise SystemExit(f"皮肤源文件不存在：{path}")
        match = INDEX_RE.match(filename)
        if match is None:
            raise SystemExit(f"皮肤源文件没有 sactx 索引：{path}")
        result[int(match.group(1))] = Image.open(path).convert("RGBA")
    return result


def build_atlas(name: str) -> dict[str, object]:
    pc_path = PC_ROOT / f"{name}.spriteatlas.bundle"
    switch_path = SWITCH_ROOT / f"{name}.spriteatlas.bundle"
    if not pc_path.is_file() or not switch_path.is_file():
        raise SystemExit(f"缺少 {name} 对应的 PC/Switch Bundle")
    _pc_env, _pc_atlas_obj, _pc_atlas, _pc_textures, pc_sprites, pc_render = load_atlas(pc_path)
    _sw_env, _sw_atlas_obj, _sw_atlas, sw_textures, sw_sprites, sw_render = load_atlas(switch_path)
    if set(pc_sprites) != set(sw_sprites) or set(pc_render) != set(sw_render):
        raise ValueError(f"{name}：PC/Switch 的 RenderDataKey 集合不同")

    source_images = load_source_images(name)
    target_canvases: dict[int, Image.Image] = {}
    target_objects: dict[int, object] = {}
    target_names: dict[int, str] = {}
    for path_id, obj in sw_textures.items():
        texture = obj.read()
        target_canvases[path_id] = texture.image.convert("RGBA").copy()
        target_objects[path_id] = obj
        target_names[path_id] = texture.m_Name

    expected_display: dict[object, Image.Image] = {}
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
        target_crop = inverse_rotate(source_display, sw_data.settingsRaw)
        target_mask = inverse_rotate(sw_sprite.image.getchannel("A"), sw_data.settingsRaw)
        if target_crop.size != target_mask.size:
            raise ValueError(f"{name}/{sw_sprite.m_Name}：补丁与遮罩尺寸不匹配")
        target_canvases[target_path_id].paste(target_crop, (sw_box[0], sw_box[1]), target_mask)
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
        })

    output_dir = OUTPUT_ROOT / name
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

    static_result = patch_bundle(
        switch_path,
        [
            {"pathId": item["pathId"], "png": item["png"], "source": "经过验证的逐 Sprite UV 区域重排", "resize": False}
            for item in generated
        ],
        switch_root=ROOT / "romfs" / "Data" / "StreamingAssets" / "aa" / "Switch",
        destination_root=STATIC_ROOT / "atmosphere" / "contents" / TITLE_ID / "romfs" / "Data" / "StreamingAssets" / "aa" / "Switch",
    )
    validation = validate_reopened(Path(static_result["destination"]), expected_display)
    validation["nonTargetResourceBytesUnchanged"] = bool(static_result["nonTargetResourceBytesUnchanged"])
    validation["bundleReopenVerified"] = True
    print(
        f"{name}：已补丁 Sprite={len(rows)}，目标纹理={len(generated)}，"
        f"RMS 均值={validation['maskedRmsMean']:.3f}，低于 10={validation['maskedRmsUnder10']}，"
        f"源纹理计数={source_counts}，跳过={skipped_source_counts}"
    )
    return {
        "name": name,
        "pcBundle": str(pc_path),
        "switchBundle": str(switch_path),
        "sourceTextures": [str(SKIN_ROOT / item) for item in SOURCE_FILES[name]],
        "spriteCount": len(rows),
        "generated": generated,
        "sourceSpriteCounts": source_counts,
        "skippedSourceSpriteCounts": skipped_source_counts,
        "targetSpriteCounts": target_counts,
        "rows": rows,
        "bundle": static_result,
        "validation": validation,
    }


def main() -> int:
    results = [build_atlas(name) for name in SOURCE_FILES]
    entries = [
        {
            "semanticKey": "0|4096x4096|hornet",
            "targetName": "sactx-0-4096x4096-ASTC 4x4-Hornet-5e00c913",
            "sourcePng": str(ROOT / "output" / "diagnostics" / "hornet-sprite-uv-remap.png"),
            "dimensions": [4096, 4096],
            "format": 48,
            "sourceKind": "经过验证的离线逐 Sprite UV 区域重排",
            "evidenceReport": str(ROOT / "output" / "reports" / "hornet-sprite-uv-remap-build.json"),
            "validation": {
                "spriteCount": 282,
                "maskedRmsMean": 3.3142935003310754,
                "maskedRmsMax": 11.257624769395406,
                "maskedRmsUnder10": 280,
                "bundleReopenVerified": True,
                "nonTargetResourceBytesUnchanged": True,
            },
        },
        {
            "semanticKey": "0|2048x2048|tools",
            "targetName": "sactx-0-2048x1024-ASTC 6x6-Tools-a7143009",
            "sourcePng": str(ROOT / "output" / "diagnostics" / "tools-sprite-uv-remap.png"),
            "dimensions": [2048, 1024],
            "format": 50,
            "sourceKind": "经过验证的离线逐 Sprite UV 区域重排",
            "evidenceReport": str(ROOT / "output" / "reports" / "test-o-tools-package.json"),
            "validation": {
                "spriteCount": 97,
                "maskedRmsMean": 3.5707125140918947,
                "maskedRmsMax": 10.728267204441181,
                "maskedRmsUnder10": 96,
                "bundleReopenVerified": True,
                "nonTargetResourceBytesUnchanged": True,
            },
        },
    ]
    for result in results:
        for generated in result["generated"]:
            entries.append({
                "semanticKey": result["name"],
                "targetName": generated["name"],
                "sourcePng": generated["png"],
                "dimensions": [generated["width"], generated["height"]],
                "format": generated["format"],
                "sourceKind": "经过验证的离线逐 Sprite UV 区域重排",
                "evidenceReport": str(REPORT),
                "validation": result["validation"],
            })
    report = {
        "version": 2,
        "titleId": TITLE_ID,
        "description": "根据 PC/Switch SpriteAtlas RenderDataKey 元数据生成的经过验证的离线逐 Sprite UV 重排；运行时仍使用整图替换。",
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
