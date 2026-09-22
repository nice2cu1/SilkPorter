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
STATIC_BUILD = ROOT / "output" / "reports" / "xingjianya-all-build.json"
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
    elif isinstance(value, list):
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

    static = load_json(STATIC_BUILD)
    for bundle in static.get("bundles", []):
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
                "sourceKind": "缩放 Texture2D 目标",
            }
    return result


def find_refs(bundle: str, targets: list[dict]) -> dict[str, dict]:
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    env = UnityPy.load(bundle)
    objects = list(env.objects)
    target_ids = {int(row["pathId"]): row for row in targets}
    rows = {
        str(row["name"]): {
            "spriteRefs": [],
            "materialRefs": [],
            "otherRefs": [],
        }
        for row in targets
    }
    for obj in objects:
        if int(obj.path_id) in target_ids:
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        if not isinstance(tree, dict):
            continue
        object_name = str(tree.get("m_Name", tree.get("Name", "")))
        for ref_path, ref in walk_refs(tree):
            if int(ref.get("m_FileID", ref.get("fileID", 0))) != 0:
                continue
            target_id = path_id(ref)
            target = target_ids.get(target_id)
            if target is None:
                continue
            name = str(target["name"])
            item = {
                "type": obj.type.name,
                "pathId": int(obj.path_id),
                "name": object_name,
                "path": ref_path,
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
    by_bundle: defaultdict[str, list[dict]] = defaultdict(list)
    for name, record in records.items():
        target = targets.get(name.lower())
        if target is not None:
            by_bundle[str(target["bundle"])].append(target)

    refs_by_name = {}
    for bundle, bundle_targets in by_bundle.items():
        refs_by_name.update(find_refs(bundle, bundle_targets))

    rows = []
    for name, record in sorted(records.items(), key=lambda item: item[0].lower()):
        target = targets.get(name.lower())
        refs = refs_by_name.get(name, {"spriteRefs": [], "materialRefs": [], "otherRefs": []})
        if target is None:
            decision = "没有 Switch 目标证据"
        elif record["kind"] == "spriteTextures" and refs["spriteRefs"]:
            decision = "可通过当前 SpriteRenderer/UI.Image Hook 应用"
        elif record["kind"] == "standaloneTextures" and refs["spriteRefs"]:
            decision = (
                "无法通过当前 SpriteRenderer/UI.Image Hook 到达："
                "Hook 查找 spriteTextures，但该记录属于 standaloneTextures"
            )
        elif record["kind"] == "standaloneTextures" and refs["materialRefs"]:
            decision = "可通过当前 Material.set_mainTexture Hook 应用"
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
            "standalone": "OnStandaloneTextureAssigned -> FindStandalone；由 Material.set_mainTexture 调用",
            "sprite": "OnSpriteAssigned -> FindSpriteTexture；由 SpriteRenderer.set_sprite 和 UI.Image.set_sprite 调用",
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
            "notReachedByCurrentSpriteHook": sum(
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
        "这份报告检查的是部署包记录能否进入当前 Loader 的实际 Hook。",
        "不是单纯检查 NS 是否存在同名 Texture2D。",
        "",
        f"- 当前包：`{CURRENT_PACKAGE.name}`",
        f"- 清单记录：{len(rows)}（独立纹理={result['summary']['standaloneRecords']}，Sprite={result['summary']['spriteRecords']}）",
        f"- 独立纹理但由 Sprite 引用：{result['summary']['standaloneSpriteBacked']}",
        f"- 独立纹理且发现 Material 引用：{result['summary']['standaloneMaterialBacked']}",
        "",
        "## 结论",
        "",
        "当前 `OnSpriteAssigned` 只执行 `FindSpriteTexture`；因此被 NS `Sprite` 使用、",
        "却放在 `standaloneTextures` 的记录，不会在 SpriteRenderer/UI.Image 路径中应用。",
        "",
        "## 记录",
        "",
        "| 名称 | 清单类别 | NS 本地引用 | 当前运行时结论 |",
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
    lines += [
        "",
        "## 点名的三张",
        "",
        "- `Hornet_death_pieces_0000s_0001_6`：当前包有记录，但 NS 中是 Sprite→Texture2D；当前 Sprite Hook 不查 standalone。",
        "- `Hornet_death_cocoon_particle_chunks`：同上。",
        "- `Hornet_death_spiders`：当前包没有记录；它是 PC 37×148 到 NS 25×100 的缩放候选，只有历史 L8 清单包含。即使放入历史候选，仍需先修正 Sprite/standalone 分类。",
        "",
        "静态引用只证明资源类型和当前代码的可达性；最终的实际应用仍应以游戏日志中的 `operation=applied` 为准。",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"summary": result["summary"], "json": str(OUT_JSON), "markdown": str(OUT_MD)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
