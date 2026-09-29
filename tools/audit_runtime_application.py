"""审计暂存的纹理记录能否到达当前运行时 Hook。

这项审计与包覆盖率审计有意区分：记录可能已经存在于部署包中，但由于运行时通过
另一条 Unity 赋值路径解析资源，实际仍然无法到达。
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import UnityPy


ROOT = Path(__file__).resolve().parents[2]
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
RESIZED_BUILD = ROOT / "output" / "reports" / "xingjianya-all-build.json"
OUT_JSON = ROOT / "output" / "reports" / "runtime-application-audit.json"
OUT_MD = ROOT / "output" / "reports" / "runtime-application-audit.md"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def path_id(value) -> int:
    if isinstance(value, dict):
        return int(value.get("m_PathID", value.get("pathID", 0)))
    return 0


def walk_refs(value, path=""):
    if isinstance(value, dict):
        if "m_PathID" in value or "pathID" in value:
            yield path, value
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            yield from walk_refs(child, child_path)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from walk_refs(child, f"{path}[{index}]")


def target_rows() -> dict[str, dict]:
    result: dict[str, dict] = {}
    exact = load_json(ALL_TARGETS)
    for values in exact.get("genericTargets", {}).values():
        for row in values:
            result[str(row["name"]).lower()] = {
                **row,
                "sourceKind": "精确 Texture2D 目标",
            }

    sprite_targets = load_json(SPRITE_TARGETS)
    for values in sprite_targets.get("matches", {}).values():
        for row in values:
            result[str(row["name"]).lower()] = {
                **row,
                "sourceKind": "精确 SpriteAtlas Texture2D 目标",
            }

    resized = load_json(RESIZED_BUILD)
    for bundle in resized.get("bundles", []):
        for row in bundle.get("textures", []):
            name = str(row.get("name", ""))
            if not name or not row.get("resized"):
                continue
            result[name.lower()] = {
                "bundle": str(bundle.get("source", "")),
                "pathId": row.get("pathId"),
                "name": name,
                "width": int(row["targetSize"][0]),
                "height": int(row["targetSize"][1]),
                "inputSize": row.get("inputSize"),
                "format": row.get("format"),
                "sourceKind": "可通过运行时 Hook 加载的适配尺寸目标",
            }
    return result


def find_refs(bundle: str, targets: list[dict]) -> dict[str, dict]:
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    env = UnityPy.load(bundle)
    target_objects = list(env.objects)
    target_ids = {int(row["pathId"]): row for row in targets}
    rows = {
        str(row["name"]): {
            "spriteRefs": [],
            "materialRefs": [],
            "otherRefs": [],
        }
        for row in targets
    }
    # A texture's materials can be serialized in another Bundle. Match the
    # resolved SerializedFile identity as well as PathID, not just fileID=0.
    identities = {(obj.assets_file.name.lower(), int(obj.path_id)): target_ids[int(obj.path_id)]
                  for obj in target_objects if int(obj.path_id) in target_ids}
    objects = list(target_objects)
    if Path(bundle).name == "herostatic_assets_all.bundle":
        prefab = Path(bundle).with_name("herodynamic_assets_all.bundle")
        if prefab.is_file():
            objects.extend(UnityPy.load(str(prefab)).objects)
    for obj in objects:
        if (obj.assets_file.name.lower(), int(obj.path_id)) in identities:
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        if not isinstance(tree, dict):
            continue
        object_name = str(tree.get("m_Name", tree.get("Name", "")))
        for ref_path, ref in walk_refs(tree):
            file_id = int(ref.get("m_FileID", ref.get("fileID", 0)))
            target_file = obj.assets_file.name.lower()
            if file_id:
                external = str(obj.assets_file.externals[file_id - 1].path)
                target_file = external.replace("\\", "/").rsplit("/", 1)[-1].lower()
            target_id = path_id(ref)
            target = identities.get((target_file, target_id))
            if target is None:
                continue
            name = str(target["name"])
            item = {
                "type": obj.type.name,
                "pathId": int(obj.path_id),
                "name": object_name,
                "path": ref_path,
                "serializedFile": obj.assets_file.name,
            }
            if obj.type.name == "Sprite":
                rows[name]["spriteRefs"].append(item)
            elif obj.type.name == "Material":
                rows[name]["materialRefs"].append(item)
            else:
                rows[name]["otherRefs"].append(item)
    return rows


def main() -> int:
    manifest = load_json(MANIFEST)
    records = {}
    for kind in ("standaloneTextures", "spriteTextures"):
        for row in manifest.get(kind, []):
            records[str(row["name"])] = {"kind": kind, **row}

    targets = target_rows()
    runtime_death = manifest.get("runtimeDeathAssetMapping", {})
    atlas_root = ROOT / "romfs/Data/StreamingAssets/aa/Switch/atlases_assets_assets/sprites/_atlases"
    map_mapping = runtime_death.get("map")
    if isinstance(map_mapping, dict):
        map_row = next((row for row in manifest.get("spriteTextures", [])
                        if row.get("name") == map_mapping.get("targetTexture")), None)
        if map_row is not None:
            targets[str(map_row["name"]).lower()] = {
                "name": map_row["name"],
                "pathId": int(map_mapping["targetPathId"]),
                "width": int(map_row["width"]),
                "height": int(map_row["height"]),
                "format": int(map_row["format"]),
                "bundle": str(atlas_root / "hornet_map.spriteatlas.bundle"),
                "sourceKind": "SkinLoader Map SpriteAtlas runtime PNG",
            }
    core_mapping = runtime_death.get("core")
    if isinstance(core_mapping, dict):
        for core_row in core_mapping.get("generated", []):
            targets[str(core_row["name"]).lower()] = {
                "name": core_row["name"],
                "pathId": int(core_row["pathId"]),
                "width": int(core_row["width"]),
                "height": int(core_row["height"]),
                "format": int(core_row["format"]),
                "bundle": str(atlas_root / "core.spriteatlas.bundle"),
                "sourceKind": "SkinLoader Core SpriteAtlas runtime PNG",
            }
    by_bundle: defaultdict[str, list[dict]] = defaultdict(list)
    for name, record in records.items():
        target = targets.get(name.lower())
        if target is not None:
            by_bundle[str(target["bundle"])].append(target)

    refs_by_name = {}
    for bundle, bundle_targets in by_bundle.items():
        refs_by_name.update(find_refs(bundle, bundle_targets))

    # SpriteAtlas texture ownership lives in SpriteAtlas.m_RenderDataMap,
    # rather than a direct Sprite.m_RD.texture pointer in these files.
    def add_atlas_sprite_mapping(texture_name: str, sprite_name: str,
                                 render_data_key: object, bundle: str) -> None:
        refs = refs_by_name.setdefault(texture_name, {
            "spriteRefs": [], "materialRefs": [], "otherRefs": [],
        })
        refs["spriteRefs"].append({
            "type": "SpriteAtlas RenderDataMap",
            "pathId": None,
            "name": sprite_name,
            "path": "m_RenderDataMap -> Texture2D",
            "serializedFile": Path(bundle).name,
            "renderDataKey": render_data_key,
            "evidence": "构建时由 UnityPy 解析 SpriteAtlas.m_RenderDataMap 并匹配 PC/Switch RenderDataKey",
        })

    if isinstance(map_mapping, dict):
        add_atlas_sprite_mapping(
            str(map_mapping["targetTexture"]), str(map_mapping["spriteName"]),
            map_mapping["renderDataKey"], str(atlas_root / "hornet_map.spriteatlas.bundle"),
        )
    if isinstance(core_mapping, dict):
        for sprite_mapping in core_mapping.get("spriteMappings", []):
            add_atlas_sprite_mapping(
                str(sprite_mapping["targetTexture"]), str(sprite_mapping["name"]),
                sprite_mapping["renderDataKey"], str(atlas_root / "core.spriteatlas.bundle"),
            )

    rows = []
    for name, record in sorted(records.items(), key=lambda item: item[0].lower()):
        target = targets.get(name.lower())
        refs = refs_by_name.get(name, {"spriteRefs": [], "materialRefs": [], "otherRefs": []})
        if target is None:
            decision = "没有 Switch 目标证据"
        elif record["kind"] == "spriteTextures" and refs["spriteRefs"] and refs["materialRefs"]:
            decision = "Sprite Hook 直接匹配，Material Hook 回退到 sprite/；实际应用待运行日志确认"
        elif record["kind"] == "spriteTextures" and refs["materialRefs"]:
            decision = "Material Hook 回退到 sprite/；实际应用待运行日志确认"
        elif record["kind"] == "spriteTextures" and refs["spriteRefs"]:
            decision = "Sprite/Atlas 纹理目标匹配；setter 是否触发及应用结果待运行日志确认"
        elif record["kind"] == "standaloneTextures" and refs["spriteRefs"]:
            decision = (
                "Sprite Hook 先查 sprite/，再回退 standalone/；应用结果待运行日志确认"
            )
        elif record["kind"] == "standaloneTextures" and refs["materialRefs"]:
            decision = "Material 目录匹配；setter 是否触发及应用结果待运行日志确认"
        else:
            decision = "本地序列化引用尚未证明运行时路径"

        rows.append({
            "name": name,
            "manifestKind": record["kind"],
            "manifestFile": record.get("file"),
            "target": target,
            "spriteRefs": refs["spriteRefs"],
            "materialRefs": refs["materialRefs"],
            "otherRefs": refs["otherRefs"],
            "runtimeDecision": decision,
        })

    result = {
        "package": str(CURRENT_PACKAGE),
        "manifest": str(MANIFEST),
        "runtimeRules": {
            "standalone": "OnStandaloneTextureAssigned -> FindStandalone，未命中时回退 FindSpriteTexture；由 Material.set_mainTexture 调用",
            "sprite": "OnSpriteAssigned -> FindSpriteTexture，未命中时回退 FindStandalone；由 SpriteRenderer.set_sprite 和 UI.Image.set_sprite 调用",
        },
        "records": rows,
        "summary": {
            "manifestRecords": len(rows),
            "standaloneRecords": sum(row["manifestKind"] == "standaloneTextures" for row in rows),
            "spriteRecords": sum(row["manifestKind"] == "spriteTextures" for row in rows),
            "standaloneSpriteBacked": sum(
                row["manifestKind"] == "standaloneTextures" and bool(row["spriteRefs"])
                for row in rows
            ),
            "standaloneMaterialBacked": sum(
                row["manifestKind"] == "standaloneTextures" and bool(row["materialRefs"])
                for row in rows
            ),
            "spriteMaterialBacked": sum(
                row["manifestKind"] == "spriteTextures" and bool(row["materialRefs"])
                for row in rows
            ),
            "standaloneSpriteFallback": sum(
                row["manifestKind"] == "standaloneTextures" and bool(row["spriteRefs"])
                for row in rows
            ),
        },
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 运行时应用路径审计",
        "",
        "这份报告检查运行时 Hook 的目录匹配与本地序列化引用。序列化引用不能证明 setter 被调用。",
        "",
        f"- 当前包：`{CURRENT_PACKAGE.name}`",
        f"- 清单记录：{len(rows)}（独立纹理={result['summary']['standaloneRecords']}，Sprite={result['summary']['spriteRecords']}）",
        f"- 独立纹理但由 Sprite 引用：{result['summary']['standaloneSpriteBacked']}",
        f"- 独立纹理且发现 Material 引用：{result['summary']['standaloneMaterialBacked']}",
        "",
        "## 结论",
        "",
        "Sprite Hook 先查找 `sprite/`，未命中时回退到 `standalone/`；Material Hook",
        "先查找 `standalone/`，未命中时回退到 `sprite/`。两条运行时入口都可以找到",
        "按另一类引用归档的纹理；是否实际执行替换仍需 `operation=applied` 日志确认。",
        "",
        "## 记录",
        "",
        "| 名称 | 清单类别 | NS 已检查引用 | 当前应用入口结论 |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        refs = []
        if row["spriteRefs"]:
            refs.append(f"Sprite×{len(row['spriteRefs'])}")
        if row["materialRefs"]:
            refs.append(f"Material×{len(row['materialRefs'])}")
        if row["otherRefs"]:
            refs.append(f"其他×{len(row['otherRefs'])}")
        target = row["target"]
        if target is None:
            target_text = "无目标"
        else:
            target_text = f"{target['width']}×{target['height']} fmt{target.get('format', '?')}"
        lines.append(
            f"| `{row['name']}` | `{row['manifestKind']}` | "
            f"{', '.join(refs) or '未发现本地引用'}（{target_text}） | {row['runtimeDecision']} |"
        )
    rows_by_name = {row["name"]: row for row in rows}
    lines += ["", "## 点名资源", ""]
    for name in (
        "Hornet_death_pieces_0000s_0001_6",
        "Hornet_death_cocoon_particle_chunks",
        "Hornet_death_spiders",
    ):
        row = rows_by_name.get(name)
        if row is None:
            lines.append(f"- `{name}`：当前包没有该纹理记录。")
        else:
            lines.append(
                f"- `{name}`：`{row['manifestKind']}`；{row['runtimeDecision']}。"
            )
    lines += [
        "",
        "运行时资源需要 `operation=applied` 与游戏显示确认。",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"summary": result["summary"], "json": str(OUT_JSON), "markdown": str(OUT_MD)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
