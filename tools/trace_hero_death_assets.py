"""Trace serialized prefab hierarchies and rendering dependencies, read-only."""
from __future__ import annotations

import json
from pathlib import Path

import UnityPy

from inspect_hero_death_assets import ROOT, encode_extra, refs


OUT = ROOT / "output/reports/hero-death-assets"


class Inventory:
    def __init__(self):
        self.assets = {}
        self.rows = {}
        self.environments = {}
        for path in (OUT / "inventory.json", OUT / "map-inventory.json", OUT / "extra-inventory.json"):
            for bundle in json.loads(path.read_text(encoding="utf-8")):
                for name, asset in bundle["assets"].items():
                    self.assets[name.lower()] = {**asset, "bundle": bundle["bundle"]}
                for row in bundle["objects"]:
                    self.rows[(row["file"].lower(), row["pathId"])] = row

    def resolve(self, file, ref):
        if not ref["m_PathID"]:
            return None
        target_file = file.lower()
        if ref["m_FileID"]:
            external = self.assets[target_file]["externals"][ref["m_FileID"] - 1]
            target_file = external.replace("\\", "/").split("/")[-1].lower()
        return target_file, ref["m_PathID"]

    def row(self, key):
        return self.rows.get(key, {"file": key[0], "pathId": key[1], "unresolved": True})

    def tree(self, key):
        bundle = self.assets[key[0]]["bundle"]
        if bundle not in self.environments:
            self.environments[bundle] = UnityPy.load(bundle)
        for obj in self.environments[bundle].objects:
            if obj.assets_file.name.lower() == key[0] and obj.path_id == key[1]:
                return obj.read_typetree()
        raise KeyError(key)

    def reader(self, key):
        self.tree(key)
        bundle = self.assets[key[0]]["bundle"]
        return next(obj for obj in self.environments[bundle].objects
                    if obj.assets_file.name.lower() == key[0] and obj.path_id == key[1])

    def describe_refs(self, key, tree):
        return [{**ref, "target": self.row(self.resolve(key[0], ref))}
                for ref in refs(tree)]

    def hierarchy(self, key, prefix=""):
        tree = self.tree(key)
        name = tree["m_Name"]
        path = f"{prefix}/{name}" if prefix else name
        components = []
        children = []
        for ref in tree["m_Component"]:
            comp_key = self.resolve(key[0], ref["component"])
            comp = self.row(comp_key)
            comp_tree = self.tree(comp_key)
            components.append({**comp, "tree": comp_tree,
                               "resolvedRefs": self.describe_refs(comp_key, comp_tree)})
            if comp["type"] in ("Transform", "RectTransform"):
                for child in comp_tree["m_Children"]:
                    transform_key = self.resolve(comp_key[0], child)
                    child_key = self.resolve(transform_key[0], self.tree(transform_key)["m_GameObject"])
                    children.extend(self.hierarchy(child_key, path))
        return [{**self.row(key), "hierarchyPath": path, "components": components}] + children

    def dependencies(self, hierarchy):
        selected = {}
        allowed = {"Sprite", "SpriteAtlas", "Texture2D", "Material", "MonoScript"}

        def visit(key):
            row = self.row(key)
            if key in selected or row.get("type") not in allowed:
                return
            tree = self.tree(key)
            selected[key] = {**row, "tree": tree,
                             "resolvedRefs": self.describe_refs(key, tree)}
            # An atlas references every packed sprite. Follow its texture refs
            # only; the caller's selected sprite remains the display target.
            for ref in refs(tree):
                target = self.resolve(key[0], ref)
                if row["type"] == "SpriteAtlas" and self.row(target).get("type") != "Texture2D":
                    continue
                visit(target)

        for go in hierarchy:
            for comp in go["components"]:
                for ref in refs(comp["tree"]):
                    visit(self.resolve(comp["file"], ref))
        return list(selected.values())


def main():
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    inventory = Inventory()
    starts = [key for key, row in inventory.rows.items()
              if row["type"] == "GameObject" and row["name"] in
              {"Hornet Cocoon Corpse", "Map_shade_head", "Shade Pos"}]
    result = []
    for key in starts:
        hierarchy = inventory.hierarchy(key)
        dependencies = inventory.dependencies(hierarchy)
        result.append({"root": inventory.row(key), "hierarchy": hierarchy,
                       "dependencies": dependencies})
        print(json.dumps({"root": inventory.row(key)["name"], "id": key[1],
                          "children": len(hierarchy),
                          "dependencies": [{"type": d["type"], "name": d["name"]}
                                           for d in dependencies]}, ensure_ascii=True))
    manifest = json.loads((ROOT / "SilkPorter/output/manifest.json").read_text(encoding="utf-8"))
    packaged = {row["name"]: {"kind": kind, **row}
                for kind in ("spriteTextures", "standaloneTextures")
                for row in manifest.get(kind, [])}
    linked = []
    for key, row in inventory.rows.items():
        if row["type"] != "MonoBehaviour":
            continue
        fields = {ref["field"] for ref in row["refs"]}
        if not fields.intersection({"corpseIcon", "shadeMarker"}):
            continue
        tree = inventory.tree(key)
        linked.append({**row, "tree": tree,
                       "resolvedRefs": inventory.describe_refs(key, tree)})
    textures = {}
    for record in result:
        for dep in record["dependencies"]:
            if dep["type"] != "Texture2D":
                continue
            tree = dep["tree"]
            textures[dep["name"]] = {"name": dep["name"], "file": dep["file"],
                "pathId": dep["pathId"], "width": tree["m_Width"],
                "height": tree["m_Height"], "format": tree["m_TextureFormat"],
                "bundle": inventory.assets[dep["file"].lower()]["bundle"],
                "packageRecord": packaged.get(dep["name"])}
    summary = {"skinName": manifest["skinName"], "textures": list(textures.values()),
               "mapBindings": linked,
               "validation": "Serialized references and original image decode only; no in-game application proof."}
    (OUT / "identified-resources.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=encode_extra) + "\n",
        encoding="utf-8")
    # Decode the exact original assets, without modifying game or skin inputs.
    images = OUT / "images"
    images.mkdir(exist_ok=True)
    wanted = {"Hornet_death_pieces_0000s_0001_6", "Hornet_death_cocoon_particle_chunks",
              "Shade_Pin", "Hornet_death_pieces_0000s_0000_death_spider_core",
              "Hornet_death_pieces_0000s_0007_back_thread"}
    for key, row in inventory.rows.items():
        if row["name"] in wanted and row["type"] in {"Sprite", "Texture2D"}:
            obj = inventory.reader(key).read()
            image = obj.image
            path = images / f"{row['name']}-{row['type']}.png"
            image.save(path)
            print(json.dumps({"image": str(path), "size": image.size}))
    path = OUT / "render-traces.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                               default=encode_extra) + "\n", encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
