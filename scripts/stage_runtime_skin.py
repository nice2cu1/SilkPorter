"""构建兼容 SilkRuntime 的运行时皮肤目录。

默认将生成物复制到 SilkPorter/output，也可指定单独的 staging 输出目录。脚本接收
PC 皮肤根目录，读取 Texture2D、Collection 和 SpriteAtlas 分析报告，暂存精确纹理与整图
SpriteAtlas PNG，并按运行时引用类型将 Sprite 引用的 Texture2D 放入
Sprite Hook 可查找的目录。死亡茧、背后丝线和地图死亡图标也通过 SilkRuntime 的
Sprite Hook 加载 PNG；本脚本不修改或输出 AssetBundle。脚本针对当前 SilkRuntime ELF 生成 IPS32 补丁，并复制
经过验证的 SilkRuntime main.npdm 和 subsdk9。运行时直接解析命名文件，不需要
skin.json 或 active.txt。原始样本和 PC Mod 文件不会被修改。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import sys
from pathlib import Path


PORTER_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_CANDIDATE = PORTER_ROOT.parent
DEFAULT_WORKSPACE = (
    WORKSPACE_CANDIDATE
    if (WORKSPACE_CANDIDATE / "SilkRuntime").is_dir()
    else PORTER_ROOT
)
ROOT = Path(os.environ.get("SILK_WORKSPACE_ROOT", DEFAULT_WORKSPACE)).resolve()
RUNTIME = Path(
    os.environ.get("SILK_RUNTIME_ROOT", str(ROOT / "SilkRuntime"))
).resolve()
DEPLOY_NSO = RUNTIME / "output" / "deploy" / "subsdk9"
RUNTIME_NPDM = RUNTIME / "output" / "main.npdm"
SYMBOL_LIST = RUNTIME / "output" / "build" / "SilkModLoader.lst"
DEFAULT_OUTPUT = PORTER_ROOT / "output"
DEFAULT_REPORTS_DIR = ROOT / "output" / "reports"

BUILD_ID = "FC9EA4CCC955D5799F37752B2D730B31"
TESTED_GAME_VERSION = "ver. 1.0.30000"
TESTED_SWITCH_SYSTEM_VERSION = "22.5.0"
TESTED_ATMOSPHERE_VERSION = "1.11.2"
RUNTIME_NPDM_SHA256 = "27B2DE6A0C7324A8E4141CC6482281067C4CB6D2D3444983412CDAF6783542DE"
RUNTIME_NPDM_ADDED_SVCS = {
    "svcCreateCodeMemory": "0x4b",
    "svcControlCodeMemory": "0x4c",
    "svcSetProcessMemoryPermission": "0x73",
    "svcMapProcessMemory": "0x74",
    "svcUnmapProcessMemory": "0x75",
    "svcMapProcessCodeMemory": "0x77",
    "svcUnmapProcessCodeMemory": "0x78",
}
CALL_SITE_RVA = 0x5B582A8
EXPECTED_INSTRUCTION = 0x9400021E
MATERIAL_SET_MAIN_TEXTURE_RVA = 0x5F82980
EXPECTED_MATERIAL_CALL_COUNT = 2
SPRITE_RENDERER_SET_SPRITE_RVA = 0x5F3F650
EXPECTED_SPRITE_RENDERER_CALL_COUNT = 103
UI_IMAGE_SET_SPRITE_RVA = 0x6182740
EXPECTED_UI_IMAGE_CALL_COUNT = 64
SPRITE_ATLAS_GET_SPRITE_RVA = 0x6031A00
EXPECTED_SPRITE_ATLAS_GET_SPRITE_CALL_COUNT = 1
SPRITE_ATLAS_GET_SPRITE_CALL_SITE = 0x5E95A60
SPRITE_ATLAS_GET_SPRITE_CALL_INSTRUCTION = 0x94066FE8
GAME_UPDATE_CALL_SITE = 0x24AB56C
GAME_UPDATE_ORIGINAL_RVA = 0x24AB7A0
GAME_UPDATE_INSTRUCTION = 0x9400008D
NSO_HEADER_SIZE = 0x100
MODULE_DELTA = 0x08A3A000

# Generic textures with observed Sprite references. OnSpriteAssigned also
# falls back to standalone/ so older classification does not hide Sprite assets.
SPRITE_BACKED_GENERIC_TEXTURE_NAMES = frozenset({
    "diving_bell_bench_grab0001",
    "diving_bell_bench_grab0002",
    "diving_bell_bench_grab0003",
    "diving_bell_bench_grab0004",
    "diving_bell_bench_grab0005",
    "diving_bell_bench_grab0006",
    "diving_bell_bench_grab0007",
    "diving_bell_bench_grab0008",
    "Hornet_death0003",
    "Hornet_death0004",
    "Hornet_death_cocoon_particle_chunks",
    "Hornet_death_pieces_0000s_0001_6",
    "HUD_frame_hunter_v2_red0005",
    "HUD_frame_v30005",
    "HUD_frame_v30009",
    "HUD_frame_v3_extra_glow_flash0000",
    "HUD_frame_v3_extra_glow_flash0001",
    "HUD_frame_v3_extra_glow_flash0002",
    "HUD_frame_v3_extra_glow_flash0003",
})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError(f"不是带有 IHDR 的 PNG：{path}")
    return struct.unpack_from(">II", data, 16)


def resource_filename(name: str) -> str:
    """将 Unity 对象名编码为确定且适合 SD 卡路径的 PNG 文件名。"""
    encoded = ["r_"]
    for byte in name.encode("utf-8"):
        if (
            ord("A") <= byte <= ord("Z")
            or ord("a") <= byte <= ord("z")
            or ord("0") <= byte <= ord("9")
            or byte == ord("-")
        ):
            encoded.append(chr(byte))
        else:
            encoded.append(f"_{byte:02X}")
    return "".join(encoded) + ".png"


def discover_default_skin_dir(root: Path) -> Path:
    """在不绑定具体皮肤名称的前提下查找唯一的 PC 皮肤根目录。"""
    pc_mods = root / "pc-mods"
    if not pc_mods.is_dir():
        raise SystemExit(
            "未指定 PC 皮肤根目录，且默认 pc-mods 目录不存在："
            f"{pc_mods}；请使用 --skin-dir"
        )
    candidates = sorted(
        {path.parent.resolve() for path in pc_mods.rglob("Texture2D") if path.is_dir()},
        key=lambda path: str(path).casefold(),
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SystemExit(
            f"在 {pc_mods} 下没有找到包含 Texture2D/ 的 PC 皮肤根目录；"
            "请使用 --skin-dir"
        )
    formatted = "\n  ".join(str(path) for path in candidates)
    raise SystemExit(
        "找到多个 PC 皮肤根目录；请使用 --skin-dir 选择一个：\n  "
        + formatted
    )


def discover_default_main_text(root: Path) -> Path:
    """在存在唯一候选时查找已解包游戏的 text 段。"""
    candidates = sorted(
        {
            path.resolve()
            for path in (root / "output").glob("exefs-unpacked-*/main/text.bin")
            if path.is_file()
        },
        key=lambda path: str(path).casefold(),
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SystemExit(
            "未指定游戏 main/text.bin，且没有找到唯一的已解包 main 文本段；"
            "请使用 --main-text"
        )
    formatted = "\n  ".join(str(path) for path in candidates)
    raise SystemExit(
        "找到多个已解包的游戏 text.bin；请使用 --main-text 指定一个：\n  "
        + formatted
    )


def validate_skin_name(value: str) -> str:
    """确保用户指定的 Mods/Skin 子目录名是安全的单一路径组件。"""
    name = value.strip()
    if not name or name in {".", ".."} or Path(name).name != name:
        raise SystemExit(f"--skin-name 无效（必须是单个目录名）：{value!r}")
    if any(char in name for char in '<>:"/\\|?*'):
        raise SystemExit(f"--skin-name 无效，包含路径字符：{value!r}")
    return name


def build_skin_png_index(skin_dir: Path) -> dict[str, list[Path]]:
    """按文件名建立源 PNG 索引，使报告不必保存本机绝对路径。"""
    index: dict[str, list[Path]] = {}
    for path in skin_dir.rglob("*.png"):
        if path.is_file():
            index.setdefault(path.name.casefold(), []).append(path)
    for paths in index.values():
        paths.sort(key=lambda path: str(path).casefold())
    return index


def resolve_skin_png(
    skin_dir: Path,
    png_index: dict[str, list[Path]],
    report_value: object,
    fallback_stem: str,
    description: str,
) -> Path:
    """根据选定皮肤解析报告中的 PNG，绝不直接使用报告中的旧路径。"""
    names: list[str] = []
    report_text = str(report_value or "").strip()
    if report_text:
        report_name = Path(report_text).name
        if report_name:
            names.append(report_name)
    if fallback_stem:
        fallback_name = Path(fallback_stem).name
        if not fallback_name.lower().endswith(".png"):
            fallback_name += ".png"
        if fallback_name not in names:
            names.append(fallback_name)

    for name in names:
        preferred = [skin_dir / "Texture2D" / name, skin_dir / name]
        for path in preferred:
            if path.is_file():
                return path
        matches = png_index.get(name.casefold(), [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            formatted = ", ".join(str(path) for path in matches)
            raise SystemExit(
                f"{description} 在选定皮肤中存在歧义；文件名 {name!r} "
                f"匹配到：{formatted}"
            )

    tried = ", ".join(names) or "<没有 PNG 文件名>"
    raise SystemExit(
        f"{description} 在选定皮肤 {skin_dir} 下不存在；尝试过：{tried}"
    )


def discover_collections(root: Path) -> dict[str, dict[int, Path]]:
    if not root.is_dir():
        raise SystemExit(f"Collection 输入目录不存在：{root}")

    collections: dict[str, dict[int, Path]] = {}
    atlas_pattern = re.compile(r"^atlas([0-9]+)\.png$")
    for directory in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
        if not directory.is_dir():
            continue
        atlases: dict[int, Path] = {}
        for path in directory.iterdir():
            if not path.is_file():
                continue
            match = atlas_pattern.fullmatch(path.name)
            if match is None:
                continue
            index = int(match.group(1))
            if index >= 4:
                raise SystemExit(
                    f"{directory.name} 存在 atlas{index}；当前运行时最多支持 atlas0-3"
                )
            atlases[index] = path
        if not atlases:
            continue
        expected = list(range(len(atlases)))
        if sorted(atlases) != expected:
            raise SystemExit(
                f"{directory.name} 的 atlas 文件没有从 atlas0 连续编号："
                f"{sorted(atlases)}"
            )
        collections[directory.name] = dict(sorted(atlases.items()))

    if not collections:
        raise SystemExit(f"在以下目录下没有找到 Atlas Collection：{root}")
    return collections


def discover_generic_textures(
    report_path: Path,
    skin_dir: Path,
    png_index: dict[str, list[Path]],
) -> list[dict[str, object]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    generic_inputs = report.get("genericInputs", {})
    generic_targets = report.get("genericTargets", {})
    if not isinstance(generic_inputs, dict) or not isinstance(generic_targets, dict):
        raise SystemExit(f"通用目标报告缺少必要的映射：{report_path}")

    result: list[dict[str, object]] = []
    found_sprite_backed: set[str] = set()
    for key in sorted(generic_inputs, key=str.casefold):
        info = generic_inputs.get(key)
        candidates = generic_targets.get(key)
        if not isinstance(info, dict):
            raise SystemExit(f"通用输入报告条目不完整：{key}")
        if not isinstance(candidates, list) or not candidates:
            # 报告可能包含没有 Switch 目标的 PC 输入。它无法部署，因此将其排除，
            # 不要因为无关的源资源导致整个皮肤失败。
            continue
        target_name = str(info.get("key", ""))
        if not target_name:
            raise SystemExit(f"独立 Texture2D 目标没有名称：{key}")
        source = resolve_skin_png(
            skin_dir,
            png_index,
            info.get("png"),
            target_name,
            f"standalone Texture2D {target_name}",
        )
        width, height = png_dimensions(source)
        candidate = candidates[0]
        expected = (int(candidate["width"]), int(candidate["height"]))
        if (width, height) != expected:
            raise SystemExit(
                f"独立纹理 {target_name} 的输入尺寸发生变化："
                f"PNG={width}x{height}，报告={expected[0]}x{expected[1]}"
            )
        item: dict[str, object] = {
            "name": target_name,
            "source": source,
            "width": width,
            "height": height,
            "size": source.stat().st_size,
            "sha256": sha256(source),
            "runtimeKind": "standalone",
        }
        if target_name in SPRITE_BACKED_GENERIC_TEXTURE_NAMES:
            target_format = int(candidate.get("format", -1))
            if target_format != 48:
                raise SystemExit(
                    f"Sprite 引用纹理 {target_name} 的目标格式发生变化："
                    f"报告格式={target_format}，需要格式 48"
                )
            item.update({
                "runtimeKind": "sprite",
                "format": target_format,
                "semanticKey": target_name,
            })
            found_sprite_backed.add(target_name)
        result.append(item)

    missing_sprite_backed = SPRITE_BACKED_GENERIC_TEXTURE_NAMES - found_sprite_backed
    if missing_sprite_backed:
        missing = ", ".join(sorted(missing_sprite_backed))
        raise SystemExit(
            "分析报告没有找到预期的 Sprite 引用 Texture2D 目标：" + missing
        )
    return result


def discover_sprite_textures(
    report_path: Path,
    skin_dir: Path,
    png_index: dict[str, list[Path]],
) -> list[dict[str, object]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    canonical = report.get("pcCanonical", {})
    matches = report.get("matches", {})
    if not isinstance(canonical, dict) or not isinstance(matches, dict):
        raise SystemExit(f"SpriteAtlas 报告缺少必要的映射：{report_path}")

    result: list[dict[str, object]] = []
    for key in sorted(matches, key=str.casefold):
        candidates = matches.get(key)
        pc = canonical.get(key)
        if not isinstance(candidates, list) or not candidates or not isinstance(pc, dict):
            raise SystemExit(f"SpriteAtlas 匹配条目不完整：{key}")
        target = candidates[0]
        target_format = int(target["format"])
        if target_format != 48:
            continue
        source = resolve_skin_png(
            skin_dir,
            png_index,
            pc.get("png"),
            str(pc.get("key", target["name"])),
            f"SpriteAtlas {target['name']}",
        )
        width, height = png_dimensions(source)
        if (width, height) != (int(target["width"]), int(target["height"])):
            raise SystemExit(
                f"SpriteAtlas {target['name']} 的输入尺寸发生变化："
                f"PNG={width}x{height}，报告={target['width']}x{target['height']}"
            )
        binding = {}
        if key.casefold() == "1|2048x2048|area_art":
            from resolve_save_slot_texture import resolve_save_slot_texture
            binding = resolve_save_slot_texture(ROOT, source, str(target["name"]))
        result.append({
            "name": str(target["name"]),
            "source": source,
            "width": width,
            "height": height,
            "format": target_format,
            "semanticKey": key,
            "size": source.stat().st_size,
            "sha256": sha256(source),
            **binding,
        })
    if not result:
        raise SystemExit("SpriteAtlas 报告中没有精确的 ASTC4x4 匹配项")
    return result


def find_bl_call_sites(blob: bytes, target_rva: int) -> list[int]:
    result: list[int] = []
    for offset in range(0, len(blob) - 3, 4):
        instruction = struct.unpack_from("<I", blob, offset)[0]
        if instruction & 0xFC000000 != 0x94000000:
            continue
        immediate = instruction & 0x03FFFFFF
        if immediate & 0x02000000:
            immediate -= 1 << 26
        if offset + (immediate << 2) == target_rva:
            result.append(offset)
    return result


def find_hook_offset(symbol: str, symbol_list: Path) -> int:
    pattern = re.compile(
        rf"^([0-9a-fA-F]{{16}})\s+[0-9a-fA-F]{{16}}\s+\S\s+{re.escape(symbol)}\s*$"
    )
    for line in symbol_list.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if match:
            return int(match.group(1), 16)
    raise RuntimeError(
        f"当前 ELF 符号清单中没有找到 {symbol}：{symbol_list}"
    )


def encode_bl(from_address: int, to_address: int) -> int:
    delta = to_address - from_address
    if (from_address | to_address) & 0x3:
        raise ValueError("BL 地址必须按 4 字节对齐")
    if delta & 0x3 or delta < -(1 << 27) or delta >= (1 << 27):
        raise ValueError(f"BL 目标超出范围或未对齐：delta={delta}")
    return 0x94000000 | ((delta >> 2) & 0x03FFFFFF)


def make_ips32(
    records: list[tuple[int, int]] | int,
    instruction: int | None = None,
) -> bytes:
    """编码 IPS32 记录，同时保留原有的单记录调用接口。"""
    if isinstance(records, int):
        if instruction is None:
            raise TypeError("单条 IPS32 记录必须提供 instruction")
        records = [(records, instruction)]
    elif instruction is not None:
        raise TypeError("instruction 只能与单个 offset 一起使用")
    output = bytearray(b"IPS32")
    for offset, instruction in records:
        output.extend(offset.to_bytes(4, "big"))
        output.extend((4).to_bytes(2, "big"))
        output.extend(struct.pack("<I", instruction))
    output.extend(b"EEOF")
    return bytes(output)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skin-dir",
        type=Path,
        default=None,
        help=(
            "包含 Collection 目录和 Texture2D/ 的 PC 皮肤根目录；"
            "省略时自动查找 pc-mods/ 下唯一的候选目录"
        ),
    )
    parser.add_argument(
        "--skin-name",
        default=None,
        help="在 romfs/SilkModLoader/Mods/Skin/ 下创建的目录名",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=DEFAULT_REPORTS_DIR,
        help="包含 all-targets.json 和 spriteatlas-targets.json 的目录",
    )
    parser.add_argument(
        "--main-text",
        type=Path,
        default=None,
        help="已解包游戏的 main/text.bin；存在多个构建时必须指定",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    output = args.output or DEFAULT_OUTPUT
    output = output.resolve()
    is_current_output = output == DEFAULT_OUTPUT.resolve()
    skin_dir = (args.skin_dir or discover_default_skin_dir(ROOT)).resolve()
    if not skin_dir.is_dir():
        raise SystemExit(f"PC 皮肤输入目录不存在：{skin_dir}")
    skin_name = validate_skin_name(args.skin_name or skin_dir.name)
    reports_dir = args.reports_dir
    if not reports_dir.is_absolute():
        reports_dir = ROOT / reports_dir
    reports_dir = reports_dir.resolve()
    generic_report = reports_dir / "all-targets.json"
    spriteatlas_report = reports_dir / "spriteatlas-targets.json"
    main_text_path = (
        args.main_text.resolve()
        if args.main_text is not None
        else discover_default_main_text(ROOT)
    )
    png_index = build_skin_png_index(skin_dir)
    collections = discover_collections(skin_dir)
    generic_textures = discover_generic_textures(
        generic_report, skin_dir, png_index
    )
    standalone = [
        item for item in generic_textures if item["runtimeKind"] == "standalone"
    ]
    sprite_backed_generic = [
        item for item in generic_textures if item["runtimeKind"] == "sprite"
    ]
    sys.path.insert(0, str(PORTER_ROOT / "tools"))
    sprite_textures = sprite_backed_generic + discover_sprite_textures(
        spriteatlas_report, skin_dir, png_index
    )
    # Initial Sprite assets and SpriteAtlas textures are routed through the
    # generic SkinLoader hooks. Core/Map need layout-aware runtime PNGs.
    from build_hero_death_runtime_textures import build_runtime_death_textures
    runtime_death_assets = build_runtime_death_textures(ROOT, skin_dir)
    death_sprite_textures = runtime_death_assets["spriteTextures"]
    death_names = {str(item["name"]) for item in death_sprite_textures}
    standalone = [item for item in standalone if item["name"] not in death_names]
    sprite_by_name = {
        str(item["name"]): item
        for item in sprite_textures
        if str(item["name"]) not in death_names
    }
    sprite_by_name.update({str(item["name"]): item for item in death_sprite_textures})
    sprite_textures = list(sprite_by_name.values())
    input_paths = [
        path for atlases in collections.values() for path in atlases.values()
    ]
    input_paths.extend(item["source"] for item in standalone)
    input_paths.extend(item["source"] for item in sprite_textures)
    for path in (
        DEPLOY_NSO,
        RUNTIME_NPDM,
        SYMBOL_LIST,
        main_text_path,
        generic_report,
        spriteatlas_report,
        *input_paths,
    ):
        if not path.is_file():
            raise SystemExit(f"必要输入不存在：{path}")

    runtime_npdm_sha256 = sha256(RUNTIME_NPDM)
    if runtime_npdm_sha256 != RUNTIME_NPDM_SHA256:
        raise SystemExit(
            "运行时皮肤 NPDM 哈希不匹配："
            f"期望 {RUNTIME_NPDM_SHA256}，实际为 {runtime_npdm_sha256}；"
            "拒绝暂存未经验证的 main.npdm"
        )

    nso = DEPLOY_NSO.read_bytes()
    if nso[:4] != b"NSO0":
        raise SystemExit(f"运行时产物不是 NSO0：{DEPLOY_NSO}")

    main_text = main_text_path.read_bytes()
    actual = struct.unpack_from("<I", main_text, CALL_SITE_RVA)[0]
    if actual != EXPECTED_INSTRUCTION:
        raise SystemExit(
            "main 调用点指令不匹配："
            f"期望 0x{EXPECTED_INSTRUCTION:08x}，实际为 0x{actual:08x}"
        )
    material_call_sites = find_bl_call_sites(main_text, MATERIAL_SET_MAIN_TEXTURE_RVA)
    if len(material_call_sites) != EXPECTED_MATERIAL_CALL_COUNT:
        raise SystemExit(
            "Material.set_mainTexture 调用点数量发生变化："
            f"期望 {EXPECTED_MATERIAL_CALL_COUNT}，实际为 {len(material_call_sites)}"
        )
    sprite_renderer_call_sites = find_bl_call_sites(
        main_text, SPRITE_RENDERER_SET_SPRITE_RVA
    )
    if len(sprite_renderer_call_sites) != EXPECTED_SPRITE_RENDERER_CALL_COUNT:
        raise SystemExit(
            "SpriteRenderer.set_sprite 调用点数量发生变化："
            f"期望 {EXPECTED_SPRITE_RENDERER_CALL_COUNT}，"
            f"实际为 {len(sprite_renderer_call_sites)}"
        )
    ui_image_call_sites = find_bl_call_sites(main_text, UI_IMAGE_SET_SPRITE_RVA)
    if len(ui_image_call_sites) != EXPECTED_UI_IMAGE_CALL_COUNT:
        raise SystemExit(
            "UI.Image.set_sprite 调用点数量发生变化："
            f"期望 {EXPECTED_UI_IMAGE_CALL_COUNT}，实际为 {len(ui_image_call_sites)}"
        )
    sprite_atlas_get_sprite_call_sites = find_bl_call_sites(
        main_text, SPRITE_ATLAS_GET_SPRITE_RVA
    )
    if len(sprite_atlas_get_sprite_call_sites) != EXPECTED_SPRITE_ATLAS_GET_SPRITE_CALL_COUNT:
        raise SystemExit(
            "SpriteAtlas.GetSprite 调用点数量发生变化："
            f"期望 {EXPECTED_SPRITE_ATLAS_GET_SPRITE_CALL_COUNT}，"
            f"实际为 {len(sprite_atlas_get_sprite_call_sites)}"
        )
    if sprite_atlas_get_sprite_call_sites != [SPRITE_ATLAS_GET_SPRITE_CALL_SITE]:
        raise SystemExit(
            "SpriteAtlas.GetSprite 调用点位置发生变化："
            f"期望 [0x{SPRITE_ATLAS_GET_SPRITE_CALL_SITE:x}]，"
            f"实际为 {[hex(offset) for offset in sprite_atlas_get_sprite_call_sites]}"
        )
    actual_atlas_call = struct.unpack_from(
        "<I", main_text, sprite_atlas_get_sprite_call_sites[0]
    )[0]
    if actual_atlas_call != SPRITE_ATLAS_GET_SPRITE_CALL_INSTRUCTION:
        raise SystemExit(
            "SpriteAtlas.GetSprite 调用点指令不匹配："
            f"期望 0x{SPRITE_ATLAS_GET_SPRITE_CALL_INSTRUCTION:08x}，"
            f"实际为 0x{actual_atlas_call:08x}"
        )

    actual_update = struct.unpack_from("<I", main_text, GAME_UPDATE_CALL_SITE)[0]
    update_calls = find_bl_call_sites(main_text, GAME_UPDATE_ORIGINAL_RVA)
    if actual_update != GAME_UPDATE_INSTRUCTION or update_calls != [GAME_UPDATE_CALL_SITE]:
        raise SystemExit(
            f"GameManager.UpdateEngagement 调用点不匹配：{update_calls}, opcode=0x{actual_update:08x}"
        )

    collection_metadata = {}
    for collection, atlases in collections.items():
        atlas_metadata = {}
        for index, path in atlases.items():
            name = f"atlas{index}"
            width, height = png_dimensions(path)
            if width == 0 or height == 0:
                raise SystemExit(f"{collection}/{name}.png 的尺寸为零")
            atlas_metadata[name] = {
                "size": path.stat().st_size,
                "sha256": sha256(path),
                "dimensions": [width, height],
            }
        collection_metadata[collection] = {
            "atlasCount": len(atlases),
            "atlases": atlas_metadata,
        }

    hook_offset = find_hook_offset("Tk2dInitExternalHook", SYMBOL_LIST)
    material_hook_offset = find_hook_offset(
        "MaterialSetMainTextureExternalHook", SYMBOL_LIST
    )
    sprite_renderer_hook_offset = find_hook_offset(
        "SpriteRendererSetSpriteExternalHook", SYMBOL_LIST
    )
    ui_image_hook_offset = find_hook_offset(
        "UIImageSetSpriteExternalHook", SYMBOL_LIST
    )
    sprite_atlas_hook_offset = find_hook_offset(
        "SpriteAtlasGetSpriteExternalHook", SYMBOL_LIST
    )
    frame_hook_offset = find_hook_offset("GameUpdateExternalHook", SYMBOL_LIST)
    target = MODULE_DELTA + hook_offset
    material_target = MODULE_DELTA + material_hook_offset
    opcode = encode_bl(CALL_SITE_RVA, target)
    material_opcodes = [
        encode_bl(call_site, material_target)
        for call_site in material_call_sites
    ]
    sprite_renderer_opcodes = [
        encode_bl(call_site, MODULE_DELTA + sprite_renderer_hook_offset)
        for call_site in sprite_renderer_call_sites
    ]
    ui_image_opcodes = [
        encode_bl(call_site, MODULE_DELTA + ui_image_hook_offset)
        for call_site in ui_image_call_sites
    ]
    sprite_atlas_opcodes = [
        encode_bl(call_site, MODULE_DELTA + sprite_atlas_hook_offset)
        for call_site in sprite_atlas_get_sprite_call_sites
    ]
    patch_records = [(NSO_HEADER_SIZE + CALL_SITE_RVA, opcode)]
    patch_records.extend(
        (NSO_HEADER_SIZE + call_site, material_opcode)
        for call_site, material_opcode in zip(material_call_sites, material_opcodes)
    )
    patch_records.extend(
        (NSO_HEADER_SIZE + call_site, opcode_value)
        for call_site, opcode_value in zip(
            sprite_renderer_call_sites, sprite_renderer_opcodes
        )
    )
    patch_records.extend(
        (NSO_HEADER_SIZE + call_site, opcode_value)
        for call_site, opcode_value in zip(ui_image_call_sites, ui_image_opcodes)
    )
    patch_records.extend(
        (NSO_HEADER_SIZE + call_site, opcode_value)
        for call_site, opcode_value in zip(
            sprite_atlas_get_sprite_call_sites, sprite_atlas_opcodes
        )
    )
    frame_opcode = encode_bl(GAME_UPDATE_CALL_SITE, MODULE_DELTA + frame_hook_offset)
    patch_records.append((NSO_HEADER_SIZE + GAME_UPDATE_CALL_SITE, frame_opcode))
    patch = make_ips32(patch_records)

    exefs = output / "atmosphere" / "contents" / "010013C00E930000" / "exefs"
    patch_path = output / "atmosphere" / "exefs_patches" / "SilkModLoader" / (
        f"{BUILD_ID}.ips"
    )
    skin_base = (
        output
        / "atmosphere"
        / "contents"
        / "010013C00E930000"
        / "romfs"
        / "SilkModLoader"
        / "Mods"
        / "Skin"
    )
    skin_root = skin_base / skin_name
    bundle_root = (
        output / "atmosphere" / "contents" / "010013C00E930000" / "romfs"
        / "Data" / "StreamingAssets" / "aa" / "Switch"
    ).resolve()
    romfs_root = (
        output / "atmosphere" / "contents" / "010013C00E930000" / "romfs"
    ).resolve()
    nso_path = exefs / "subsdk9"
    npdm_path = exefs / "main.npdm"
    manifest_path = output / "manifest.json"
    readme_path = output / "README.txt"

    # Remove only Bundle files recorded by the previous generated manifest.
    # This migrates the old output to a runtime-only folder without touching
    # game originals or unrelated files elsewhere in the workspace.
    if manifest_path.is_file():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in previous.get("staticBundles", []):
            old_file = (output / item["file"]).resolve()
            if not old_file.is_relative_to(bundle_root):
                raise SystemExit(f"旧生成 Bundle 路径越界：{item.get('file')}")
            if old_file.is_file():
                old_file.unlink()
                parent = old_file.parent
                while parent != bundle_root and parent.is_dir():
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
    parent = bundle_root
    while parent != romfs_root and parent.is_dir():
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    remaining_bundles = sorted(
        (path for path in bundle_root.rglob("*")
         if path.is_file() and path.suffix.casefold() == ".bundle"),
        key=lambda path: str(path).casefold(),
    ) if bundle_root.is_dir() else []
    if remaining_bundles:
        raise SystemExit(
            "运行时皮肤输出路径仍含 AssetBundle 文件，拒绝生成："
            + ", ".join(str(path) for path in remaining_bundles)
        )

    # 输出目录是生成的部署包。Mods/Skin 下只保留一个 SilkRuntime 皮肤；暂存前
    # 删除旧生成目录，避免旧皮肤名称意外继续生效。
    legacy_skin_roots = [
        output / "SilkModLoader",
        output
        / "atmosphere"
        / "contents"
        / "010013C00E930000"
        / "romfs"
        / "SilkModLoader"
        / "Mods"
        / "TestSkin"
    ]
    for legacy_skin_root in legacy_skin_roots:
        if legacy_skin_root.is_dir():
            shutil.rmtree(legacy_skin_root)
    if skin_base.is_dir():
        shutil.rmtree(skin_base)

    exefs.mkdir(parents=True, exist_ok=True)
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    skin_root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEPLOY_NSO, nso_path)
    shutil.copyfile(RUNTIME_NPDM, npdm_path)
    patch_path.write_bytes(patch)
    for collection, atlases in collections.items():
        collection_root = skin_root / collection
        collection_root.mkdir(parents=True, exist_ok=True)
        for index, path in atlases.items():
            shutil.copyfile(path, collection_root / f"atlas{index}.png")
    standalone_root = skin_root / "standalone"
    standalone_root.mkdir(parents=True, exist_ok=True)
    standalone_manifest = []
    for item in standalone:
        destination = standalone_root / resource_filename(str(item["name"]))
        shutil.copyfile(item["source"], destination)
        standalone_manifest.append({
            "name": item["name"],
            "file": f"standalone/{destination.name}",
            "width": item["width"],
            "height": item["height"],
        })
    sprite_root = skin_root / "sprite"
    sprite_root.mkdir(parents=True, exist_ok=True)
    sprite_manifest = []
    for item in sprite_textures:
        destination = sprite_root / resource_filename(str(item["name"]))
        shutil.copyfile(item["source"], destination)
        sprite_manifest.append({
            "name": item["name"],
            "file": f"sprite/{destination.name}",
            "width": item["width"],
            "height": item["height"],
            "format": item["format"],
            "semanticKey": item["semanticKey"],
            "size": destination.stat().st_size,
            "sha256": sha256(destination),
            **{key: item[key] for key in ("pathId", "validationReport", "bindingEvidence")
               if key in item},
        })
    sprite_aliases = []
    package_name = skin_name if is_current_output else output.name
    package = {
        "name": package_name,
        "purpose": "通过 SilkRuntime SkinLoader 加载 PNG 的运行时皮肤目录",
        "programId": "010013C00E930000",
        "gameBuildId": BUILD_ID,
        "testedEnvironment": {
            "gameVersion": TESTED_GAME_VERSION,
            "switchSystemVersion": TESTED_SWITCH_SYSTEM_VERSION,
            "atmosphereVersion": TESTED_ATMOSPHERE_VERSION,
        },
        "loaderNsoSha256": sha256(nso_path),
        "loaderNsoSize": nso_path.stat().st_size,
        "collectionCount": len(collections),
        "replacementCount": len(input_paths),
        "runtimeReplacementCount": len(input_paths),
        "standaloneCount": len(standalone),
        "spriteTextureCount": len(sprite_textures),
        "spriteTextureAliases": sprite_aliases,
        "runtimeDeathAssetMapping": {
            "map": runtime_death_assets["report"].get("mapMapping"),
            "core": runtime_death_assets["report"].get("coreMapping"),
            "report": runtime_death_assets["reportPath"].relative_to(ROOT).as_posix(),
        },
        "inGameValidation": "待 Switch 游戏内确认；需要 operation=applied 日志",
        "skinName": skin_name,
        "skinInputDirectory": str(skin_dir),
        "outputKind": "directory",
        "standaloneTextures": standalone_manifest,
        "spriteTextures": sprite_manifest,
        "collections": collection_metadata,
        "skinPath": f"rom:/SilkModLoader/Mods/Skin/{skin_name}",
        "standaloneInputs": "来自 all-targets.json 的通用 Texture2D 目标，按运行时引用类型分类",
        "spriteBackedGenericInputs": sorted(SPRITE_BACKED_GENERIC_TEXTURE_NAMES),
        "spriteAtlasInputs": "来自 spriteatlas-targets.json 的精确目标，以及按 RenderDataKey 重排的运行时整图 PNG",
        "callSiteRva": f"0x{CALL_SITE_RVA:x}",
        "expectedInstruction": f"0x{EXPECTED_INSTRUCTION:08x}",
        "moduleDelta": f"0x{MODULE_DELTA:x}",
        "hookOffset": f"0x{hook_offset:x}",
        "patchOffsets": [f"0x{offset:x}" for offset, _ in patch_records],
        "patchInstruction": f"0x{opcode:08x}",
        "materialCallSites": [f"0x{offset:x}" for offset in material_call_sites],
        "materialHookOffset": f"0x{material_hook_offset:x}",
        "materialPatchInstructions": [f"0x{value:08x}" for value in material_opcodes],
        "spriteRendererCallSites": [f"0x{offset:x}" for offset in sprite_renderer_call_sites],
        "spriteRendererHookOffset": f"0x{sprite_renderer_hook_offset:x}",
        "uiImageCallSites": [f"0x{offset:x}" for offset in ui_image_call_sites],
        "uiImageHookOffset": f"0x{ui_image_hook_offset:x}",
        "spriteAtlasGetSpriteCallSites": [
            f"0x{offset:x}" for offset in sprite_atlas_get_sprite_call_sites
        ],
        "spriteAtlasGetSpriteHookOffset": f"0x{sprite_atlas_hook_offset:x}",
        "loadedTextureDiscovery": {
            "method": "Resources.FindObjectsOfTypeAll(Texture2D) on Unity main thread",
            "callSiteRva": f"0x{GAME_UPDATE_CALL_SITE:x}",
            "originalRva": f"0x{GAME_UPDATE_ORIGINAL_RVA:x}",
            "hookOffset": f"0x{frame_hook_offset:x}",
            "patchInstruction": f"0x{frame_opcode:08x}",
            "intervalSeconds": 1,
            "maxObjectsPerFrame": 64,
            "maxNewPngPerFrame": 1,
            "snapshotGcHandle": "Normal",
            "identityCache": "native pointer + instance ID; linear probing; two scan generations",
            "cacheEntriesPerGeneration": 8192,
            "scanLogPolicy": "first scan, applied textures, or errors only",
            "retryIntervalSeconds": 5,
            "hardwareValidation": "pending",
        },
        "patchSha256": sha256(patch_path),
        "npdm": {
            "source": "SilkRuntime/output/main.npdm",
            "sourceJson": "SilkRuntime/misc/npdm-json/skyline.json",
            "sha256": sha256(npdm_path),
            "purpose": "由 SilkRuntime 生成并经过 H1e 验证的运行时皮肤 main.npdm",
            "addedSyscalls": RUNTIME_NPDM_ADDED_SVCS,
        },
        "gdb": r".\SilkRuntime\tools\switch_gdb\start-silkmodloader.ps1 -SwitchIp 192.168.5.7",
    }
    write_text(manifest_path, json.dumps(package, indent=2) + "\n")
    write_text(
        readme_path,
        f"""SilkModLoader 运行时皮肤包：{package_name}

