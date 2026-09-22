"""识别引用 Bone Beast 子呼叫资源的场景 Bundle。"""

from __future__ import annotations

import json
from pathlib import Path

import UnityPy


ROOT = Path(__file__).resolve().parents[2]
SWITCH_ROOT = ROOT / "romfs" / "Data" / "StreamingAssets" / "aa" / "Switch"
SCENE_BUNDLE = SWITCH_ROOT / "scenes_scenes_scenes" / "bellway_centipede_additive.bundle"
OUT = ROOT / "output" / "reports" / "bone-beast-scene-identification.json"
TERMS = (
    "bone beast",
    "bone_beast",
    "beastling",
    "fast travel",
    "fast_travel",
    "child call",
    "children teleport",
    "bellway",
    "centipede",
    "somersault",
)


def load(path: Path):
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    return UnityPy.load(str(path))


def path_id(value) -> int:
    return int(value.get("m_PathID", 0)) if isinstance(value, dict) else 0


def strings(value, prefix: str = "") -> list[dict[str, str]]:
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(strings(child, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(strings(child, f"{prefix}[{index}]"))
    elif isinstance(value, str):
        lowered = value.lower()
        if any(term in lowered for term in TERMS):
            found.append({"field": prefix, "value": value})
    return found


def fsm_summary(tree) -> list[dict]:
    """返回用于场景级证据的精简 PlayMaker 状态和动作名称。"""
    if not isinstance(tree, dict):
        return []
    fsm = tree.get("fsm")
    if not isinstance(fsm, dict):
        return []
    states = fsm.get("states")
    if not isinstance(states, list):
        return []
    result = []
    for index, state in enumerate(states):
        if not isinstance(state, dict):
            continue
        action_data = state.get("actionData")
        if not isinstance(action_data, dict):
            action_data = {}
        actions = action_data.get("actionNames")
        result.append({
            "index": index,
            "name": state.get("name", ""),
            "actions": actions if isinstance(actions, list) else [],
            "matchingStrings": strings(state),
        })
    return result


def main() -> int:
    env = load(SCENE_BUNDLE)
    objects = {int(obj.path_id): obj for obj in env.objects}
    rows = []
    for obj in env.objects:
        try:
            tree = obj.read_typetree()
        except Exception:
            tree = None
        row = {
            "pathId": int(obj.path_id),
            "type": obj.type.name,
            "name": "",
            "matchingStrings": strings(tree) if isinstance(tree, (dict, list)) else [],
        }
        if isinstance(tree, dict):
            row["name"] = str(tree.get("m_Name") or tree.get("name") or "")
            if "m_GameObject" in tree:
                row["gameObjectPathId"] = path_id(tree.get("m_GameObject"))
            if int(obj.path_id) in (187, 189):
                row["fsmStates"] = fsm_summary(tree)
        if row["name"] or row["matchingStrings"]:
            rows.append(row)

    containers = []
    try:
        for key, pptr in env.container.items():
            containers.append({"key": key, "pathId": int(pptr.path_id), "type": pptr.type.name})
    except Exception as exc:
        containers.append({"error": repr(exc)})

    result = {
        "bundle": str(SCENE_BUNDLE),
        "objectCount": len(env.objects),
        "containerCount": len(containers),
        "containers": containers,
        "matchingObjects": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "bundle": str(SCENE_BUNDLE),
        "objectCount": len(env.objects),
        "containerCount": len(containers),
        "matchingObjects": rows,
        "out": str(OUT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
