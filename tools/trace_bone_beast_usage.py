"""跟踪 Bone Beast Child Call 的运行时对象和 PlayMaker 引用。"""

from __future__ import annotations

import json
from pathlib import Path

import UnityPy


ROOT = Path(__file__).resolve().parents[2]
SWITCH_ROOT = ROOT / "romfs" / "Data" / "StreamingAssets" / "aa" / "Switch"
PREFAB_BUNDLE = SWITCH_ROOT / "localpoolprefabs_assets_shared.bundle"
TARGET_COLLECTION_PATH_ID = 2368965096697533012
OUT = ROOT / "output" / "reports" / "bone-beast-usage-trace.json"


def load(path: Path):
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    return UnityPy.load(str(path))


def path_id(value) -> int:
    if isinstance(value, dict):
        return int(value.get("m_PathID", 0))
    return 0


def field(obj: dict, name: str, default=None):
    value = obj.get(name, default) if isinstance(obj, dict) else default
    return value


def collect_objects(env):
    by_id = {int(obj.path_id): obj for obj in env.objects}
    trees = {}
    for obj in env.objects:
        try:
            if obj.type.name in {"GameObject", "Transform", "MonoBehaviour", "AnimationClip", "AnimatorController"}:
                trees[int(obj.path_id)] = obj.read_typetree()
        except Exception:
            pass
    return by_id, trees


def game_object_component_map(trees):
    result = {}
    for object_id, tree in trees.items():
        if not isinstance(tree, dict) or "m_Component" not in tree or "m_Name" not in tree:
            continue
        for component in tree.get("m_Component") or []:
            component_id = path_id(component.get("component")) if isinstance(component, dict) else 0
            if component_id:
                result[component_id] = object_id
    return result


def hierarchy(trees, game_objects, transforms, object_id: int) -> list[str]:
    chain = []
    seen = set()
    current = object_id
    while current and current not in seen:
        seen.add(current)
        tree = trees.get(current) or {}
        chain.append(str(tree.get("m_Name", current)))
        transform_id = next((tid for tid, t in transforms.items() if path_id((t or {}).get("m_GameObject")) == current), 0)
        if not transform_id:
            break
        parent = path_id((transforms[transform_id] or {}).get("m_Father"))
        if not parent:
            break
        current = path_id((trees.get(parent) or {}).get("m_GameObject"))
    return list(reversed(chain))


def compact_string_params(tree: dict) -> list[dict]:
    result = []
    for item in tree.get("fsmStringParams") or []:
        if isinstance(item, dict):
            result.append({k: item.get(k) for k in ("name", "value") if k in item})
    return result


def compact_action(action: dict, string_params: list[dict]) -> dict:
    if not isinstance(action, dict):
        return {}
    out = {}
    for key in ("type", "enabled", "isLooping", "animLibName", "clipName", "animationName", "name"):
        if key in action:
            out[key] = action[key]
    # PlayMaker 的动作数据通常通过 fsmStringParams 间接保存字符串字段。
    # 保留字段元数据，以便后续解析映射关系。
    if "fields" in action:
        out["fields"] = action["fields"]
    for key, value in action.items():
        if key in out or key in {"fields", "m_GameObject", "m_Enabled", "m_EditorHideFlags"}:
            continue
        if isinstance(value, (str, int, float, bool)) and ("clip" in key.lower() or "anim" in key.lower() or "name" in key.lower()):
            out[key] = value
    return out


def extract_fsm(tree: dict) -> dict:
    # PlayMaker 在 UnityPy 类型树的 `fsm` 子节点下保存实际 FSM 数据；
    # 保留组件包装层与实际数据的分离关系。
    payload = tree.get("fsm") if isinstance(tree.get("fsm"), dict) else tree
    states_out = []
    state_shapes = []
    for state in payload.get("fsmStates") or payload.get("states") or []:
        if not isinstance(state, dict):
            continue
        action_data = state.get("actionData") if isinstance(state.get("actionData"), dict) else {}
        state_shapes.append({
            "stateName": state.get("name"),
            "keys": sorted(state.keys()),
            "actionDataType": type(state.get("actionData")).__name__,
            "actionDataKeys": sorted(action_data.keys()),
            "actionNames": action_data.get("actionNames"),
            "actionStartIndex": action_data.get("actionStartIndex"),
            "paramName": action_data.get("paramName"),
            "fsmStringParams": action_data.get("fsmStringParams"),
        })
        actions = []
        for action in state.get("actions") or []:
            if isinstance(action, dict):
                compact = compact_action(action, payload.get("fsmStringParams") or payload.get("stringParams") or [])
                if compact:
                    actions.append(compact)
        if actions:
            states_out.append({"name": state.get("name"), "actions": actions})
    return {
        "name": payload.get("Name") or payload.get("name"),
        "startState": payload.get("startState"),
        "stringParams": compact_string_params(payload),
        "payloadKeys": sorted(payload.keys()),
        "stateShapes": state_shapes[:8],
        "states": states_out,
    }