此包绑定 Silksong Build ID
FC9EA4CCC955D5799F37752B2D730B31。将目录内容复制到 SD 卡根目录即可。包内包含
main.npdm、subsdk9、{len(patch_records)} 条启动前 IPS32 重定向，以及包含 {len(collections)} 个
Collection 目录的 LayeredFS romfs Skin/{skin_name}，其中有
{sum(len(atlases) for atlases in collections.values())} 张 TK2D Atlas PNG、
{len(standalone)} 张 Material 路径 Texture2D PNG，以及 {len(sprite_textures)} 张
Sprite 路径纹理 PNG（Sprite 引用的 Texture2D 与精确匹配的 SpriteAtlas 整图）。
日常构建只输出当前文件夹。压缩包仅在明确制作 release 时，使用专用 release 目录生成。

SkinLoader 在 Unity 主线程定期枚举已经加载的 Texture2D，并分帧匹配皮肤 PNG。
这条通用路径覆盖预先绑定在 Prefab、材质或 Sprite 中、没有触发 setter 的纹理。
每轮枚举结果由 Normal GCHandle 保护；最多检查 64 个对象/帧、应用 1 张新 PNG/帧。
枚举和单张 LoadImage 不能拆分，实际帧耗时仍需 Switch 验证。
Sprite、Material、Atlas.GetSprite Hook 和扫描器共用按实际纹理名查找及去重逻辑。
Core 与地图图集在离线阶段按 Sprite 元数据生成同尺寸 PNG，运行时由 SkinLoader
通过 Unity LoadImage 应用。本目录不包含任何 AssetBundle。
Core 的目标贴图按实际网格范围写入；构建检查非目标 Sprite 与原版相同。
此前扫描机制已有 Switch 应用日志与用户画面确认。本次缓存/日志优化尚待真机复核。
正常扫描仅在首次、有实际应用或异常时输出摘要；普通扫描保持安静。
存档选择页的 Area_Art 图标按当前原始资源中的 Texture2D 名称输出 PNG；构建时
先核对源图的未修改区域和当前 Switch Sprite 网格，避免继续沿用过期报告中的名称。
该名称修复尚待 Switch 确认；具体外观以皮肤源图为准，源图未改的图标仍保持原样。

