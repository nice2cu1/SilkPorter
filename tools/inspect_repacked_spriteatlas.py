"""比较 PC 与 Switch SpriteAtlas 元数据，检查需要重排的图集候选。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import UnityPy


PORTER_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_CANDIDATE = PORTER_ROOT.parent
DEFAULT_WORKSPACE = (
    WORKSPACE_CANDIDATE
    if (WORKSPACE_CANDIDATE / "SilkRuntime").is_dir()
    else PORTER_ROOT
)
ROOT = Path(os.environ.get("SILK_WORKSPACE_ROOT", DEFAULT_WORKSPACE)).resolve()
DEFAULT_PC_ROOT = ROOT / "samples" / "pc-original"
DEFAULT_SWITCH_ROOT = (
    ROOT
    / "romfs"
    / "Data"
    / "StreamingAssets"
    / "aa"
    / "Switch"
    / "atlases_assets_assets"
    / "sprites"
    / "_atlases"
)
DEFAULT_OUTPUT = ROOT / "output" / "reports" / "repacked-spriteatlas-metadata.json"
DEFAULT_NAMES = (
    "heart_deaths",
    "memory",
    "peak",
    "core",
    "beast_slash",
    "abyss_last_dive",
)


def inspect(path: Path) -> dict[str, object]:
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    env = UnityPy.load(str(path))
    counts: dict[str, int] = {}
    textures: list[dict[str, object]] = []
    atlas_entries: list[dict[str, object]] = []
    sprites: dict[str, dict[str, object]] = {}
    render_data: dict[str, object] = {}
    sprite_objects: list[object] = []
    for obj in env.objects:
        type_name = obj.type.name
        counts[type_name] = counts.get(type_name, 0) + 1
        if type_name == "Texture2D":
            texture = obj.read()
            textures.append({
                "name": texture.m_Name,
                "width": texture.m_Width,
                "height": texture.m_Height,
                "format": int(texture.m_TextureFormat),
            })
        elif type_name == "SpriteAtlas":
            atlas = obj.read()
            render_data = {
                str(key): value for key, value in dict(atlas.m_RenderDataMap).items()
            }
            atlas_entries.append({
                "renderDataCount": len(atlas.m_RenderDataMap),
                "packedSpriteCount": len(atlas.m_PackedSprites),
                "renderDataKeys": [str(key) for key in atlas.m_RenderDataMap],
            })
        elif type_name == "Sprite":
            sprite_objects.append(obj)
    for obj in sprite_objects:
        sprite = obj.read()
        key = str(sprite.m_RenderDataKey)
        entry: dict[str, object] = {
            "name": sprite.m_Name,
            "renderDataKey": key,
        }
        data = render_data.get(key)
        if data is not None:
            texture = data.texture.deref_parse_as_object()
            entry["texture"] = {
                "name": texture.m_Name,
                "width": texture.m_Width,
                "height": texture.m_Height,
            }
            entry["textureRect"] = [
                float(data.textureRect.x),
                float(data.textureRect.y),
                float(data.textureRect.width),
                float(data.textureRect.height),
            ]
            entry["settingsRaw"] = int(data.settingsRaw)
        sprites[key] = entry
    return {
        "path": str(path),
        "size": path.stat().st_size,
        "counts": counts,
        "textures": textures,
        "atlases": atlas_entries,
        "sprites": sprites,
    }


def compare(name: str, pc_root: Path, switch_root: Path) -> dict[str, object]:
    pc_path = pc_root / f"{name}.spriteatlas.bundle"
    switch_path = switch_root / f"{name}.spriteatlas.bundle"
    if not pc_path.is_file():
        raise SystemExit(f"PC SpriteAtlas 不存在：{pc_path}")
    if not switch_path.is_file():
        raise SystemExit(f"Switch SpriteAtlas 不存在：{switch_path}")
    pc = inspect(pc_path)
    switch = inspect(switch_path)
    pc_names = {item["name"] for item in pc["sprites"].values()}
    switch_names = {item["name"] for item in switch["sprites"].values()}
    pc_keys = set(pc["sprites"])
    switch_keys = set(switch["sprites"])
    shared_keys = pc_keys & switch_keys
    key_name_mismatches = [
        {
            "renderDataKey": key,
            "pcName": pc["sprites"][key]["name"],
            "switchName": switch["sprites"][key]["name"],
        }
        for key in sorted(shared_keys)
        if pc["sprites"][key]["name"] != switch["sprites"][key]["name"]
    ]
    return {
        "name": name,
        "pc": pc,
        "switch": switch,
        "spriteNameIntersectionCount": len(pc_names & switch_names),
        "renderDataKeyIntersectionCount": len(shared_keys),
        "pcOnlyRenderDataKeyCount": len(pc_keys - switch_keys),
        "switchOnlyRenderDataKeyCount": len(switch_keys - pc_keys),
        "renderDataKeyNameMismatches": key_name_mismatches,
        "pcOnlySpriteNames": sorted(pc_names - switch_names),
        "switchOnlySpriteNames": sorted(switch_names - pc_names),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pc-root", type=Path, default=DEFAULT_PC_ROOT)
    parser.add_argument("--switch-root", type=Path, default=DEFAULT_SWITCH_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("names", nargs="*", default=list(DEFAULT_NAMES))
    args = parser.parse_args()
    result = {
        "version": 1,
        "pcRoot": str(args.pc_root),
        "switchRoot": str(args.switch_root),
        "atlases": [compare(name, args.pc_root, args.switch_root) for name in args.names],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for atlas in result["atlases"]:
        pc = atlas["pc"]
        switch = atlas["switch"]
        print(
            f"{atlas['name']}："
            f"PC Sprite={len(pc['sprites'])}，Switch Sprite={len(switch['sprites'])}，"
            f"名称交集={atlas['spriteNameIntersectionCount']}，"
            f"RenderDataKey 交集={atlas['renderDataKeyIntersectionCount']}，"
            f"键名不匹配={len(atlas['renderDataKeyNameMismatches'])}，"
            f"PC 纹理={pc['textures']}，Switch 纹理={switch['textures']}"
        )
    print(f"报告：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
