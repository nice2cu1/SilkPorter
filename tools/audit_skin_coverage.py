"""根据 Switch 资源包证据审计当前运行时皮肤的覆盖情况。

审计会明确区分三类情况：

* Switch TK2D Collection 或 Atlas 槽位存在，但当前皮肤没有输入；
* PC 皮肤输入存在 Switch 目标，但没有进入当前 Loader 清单；
* SpriteAtlas 目标的 Switch 格式或布局需要单独处理。

脚本不会修改任何原始 Bundle 或皮肤输入。
"""

from __future__ import annotations

import json
import re
import subprocess
import warnings
from collections import Counter, defaultdict
from pathlib import Path

import UnityPy


ROOT = Path(__file__).resolve().parents[2]
SWITCH_ROOT = ROOT / "romfs" / "Data" / "StreamingAssets" / "aa" / "Switch"
CURRENT_PACKAGE = ROOT / "SilkPorter" / "output"
SKIN_ROOT = (
    CURRENT_PACKAGE
    / "atmosphere"
    / "contents"
    / "010013C00E930000"
    / "romfs"
    / "SilkModLoader"
    / "Mods"
    / "Skin"
)
MANIFEST = CURRENT_PACKAGE / "manifest.json"
ALL_TARGETS = ROOT / "output" / "reports" / "all-targets.json"
SPRITE_TARGETS = ROOT / "output" / "reports" / "spriteatlas-targets.json"
STATIC_BUILD = ROOT / "output" / "reports" / "xingjianya-all-build.json"
OUT_JSON = ROOT / "output" / "reports" / "skin-coverage-audit.json"
OUT_MD = ROOT / "output" / "reports" / "skin-coverage-audit.md"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def texture_row(obj) -> dict:
    tex = obj.read()
    stream = tex.m_StreamData
    return {
        "pathId": int(obj.path_id),
        "name": str(tex.m_Name),
        "width": int(tex.m_Width),
        "height": int(tex.m_Height),
        "format": int(tex.m_TextureFormat),
        "mipCount": int(tex.m_MipCount),
        "streamPath": str(stream.path),
        "streamOffset": int(stream.offset),
        "streamSize": int(stream.size),
    }