此前基线验证成功的环境（不代表本次修改已验证）：

- 游戏版本：ver. 1.0.30000
- NS 系统版本：22.5.0
- Atmosphère（AMS）版本：1.11.2

运行时 PNG 匹配 Switch 格式 4（RGBA32）、12（DXT5/BC3）、48（ASTC 4x4）或
50（ASTC 6x6）。纹理按实际 Unity 对象名精确匹配，统一先查 standalone/，再查
sprite/；同名多实例按 native 地址和 instance ID 区分。不同名字的整图不能只凭
Sprite 名和相同尺寸套用，避免把不兼容的 UV 布局写入当前纹理。

此包包含由 SilkRuntime 生成、经过 H1e 验证，并具备运行时 Hook 所需进程内存 SVC
能力的 main.npdm。不要将它与游戏原版 main.npdm 或其他 Loader 模板 NPDM 混用。
不要同时启用历史 dmnt cheat。

如需捕获 SVC，请在工作区根目录执行：
  .\\SilkRuntime\\tools\\switch_gdb\\start-silkmodloader.ps1 -SwitchIp 192.168.5.7
等待 GDB 就绪后启动游戏。脚本停在 Build ID 行时输入 `continue`。预期会看到：
  [Skin] Unity Texture2D and RomFS reader bindings accepted ... skin root=...
  [Skin] Knight atlas0 operation=applied
  [Skin] texture=Hornet_death_pieces_0000s_0001_6 observer=discovery source=sprite operation=applied
  [SkinScan] round=1 textures=... cacheMisses=... applied=... retry=0

