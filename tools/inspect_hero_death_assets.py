"""Read-only inventory of hero death/corpse prefab and rendering references."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import UnityPy


ROOT = Path(__file__).resolve().parents[2]
SWITCH = ROOT / "romfs/Data/StreamingAssets/aa/Switch"
DEFAULT_BUNDLES = [
    "herostatic_assets_all.bundle",
    "herodynamic_assets_all.bundle",
    "heroloading_assets_all.bundle",
    "globalpoolprefabs_assets_all.bundle",
    "coremanagers_assets_globalpool.bundle",
    "localpoolprefabs_assets_shared.bundle",
    "scenes_scenes_scenes/bellway_centipede_additive.bundle",
]
OUT = ROOT / "output/reports/hero-death-assets/inventory.json"
TERMS = ("death", "corpse", "cocoon")


def encode_extra(value):
    if isinstance(value, (bytes, bytearray)):
        return {"bytesHex": value.hex()}
    raise TypeError(f"Unsupported report value: {type(value).__name__}")


def strings(value, field=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from strings(child, f"{field}.{key}" if field else key)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from strings(child, f"{field}[{index}]")
    elif isinstance(value, str) and any(term in value.lower() for term in TERMS):
        yield {"field": field, "value": value}


def refs(value, field=""):
    if isinstance(value, dict):
        if "m_PathID" in value:
            if value["m_PathID"]:
                yield {"field": field, **value}
        else:
            for key, child in value.items():
                yield from refs(child, f"{field}.{key}" if field else key)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from refs(child, f"{field}[{index}]")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", action="append")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    result = []
    for relative in args.bundle or DEFAULT_BUNDLES:
        path = SWITCH / relative
        env = UnityPy.load(str(path))
        assets = {}
        objects = []
        matches = []
        errors = []
        for obj in env.objects:
            asset = obj.assets_file
            if asset.name not in assets:
                assets[asset.name] = {
                    "externals": [str(external.path) for external in asset.externals]
                }
            try:
                tree = obj.read_typetree()
            except Exception as exc:
                errors.append({"file": asset.name, "pathId": obj.path_id,
                               "type": obj.type.name, "error": str(exc)})
                continue
            row = {"file": asset.name, "pathId": obj.path_id,
                   "type": obj.type.name,
                   "name": tree.get("m_Name", tree.get("name", "")),
                   "refs": list(refs(tree))}
            if obj.type.name == "MonoScript":
                row["className"] = tree.get("m_ClassName")
                row["namespace"] = tree.get("m_Namespace")
            objects.append(row)
            hits = list(strings(tree))
            if hits or any("corpse" in key.lower() or "cocoon" in key.lower()
                           or key == "heroDeathPrefab" for key in tree):
                matches.append({**row, "hits": hits, "tree": tree})
        record = {"bundle": str(path), "assets": assets,
                  "objects": objects, "matches": matches, "errors": errors}
        result.append(record)
        print(json.dumps({"bundle": relative, "objects": len(objects),
                          "matches": len(matches), "errors": len(errors)},
                         ensure_ascii=True))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                   default=encode_extra) + "\n",
                        encoding="utf-8")
    print(str(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