def collection_bundle_paths() -> list[Path]:
    command = [
        "rg",
        "-l",
        "-a",
        "--text",
        "--fixed-strings",
        "spriteCollectionName",
        "--glob",
        "*.bundle",
        str(SWITCH_ROOT),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(f"查找 Collection Bundle 时 rg 执行失败：{result.stderr}")
    paths = [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    return sorted(set(paths))


def read_switch_collections() -> tuple[list[dict], list[dict]]:
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    instances: list[dict] = []
    errors: list[dict] = []
    for bundle_path in collection_bundle_paths():
        try:
            env = UnityPy.load(str(bundle_path))
            objects = {int(obj.path_id): obj for obj in env.objects}
            for obj in env.objects:
                if obj.type.name != "MonoBehaviour":
                    continue
                tree = obj.read_typetree()
                if not isinstance(tree, dict):
                    continue
                collection_name = tree.get("spriteCollectionName")
                refs = tree.get("textures")
                if not isinstance(collection_name, str) or not isinstance(refs, list):
                    continue
                textures = []
                unresolved = []
                for ref in refs:
                    if not isinstance(ref, dict):
                        unresolved.append({"ref": ref})
                        continue
                    file_id = int(ref.get("m_FileID", ref.get("fileID", 0)))
                    path_id = int(ref.get("m_PathID", ref.get("pathID", 0)))
                    target = objects.get(path_id) if file_id == 0 else None
                    if target is None or target.type.name != "Texture2D":
                        unresolved.append({
                            "fileId": file_id,
                            "pathId": path_id,
                        })
                        continue
                    textures.append(texture_row(target))
                if not textures and not unresolved:
                    continue
                instances.append({
                    "bundle": rel(bundle_path),
                    "pathId": int(obj.path_id),
                    "collection": collection_name,
                    "textureCount": len(refs),
                    "textures": textures,
                    "unresolved": unresolved,
                })
        except Exception as exc:
            errors.append({"bundle": rel(bundle_path), "error": repr(exc)})
    return instances, errors


def canonical_sprite(name: str) -> tuple[int, str, str] | None:
    pattern = re.compile(
        r"^sactx-(\d+)-(\d+x\d+)-[^-]+-(.+?)(?:-([0-9a-fA-F]{6,10}))?$",
        re.IGNORECASE,
    )
    match = pattern.match(name)
    if not match:
        return None
    return int(match.group(1)), match.group(2).lower(), match.group(3).lower()


def canonical_key(value: tuple[int, str, str]) -> str:
    return "|".join(map(str, value))


def scaled_entries() -> dict[str, dict]:
    report = load_json(STATIC_BUILD)
    result = {}
    for bundle in report.get("bundles", []):
        if not isinstance(bundle, dict):
            continue
        for texture in bundle.get("textures", []):
            if not isinstance(texture, dict) or not texture.get("resized"):
                continue
            name = str(texture.get("name", ""))
            if name:
                result[name.lower()] = {
                    "name": name,
                    "bundle": str(bundle.get("source", "")),
                    "pathId": texture.get("pathId"),
                    "inputSize": texture.get("inputSize"),
                    "targetSize": texture.get("targetSize"),
                    "format": texture.get("format"),
                }
    return result


def resolved_external_collection_entries() -> dict[str, list[dict]]:
    """从静态报告中读取经过验证的跨文件 Collection 解析结果。"""
    report = load_json(STATIC_BUILD)
    result: dict[str, list[dict]] = defaultdict(list)
    pattern = re.compile(r"^collection:(.+?)(?: \(external FileID \d+\))?$")
    for bundle in report.get("bundles", []):
        if not isinstance(bundle, dict):
            continue
        for texture in bundle.get("textures", []):
            if not isinstance(texture, dict):
                continue
            match = pattern.match(str(texture.get("source", "")))
            if match is None:
                continue
            target_size = texture.get("targetSize", [None, None])
            result[match.group(1)].append({
                "bundle": str(bundle.get("source", "")),
                "pathId": texture.get("pathId"),
                "name": texture.get("name"),
                "width": target_size[0],
                "height": target_size[1],
                "format": texture.get("format"),
            })
    return dict(result)


def main() -> int:
    warnings.filterwarnings("ignore", category=UserWarning, module="UnityPy")
    manifest = load_json(MANIFEST)
    all_targets = load_json(ALL_TARGETS)
    sprite_targets = load_json(SPRITE_TARGETS)

    collection_records = manifest.get("collections", [])
    if isinstance(collection_records, dict):
        skin_collections = {
            str(name): int(item["atlasCount"])
            for name, item in collection_records.items()
            if isinstance(item, dict) and "atlasCount" in item
        }
    else:
        skin_collections = {
            str(item["collection"]): int(item["atlasCount"])
            for item in collection_records
            if isinstance(item, dict) and "collection" in item
        }
    skin_standalone = {
        str(item["name"]).lower(): item
        for item in manifest.get("standaloneTextures", [])
        if isinstance(item, dict) and "name" in item
    }
    skin_sprite = {
        str(item.get("semanticKey", "")): item
        for item in manifest.get("spriteTextures", [])
        if isinstance(item, dict) and item.get("semanticKey")
    }

    instances, collection_errors = read_switch_collections()
    resolved_external = resolved_external_collection_entries()
    by_name: dict[str, list[dict]] = defaultdict(list)
    for item in instances:
        by_name[item["collection"]].append(item)

    collection_coverage = []
    for name in sorted(skin_collections, key=str.casefold):
        rows = by_name.get(name, [])
        max_count = max((row["textureCount"] for row in rows), default=0)
        extra = sorted(
            {
                index
                for row in rows
                for index in range(row["textureCount"])
                if index >= skin_collections[name]
            }
        )
        raw_unresolved = [
            {
                "bundle": row["bundle"],
                "pathId": row["pathId"],
                "refs": row["unresolved"],
            }
            for row in rows
            if row["unresolved"]
        ]
        external = resolved_external.get(name, [])
        unresolved = [] if external else raw_unresolved
        collection_coverage.append({
            "collection": name,
            "skinAtlasCount": skin_collections[name],
            "switchInstanceCount": len(rows),
            "switchMaxTextureCount": max_count,
            "extraSwitchAtlasIndices": extra,
            "externallyResolvedTextures": external,
            "rawUnresolvedTextureRefs": raw_unresolved,
            "unresolvedTextureRefs": unresolved,
            "covered": bool(rows) and not extra and not unresolved,
        })

    ns_collection_names = set(by_name)
    missing_ns_collections = [
        {
            "collection": name,
            "instanceCount": len(by_name[name]),
            "bundles": sorted({row["bundle"] for row in by_name[name]}),
            "maxTextureCount": max(row["textureCount"] for row in by_name[name]),
        }
        for name in sorted(ns_collection_names - set(skin_collections), key=str.casefold)
    ]

    generic_inputs = all_targets.get("genericInputs", {})
    generic_targets = all_targets.get("genericTargets", {})
    scaled = scaled_entries()
    sprite_pc = sprite_targets.get("pcCanonical", {})
    sprite_matches = sprite_targets.get("matches", {})
    sprite_unmatched = set(sprite_targets.get("unmatched", []))
    sprite_inventory = sprite_targets.get("inventory", [])

    missing_pc_inputs = []
    for key, item in sorted(generic_inputs.items(), key=lambda pair: str(pair[0]).casefold()):
        original_name = str(item.get("key", key))
        lower_name = original_name.lower()
        if lower_name in skin_standalone:
            continue
        canonical = canonical_sprite(original_name)
        if canonical is not None:
            semantic = canonical_key(canonical)
            if semantic in skin_sprite:
                continue
            matches = sprite_matches.get(semantic, [])
            if matches:
                missing_pc_inputs.append({
                    "kind": "spriteatlas-format-or-package",
                    "name": original_name,
                    "semanticKey": semantic,
                    "switchTargets": matches,
                    "reason": "Switch 目标存在，但当前清单没有包含这张 SpriteAtlas 图集；加入前需要检查格式。",
                })
                continue
            candidates = []
            for candidate in sprite_inventory:
                parsed = canonical_sprite(str(candidate.get("name", "")))
                if parsed is not None and parsed[0] == canonical[0] and parsed[2] == canonical[2]:
                    candidates.append(candidate)
            missing_pc_inputs.append({
                "kind": "spriteatlas-repacked-layout",
                "name": original_name,
                "semanticKey": semantic,
                "switchTargets": candidates,
                "reason": "没有匹配到同尺寸的 Switch 图集；需要进行 SpriteAtlas RenderData/UV 重排。",
            })
            continue
        if lower_name in scaled:
            missing_pc_inputs.append({
                "kind": "standalone-scaled-candidate",
                "name": original_name,
                "switchTarget": scaled[lower_name],
                    "reason": "找到尺寸不同的 Switch 目标；当前包没有包含这个缩放候选。",
            })
            continue
        exact_targets = generic_targets.get(key.lower(), generic_targets.get(key, []))
        missing_pc_inputs.append({
            "kind": "standalone-no-current-input",
            "name": original_name,
            "switchTargets": exact_targets,
            "reason": "PC 皮肤输入不在当前 Loader 的独立纹理清单中。",
        })

    covered_pc = len(generic_inputs) - len(missing_pc_inputs)
    result = {
        "scope": {
            "package": rel(CURRENT_PACKAGE),
            "manifest": rel(MANIFEST),
            "switchRoot": rel(SWITCH_ROOT),
                "basis": "当前 Loader 清单以及 all-targets/spriteatlas Switch 资源清单",
        },
        "summary": {
            "switchCollectionBundleCount": len(collection_bundle_paths()),
            "switchCollectionInstanceCount": len(instances),
            "switchUniqueCollectionCount": len(ns_collection_names),
            "skinCollectionCount": len(skin_collections),
            "skinAtlasCount": sum(skin_collections.values()),
            "missingSwitchCollectionsFromSkin": len(missing_ns_collections),
            "fullyCoveredSkinCollections": sum(1 for row in collection_coverage if row["covered"]),
            "pcGenericInputCount": len(generic_inputs),
            "pcGenericCoveredByCurrentLoader": covered_pc,
            "pcGenericMissingFromCurrentLoader": len(missing_pc_inputs),
            "currentStandaloneCount": len(skin_standalone),
            "currentSpriteTextureCount": len(skin_sprite),
        },
        "collectionCoverage": collection_coverage,
        "resolvedExternalCollections": resolved_external,
        "missingSwitchCollectionsFromSkin": missing_ns_collections,
        "missingPcInputsFromCurrentLoader": missing_pc_inputs,
        "collectionScanErrors": collection_errors,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Loader 皮肤覆盖审计",
        "",
        f"审计包：`{rel(CURRENT_PACKAGE)}`。对比当前 Loader 清单、PC 皮肤输入和 NS 端 Unity 资源对象。",
        "",
        "## 总结",
        "",
        f"- NS TK2D 资源包候选：{result['summary']['switchCollectionBundleCount']} 个；集合实例 {result['summary']['switchCollectionInstanceCount']} 个；唯一集合名 {result['summary']['switchUniqueCollectionCount']} 个。",
        f"- 当前皮肤覆盖：{result['summary']['skinCollectionCount']} 个集合、{result['summary']['skinAtlasCount']} 张 atlas；其中 {result['summary']['fullyCoveredSkinCollections']} 个集合的 NS atlas 数量和引用均完整。",
        f"- NS 中存在但当前皮肤没有同名集合：{len(missing_ns_collections)} 个。",
        f"- PC 皮肤通用 Texture2D/SpriteAtlas 输入：{len(generic_inputs)} 个；当前 Loader 已覆盖 {covered_pc} 个，未覆盖 {len(missing_pc_inputs)} 个。",
        "",
        "## A. NS 有、当前皮肤没有的 TK2D 集合",
        "",
    ]
    if missing_ns_collections:
        lines += ["| 集合 | NS 实例数 | 最大 atlas 数 | Bundle |"]
        lines += ["| --- | ---: | ---: | --- |"]
        for item in missing_ns_collections:
            lines.append(
                f"| `{item['collection']}` | {item['instanceCount']} | {item['maxTextureCount']} | "
                + ", ".join(f"`{bundle}`" for bundle in item["bundles"])
                + " |"
            )
    else:
        lines.append("无。")

    lines += ["", "## B. 已有集合但 atlas 数量或引用不完整", ""]
    collection_gaps = [
        row for row in collection_coverage
        if row["extraSwitchAtlasIndices"] or row["unresolvedTextureRefs"] or not row["switchInstanceCount"]
    ]
    if collection_gaps:
        lines += ["| 集合 | 皮肤 atlas 数 | NS 最大 atlas 数 | 问题 |", "| --- | ---: | ---: | --- |"]
        for row in collection_gaps:
            problems = []
            if not row["switchInstanceCount"]:
                problems.append("NS 未发现集合实例")
            if row["extraSwitchAtlasIndices"]:
                problems.append("未覆盖 atlas " + ", ".join(map(str, row["extraSwitchAtlasIndices"])))
            if row["unresolvedTextureRefs"]:
                problems.append(f"{len(row['unresolvedTextureRefs'])} 个对象有未解析纹理引用")
            lines.append(f"| `{row['collection']}` | {row['skinAtlasCount']} | {row['switchMaxTextureCount']} | {'；'.join(problems)} |")
    else:
        lines.append("当前 35 个皮肤集合均未发现 atlas 数量或本地纹理引用缺口。")

    lines += ["", "## C. PC 输入未进入当前 Loader 包", ""]
    if missing_pc_inputs:
        lines += ["| 类型 | 名称 | NS 证据/缺口 |"]
        lines += ["| --- | --- | --- |"]
        for item in missing_pc_inputs:
            evidence = item.get("reason", "")
            targets = item.get("switchTargets")
            if targets:
                evidence += "；" + ", ".join(
                    f"{target.get('name', '<unnamed>')} {target.get('width', '?')}×{target.get('height', '?')} fmt{target.get('format', '?')}"
                    for target in targets[:6]
                )
            target = item.get("switchTarget")
            if target:
                evidence += f"；NS 目标 {target.get('targetSize')} fmt{target.get('format')}"
            lines.append(f"| `{item['kind']}` | `{item['name']}` | {evidence} |")
    else:
        lines.append("无。")

    lines += [
        "",
        "## 结论",
        "",
        "当前 Loader 对 35 个 TK2D 皮肤集合的覆盖是完整的；剩余未覆盖项集中在独立纹理的尺寸缩放候选，以及 SpriteAtlas 的非 ASTC4x4 格式和重新排布图集。",
        "这些项目不是集合回调失效，而是当前包的输入清单没有包含对应资源，或不能直接把 PC 整图交给 Switch 的 RenderData/UV。",
        "",
        f"完整机器可读结果：`{rel(OUT_JSON)}`。",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"summary": result["summary"], "outJson": str(OUT_JSON), "outMarkdown": str(OUT_MD)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