其他 Collection 会在初始化时输出对应日志。如果某项没有 applied，请停止测试并
保留 SVC 输出及 Atmosphère 崩溃报告。删除 exefs_patches 文件可以恢复未重定向的
游戏路径；删除 subsdk9 覆盖文件可以恢复 Loader 基线。
""",
    )

    print(f"已暂存：{output}")
    print(f"Loader SHA256：{package['loaderNsoSha256']}")
    print(f"TK2D Hook 偏移：0x{hook_offset:x}")
    print(f"Material Hook 偏移：0x{material_hook_offset:x}")
    print(f"SpriteRenderer Hook 偏移：0x{sprite_renderer_hook_offset:x}")
    print(f"UI Image Hook 偏移：0x{ui_image_hook_offset:x}")
    print(f"Frame Hook 偏移：0x{frame_hook_offset:x}，IPS32 记录：{len(patch_records)}")
    print(f"TK2D 补丁指令：0x{opcode:08x}")
    print(f"Material 补丁指令：{[f'0x{value:08x}' for value in material_opcodes]}")
    print(f"Collection：{len(collections)}，Atlas PNG：{len(input_paths) - len(standalone) - len(sprite_textures)}，独立 PNG：{len(standalone)}，Sprite PNG：{len(sprite_textures)}")
    print(f"死亡资源运行时 Sprite PNG：{len(death_sprite_textures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
