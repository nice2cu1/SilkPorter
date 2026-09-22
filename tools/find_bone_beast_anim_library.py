"""查找 Child R 引用的外部 TK2D 动画库。"""

from __future__ import annotations

import json
from pathlib import Path

import UnityPy


ROOT = Path(__file__).resolve().parents[2]
SWITCH_ROOT = ROOT / "romfs" / "Data" / "StreamingAssets" / "aa" / "Switch"
TARGET_PATH_ID = 1140255965157231184
OUT = ROOT / "output" / "reports" / "bone-beast-animation-library.json"


def main() -> int:
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    hits = []
    bundle_paths = sorted(SWITCH_ROOT.rglob("*.bundle"))
    for index, path in enumerate(bundle_paths, 1):
        try:
            env = UnityPy.load(str(path))
        except Exception:
            continue
        for obj in env.objects:
            if int(obj.path_id) != TARGET_PATH_ID:
                continue
            item = {
                "bundle": str(path),
                "type": obj.type.name,
                "pathId": int(obj.path_id),
            }
            try:
                data = obj.read_typetree()
                item["keys"] = sorted(data.keys()) if isinstance(data, dict) else []
                if isinstance(data, dict):
                    item["name"] = data.get("m_Name") or data.get("name") or ""
                    item["clips"] = data.get("clips")
                    item["spriteCollection"] = data.get("spriteCollection")
                    item["serialized"] = data
            except Exception as exc:
                item["error"] = str(exc)
            hits.append(item)
        if index % 50 == 0:
            print(f"已扫描 {index}/{len(bundle_paths)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"targetPathId": TARGET_PATH_ID, "hits": hits}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"bundleCount": len(bundle_paths), "hits": [{k: v for k, v in item.items() if k not in {"serialized", "clips"}} for item in hits], "out": str(OUT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