def main() -> int:
    env = load(PREFAB_BUNDLE)
    by_id, trees = collect_objects(env)
    game_objects = {oid: tree for oid, tree in trees.items() if isinstance(tree, dict) and "m_Component" in tree and "m_Name" in tree}
    transforms = {oid: tree for oid, tree in trees.items() if isinstance(tree, dict) and "m_Father" in tree and "m_GameObject" in tree}
    component_to_go = game_object_component_map(trees)

    target_components = []
    target_go_ids = set()
    for object_id, tree in trees.items():
        if not isinstance(tree, dict) or "collection" not in tree:
            continue
        if path_id(tree.get("collection")) != TARGET_COLLECTION_PATH_ID:
            continue
        go_id = path_id(tree.get("m_GameObject"))
        target_go_ids.add(go_id)
        target_components.append({
            "componentPathId": object_id,
            "gameObjectPathId": go_id,
            "gameObjectName": (game_objects.get(go_id) or {}).get("m_Name", ""),
            "hierarchy": hierarchy(trees, game_objects, transforms, go_id),
            "spriteId": tree.get("_spriteId"),
            "treeKeys": sorted(tree.keys()),
        })

    # 清点完整的预制体子树，以确认子 Sprite 是由 TK2D Animator、Animator，
    # 还是仅由 FSM 驱动。
    child_ids = set()
    for go_id in game_objects:
        if go_id in target_go_ids:
            child_ids.add(go_id)
    changed = True
    while changed:
        changed = False
        for transform_id, transform in transforms.items():
            parent_go = path_id((trees.get(path_id((transform or {}).get("m_Father"))) or {}).get("m_GameObject"))
            go_id = path_id((transform or {}).get("m_GameObject"))
            if parent_go in child_ids and go_id not in child_ids:
                child_ids.add(go_id)
                changed = True
    inventory = []
    for go_id in sorted(child_ids):
        go = game_objects.get(go_id) or {}
        components = []
        for component in go.get("m_Component") or []:
            component_id = path_id(component.get("component")) if isinstance(component, dict) else 0
            component_obj = by_id.get(component_id)
            component_tree = trees.get(component_id)
            components.append({
                "pathId": component_id,
                "type": component_obj.type.name if component_obj else "",
                "name": (component_tree or {}).get("m_Name", "") if isinstance(component_tree, dict) else "",
                "keys": sorted(component_tree.keys()) if isinstance(component_tree, dict) else [],
                "selected": {
                    key: component_tree.get(key)
                    for key in ("library", "defaultClipId", "playAutomatically", "playAutomaticallyOnEnable", "spriteOverride")
                    if isinstance(component_tree, dict) and key in component_tree
                },
            })
        inventory.append({
            "pathId": go_id,
            "name": go.get("m_Name", ""),
            "hierarchy": hierarchy(trees, game_objects, transforms, go_id),
            "components": components,
        })

    fsms = []
    for object_id, tree in trees.items():
        if not isinstance(tree, dict) or not any("fsm" in key.lower() for key in tree):
            continue
        go_id = path_id(tree.get("m_GameObject"))
        item = extract_fsm(tree)
        item.update({
            "componentPathId": object_id,
            "gameObjectPathId": go_id,
            "gameObjectName": (game_objects.get(go_id) or {}).get("m_Name", ""),
            "hierarchy": hierarchy(trees, game_objects, transforms, go_id),
            "treeKeys": sorted(tree.keys()),
        })
        # 保留相关层级中的 FSM，以及包含 TK2D 动作或子呼叫名称的 FSM。
        text = json.dumps(item, ensure_ascii=False).lower()
        if go_id in target_go_ids or "tk2d" in text or "bone beast" in text or "child" in text:
            fsms.append(item)

    result = {
        "bundle": str(PREFAB_BUNDLE),
        "environmentFiles": [repr(item) for item in getattr(env, "files", [])],
        "serializedFiles": [
            {
                "name": str(getattr(item, "name", "")),
                "externals": [
                    {"path": str(getattr(external, "path", "")), "guid": str(getattr(external, "guid", ""))}
                    for external in getattr(item, "externals", [])
                ],
            }
            for item in getattr(getattr(env, "file", None), "files", {}).values()
            if hasattr(item, "externals")
        ],
        "targetCollectionPathId": TARGET_COLLECTION_PATH_ID,
        "targetComponents": target_components,
        "prefabSubtree": inventory,
        "fsmCount": len(fsms),
        "fsms": fsms,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"targetComponents": target_components, "fsmCount": len(fsms), "out": str(OUT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
