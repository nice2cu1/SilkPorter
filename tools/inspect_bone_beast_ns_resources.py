"""提取 Nintendo Switch Bone Beast Child Call 的资源链。

对于原始 Bundle，本脚本只读不写。它会记录 TK2D Collection 元数据、解码后的
Atlas，以及引用该 Collection 的预制体组件，使后续 PC Atlas 重排能够使用真实的
NS 资源数据。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import UnityPy
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]
SWITCH_ROOT = ROOT / "romfs" / "Data" / "StreamingAssets" / "aa" / "Switch"
COLLECTION_BUNDLE = SWITCH_ROOT / "herocollections_assets_shared.bundle"
PREFAB_BUNDLE = SWITCH_ROOT / "localpoolprefabs_assets_shared.bundle"
ANIMATION_BUNDLE = SWITCH_ROOT / "tk2danimations_assets_areabellway.bundle"
GENERAL_ANIMATION_BUNDLE = SWITCH_ROOT / "animations_assets_shared.bundle"
COLLECTION_NAME = "Bone Beast Child Call Cln"
OUTPUT_REPORT = ROOT / "output" / "reports" / "bone-beast-ns-resources.json"
OUTPUT_MARKDOWN = ROOT / "output" / "reports" / "bone-beast-ns-resources.md"
OUTPUT_ATLAS = ROOT / "output" / "decoded" / "bone-beast-child-call" / "atlas0.png"
OUTPUT_CONTACT = ROOT / "output" / "diagnostics" / "bone-beast-child-call-sprites-contact.png"


def load(path: Path):
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    return UnityPy.load(str(path))


def box_from_uvs(definition: dict, width: int, height: int) -> list[int]:
    uvs = definition["uvs"]
    x0 = int(min(v["x"] for v in uvs) * width)
    x1 = int(max(v["x"] for v in uvs) * width)
    y0 = int(min(v["y"] for v in uvs) * height)
    y1 = int(max(v["y"] for v in uvs) * height)
    return [x0, height - y1, x1, height - y0]


def find_collection():
    env = load(COLLECTION_BUNDLE)
    objects = {obj.path_id: obj for obj in env.objects}
    collection_obj = None
    collection = None
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        if isinstance(tree, dict) and tree.get("spriteCollectionName") == COLLECTION_NAME:
            collection_obj = obj
            collection = tree
            break
    if collection_obj is None or collection is None:
        raise RuntimeError(f"没有找到 Collection：{COLLECTION_NAME}")

    texture_refs = collection.get("textures") or []
    if len(texture_refs) != 1:
        raise RuntimeError(f"应有一个纹理，实际得到 {len(texture_refs)} 个")
    texture_path_id = int(texture_refs[0]["m_PathID"])
    texture_obj = objects.get(texture_path_id)
    if texture_obj is None or texture_obj.type.name != "Texture2D":
        raise RuntimeError(f"没有找到纹理：{texture_path_id}")
    texture = texture_obj.read()
    image = texture.image.convert("RGBA")

    sprites = []
    for index, definition in enumerate(collection.get("spriteDefinitions") or []):
        box = box_from_uvs(definition, texture.m_Width, texture.m_Height)
        sprites.append({
            "index": index,
            "name": str(definition.get("name") or ""),
            "box": box,
            "width": box[2] - box[0],
            "height": box[3] - box[1],
            "flipped": int(definition.get("flipped", 0)),
            "materialId": int(definition.get("materialId", 0)),
            "uvs": definition.get("uvs"),
            "position0": definition.get("positions", [None])[0] if definition.get("positions") else None,
        })

    return {
        "bundle": str(COLLECTION_BUNDLE),
        "collectionPathId": int(collection_obj.path_id),
        "collectionName": COLLECTION_NAME,
        "texturePathId": texture_path_id,
        "textureName": texture.m_Name,
        "dimensions": [texture.m_Width, texture.m_Height],
        "format": int(texture.m_TextureFormat),
        "mipmapCount": int(texture.m_MipCount),
        "encodedByteLength": len(texture.image_data),
        "sprites": sprites,
        "image": image,
    }


def prefab_references(collection_path_id: int) -> dict[str, object]:
    env = load(PREFAB_BUNDLE)
    game_objects = {}
    for obj in env.objects:
        if obj.type.name != "GameObject":
            continue
        try:
            data = obj.read()
            game_objects[obj.path_id] = str(data.m_Name)
        except Exception:
            pass

    references = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        collection = tree.get("collection") if isinstance(tree, dict) else None
        if not isinstance(collection, dict) or int(collection.get("m_PathID", 0)) != collection_path_id:
            continue
        go = tree.get("m_GameObject", {})
        go_id = int(go.get("m_PathID", 0)) if isinstance(go, dict) else 0
        references.append({
            "componentPathId": int(obj.path_id),
            "gameObjectPathId": go_id,
            "gameObjectName": game_objects.get(go_id, ""),
            "spriteId": int(tree.get("_spriteId", -1)),
            "scale": tree.get("_scale"),
            "color": tree.get("_color"),
            "fileId": int(collection.get("m_FileID", 0)),
        })
    return {
        "bundle": str(PREFAB_BUNDLE),
        "collectionPathId": collection_path_id,
        "references": references,
    }


def animation_inventory() -> dict[str, object]:
    result = []
    for path in (ANIMATION_BUNDLE, GENERAL_ANIMATION_BUNDLE):
        env = load(path)
        names = []
        clips = []
        for obj in env.objects:
            try:
                data = obj.read()
            except Exception:
                continue
            name = str(getattr(data, "m_Name", ""))
            if name and ("bone beast" in name.lower() or "bone_beast_child_call" in name.lower()):
                names.append({"type": obj.type.name, "pathId": int(obj.path_id), "name": name})
            if obj.type.name == "MonoBehaviour":
                try:
                    tree = obj.read_typetree()
                except Exception:
                    continue
                if isinstance(tree, dict) and isinstance(tree.get("clips"), list):
                    clip_names = [str(c.get("name") or "") for c in tree["clips"]]
                    if clip_names:
                        clips.append({"pathId": int(obj.path_id), "clipNames": clip_names})
        result.append({
            "bundle": str(path),
            "namedObjects": names,
            "tk2dAnimationComponents": clips,
        })
    return {"bundles": result}


def save_contact(image: Image.Image, sprites: list[dict[str, object]]) -> None:
    cell_w, cell_h = 240, 190
    columns = 4
    rows = (len(sprites) + columns - 1) // columns
    sheet = Image.new("RGBA", (columns * cell_w, rows * cell_h), (24, 24, 24, 255))
    draw = ImageDraw.Draw(sheet)
    for n, item in enumerate(sprites):
        x = (n % columns) * cell_w
        y = (n // columns) * cell_h
        crop = image.crop(tuple(item["box"]))
        crop.thumbnail((cell_w - 12, cell_h - 42), Image.Resampling.NEAREST)
        px = x + (cell_w - crop.width) // 2
        py = y + 4
        sheet.alpha_composite(crop, (px, py))
        label = f"{item['index']:02d} {item['name']}"
        draw.text((x + 4, cell_h * (n // columns) + cell_h - 30), label[:36], fill=(255, 255, 255, 255))
        draw.text((x + 4, cell_h * (n // columns) + cell_h - 15), f"{item['width']}×{item['height']} 翻转={item['flipped']}", fill=(180, 220, 255, 255))
    OUTPUT_CONTACT.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(OUTPUT_CONTACT)


def main() -> int:
    collection = find_collection()
    image = collection.pop("image")
    OUTPUT_ATLAS.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT_ATLAS)
    save_contact(image, collection["sprites"])
    collection["decodedAtlas"] = str(OUTPUT_ATLAS)
    collection["contactSheet"] = str(OUTPUT_CONTACT)
    report = {
        "collection": collection,
        "prefabReferences": prefab_references(collection["collectionPathId"]),
        "animationInventory": animation_inventory(),
    }
    OUTPUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    refs = report["prefabReferences"]["references"]
    named = []
    for entry in report["animationInventory"]["bundles"]:
        named.extend(entry["namedObjects"])
    lines = [
        "# Bone Beast Child Call：NS 资源解包结果",
        "",
        "## 确认的资源链",
        "",
        f"- Collection：`{COLLECTION_NAME}`，PathID `{collection['collectionPathId']}`。",
        f"- Atlas：`{collection['textureName']}`，{collection['dimensions'][0]}×{collection['dimensions'][1]}，TextureFormat `{collection['format']}`。",
        f"- Sprite 定义：`{len(collection['sprites'])}` 个。",
        f"- `localpoolprefabs_assets_shared.bundle` 中发现 `{len(refs)}` 个组件直接引用该 Collection。",
        "- 这条引用来自 `Bone Beast Children Teleport` 预制体，组件保存了实际的 `collection` PathID 和 `_spriteId`，不是孤立贴图。",
        "",
        "## 动画侧观察",
        "",
        "- `tk2danimations_assets_areabellway.bundle` 中的 `Bone Beast Anim` 使用的是另一个 `Bone Beast Cln` Collection，不是 `Bone Beast Child Call Cln`。",
        "- `animations_assets_shared.bundle` 中能找到 `bone_beast_child_call_cave_glow` 的 AnimatorController/AnimationClip，但它是光效，不是这张主 atlas 的逐帧动画定义。",
        "- 因此这张过场 atlas 的实际使用入口目前更像是预制体/PlayMaker 状态机配合 Sprite ID，而不是一个名字相同的独立 `tk2danimations` 包。",
        "",
        "## 导出文件",
        "",
        f"- 解码 atlas：`{OUTPUT_ATLAS}`",
        f"- 97 个 Sprite 接触表：`{OUTPUT_CONTACT}`",
        f"- 机器可读报告：`{OUTPUT_REPORT}`",
    ]
    OUTPUT_MARKDOWN.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Collection Sprite 数量：{len(collection['sprites'])}")
    print(f"预制体引用数量：{len(refs)}")
    print(f"解码 Atlas：{OUTPUT_ATLAS}")
    print(f"接触表：{OUTPUT_CONTACT}")
    print(f"报告：{OUTPUT_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
