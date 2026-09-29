"""Validate the runtime-only XJY-Zycl_dhth folder output."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "SilkPorter" / "output"
EXPECTED_SKIN = "XJY-Zycl_dhth"
REQUIRED_SPRITES = {
    "Hornet_death_pieces_0000s_0001_6",
    "Hornet_death_cocoon_particle_chunks",
    "Hornet_death_spiders",
}
BACK_THREAD = "Hornet_death_pieces_0000s_0007_back_thread"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if (len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n"
            or data[12:16] != b"IHDR"):
        raise ValueError(f"不是有效 PNG：{path}")
    return struct.unpack_from(">II", data, 16)


def main() -> int:
    manifest_path = OUTPUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    skin_name = manifest["skinName"]
    if skin_name != EXPECTED_SKIN:
        raise ValueError(f"本次皮肤应输出 {EXPECTED_SKIN}，实际为 {skin_name}")
    if skin_name != Path(manifest["skinInputDirectory"]).name:
        raise ValueError("输出皮肤名称与 PC 输入目录不一致")
    if manifest.get("staticBundles") or manifest.get("staticBundleCount", 0):
        raise ValueError("运行时清单不应包含静态 Bundle 项")

    skin_base = (OUTPUT / "atmosphere/contents/010013C00E930000/romfs/"
                 "SilkModLoader/Mods/Skin")
    skin_root = skin_base / skin_name
    if {path.name for path in skin_base.iterdir() if path.is_dir()} != {skin_name}:
        raise ValueError("输出含多余皮肤目录")

    collection_records = []
    for collection, metadata in manifest.get("collections", {}).items():
        for atlas_name, atlas in metadata.get("atlases", {}).items():
            path = (skin_root / collection / f"{atlas_name}.png").resolve()
            if not path.is_relative_to(skin_root.resolve()) or not path.is_file():
                raise ValueError(f"TK2D Atlas PNG 缺失：{collection}/{atlas_name}")
            dimensions = png_dimensions(path)
            if dimensions != tuple(atlas["dimensions"]):
                raise ValueError(f"{collection}/{atlas_name} 尺寸与清单不一致")
            if digest(path) != str(atlas["sha256"]).upper():
                raise ValueError(f"{collection}/{atlas_name} 哈希与清单不一致")
            collection_records.append({
                "collection": collection,
                "atlas": atlas_name,
                "dimensions": list(dimensions),
                "sha256": digest(path),
            })

    exefs = OUTPUT / "atmosphere/contents/010013C00E930000/exefs"
    loader_path = exefs / "subsdk9"
    npdm_path = exefs / "main.npdm"
    patch_path = (OUTPUT / "atmosphere/exefs_patches/SilkModLoader"
                  / f"{manifest['gameBuildId']}.ips")
    for path in (loader_path, npdm_path, patch_path):
        if not path.is_file():
            raise ValueError(f"运行时文件缺失：{path}")
    loader_hash = digest(loader_path)
    npdm_hash = digest(npdm_path)
    patch_hash = digest(patch_path)
    if loader_hash != str(manifest["loaderNsoSha256"]).upper():
        raise ValueError("输出 subsdk9 哈希与清单不一致")
    if loader_path.stat().st_size != int(manifest["loaderNsoSize"]):
        raise ValueError("输出 subsdk9 大小与清单不一致")
    if npdm_hash != str(manifest["npdm"]["sha256"]).upper():
        raise ValueError("输出 main.npdm 哈希与清单不一致")
    if patch_hash != str(manifest["patchSha256"]).upper():
        raise ValueError("输出 IPS32 补丁哈希与清单不一致")

    records = []
    names = set()
    for kind in ("standaloneTextures", "spriteTextures"):
        for row in manifest.get(kind, []):
            name = str(row["name"])
            if name in names:
                raise ValueError(f"重复的运行时纹理记录：{name}")
            names.add(name)
            path = (skin_root / row["file"]).resolve()
            if not path.is_relative_to(skin_root.resolve()) or not path.is_file():
                raise ValueError(f"清单 PNG 路径无效：{row['file']}")
            dimensions = png_dimensions(path)
            expected = (int(row["width"]), int(row["height"]))
            if dimensions != expected:
                raise ValueError(f"{name} PNG 尺寸 {dimensions} != 清单 {expected}")
            if kind == "spriteTextures" and int(row["format"]) not in {4, 12, 48, 50}:
                raise ValueError(f"{name} 使用尚未支持的 Unity 格式：{row['format']}")
            records.append({
                "name": name,
                "kind": kind,
                "file": path.relative_to(OUTPUT).as_posix(),
                "dimensions": list(dimensions),
                "format": row.get("format"),
                "sha256": digest(path),
            })

    sprite_names = {row["name"] for row in manifest.get("spriteTextures", [])}
    missing = REQUIRED_SPRITES - sprite_names
    if missing:
        raise ValueError("缺少死亡茧运行时 Sprite PNG：" + ", ".join(sorted(missing)))
    core = manifest.get("runtimeDeathAssetMapping", {}).get("core")
    mapped_sprites = core.get("spriteMappings", []) if isinstance(core, dict) else []
    if not any(row.get("name") == BACK_THREAD for row in mapped_sprites):
        raise ValueError("Core 运行时重排没有包含背后白色丝线 Sprite")
    map_mapping = manifest.get("runtimeDeathAssetMapping", {}).get("map")
    if not isinstance(map_mapping, dict) or map_mapping.get("name") != "Shade_Pin":
        raise ValueError("地图死亡位置图标 Shade_Pin 没有运行时 PNG 映射")

    all_files = [path for path in OUTPUT.rglob("*") if path.is_file()]
    bundles = [path for path in all_files if path.suffix.lower() == ".bundle"]
    if bundles:
        raise ValueError("运行时输出仍含 AssetBundle：" + ", ".join(map(str, bundles)))
    archives = [path for path in all_files
                if path.suffix.lower() in {".zip", ".7z", ".rar"}]
    if archives:
        raise ValueError("日常输出目录不应包含压缩包：" + ", ".join(map(str, archives)))
    if any("TestSkin" in path.relative_to(OUTPUT).as_posix() for path in all_files):
        raise ValueError("输出包含旧测试皮肤路径")

    result = {
        "skinName": skin_name,
        "outputDirectory": str(OUTPUT),
        "outputKind": "runtime-directory",
        "bundleCount": 0,
        "archiveCount": 0,
        "textureCount": len(records),
        "textures": records,
        "collectionAtlasCount": len(collection_records),
        "collectionAtlases": collection_records,
        "runtimeFiles": {
            "subsdk9Sha256": loader_hash,
            "mainNpdmSha256": npdm_hash,
            "ips32Sha256": patch_hash,
        },
        "deathAssets": {
            "directSpriteTextures": sorted(REQUIRED_SPRITES),
            "mapSprite": map_mapping,
            "backThreadSprite": BACK_THREAD,
            "coreRuntimeMapping": core,
        },
        "validation": "PNG paths, dimensions and runtime-only folder contents passed; Switch display pending.",
    }
    report_path = ROOT / "output/reports/hero-death-assets/runtime-package-validation.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({
        "skinName": skin_name,
        "outputDirectory": str(OUTPUT),
        "textureCount": len(records),
        "bundleCount": 0,
        "archiveCount": 0,
        "validationReport": str(report_path),
        "inGameValidation": "pending Switch test",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
