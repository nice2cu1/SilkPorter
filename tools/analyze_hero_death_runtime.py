"""Read-only diagnosis of the current death-sprite package and live source assets.

Writes reports and a diagnostic PNG under workspace output, never a deployment
package or an AssetBundle. UnityPy's decoded-image cache is changed only in memory
to render the packaged pixels through the original Sprite meshes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import UnityPy
from PIL import Image, ImageChops, ImageDraw, ImageFont

from build_repacked_sprite_remaps import (
    ROOT, inverse_rotate, load_atlas, rms, texture_box, tight_mesh_mask,
)

OUT = ROOT / "output/reports/hero-death-assets/reanalysis"
SWITCH = ROOT / "romfs/Data/StreamingAssets/aa/Switch"
TARGETS = {
    "Hornet_death_pieces_0000s_0007_back_thread",
    "Hornet_death_pieces_0000s_0000_death_spider_core",
}
BODY = "Hornet_death_pieces_0000s_0001_6"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def visible(image):
    image = image.convert("RGBA")
    alpha = image.getchannel("A")
    return Image.merge("RGBA", tuple(
        ImageChops.multiply(channel, alpha) for channel in image.split()[:3]
    ) + (alpha,))


def changed_pixels(left, right):
    # Count a change in any RGBA channel; RGBA.getbbox defaults to alpha only.
    difference = ImageChops.difference(visible(left), visible(right))
    maximum = Image.new("L", difference.size, 0)
    for channel in difference.split():
        maximum = ImageChops.lighter(maximum, channel)
    return sum(maximum.histogram()[1:])


def render(sprites, canvases):
    # SpriteHelper caches textures with Unity's bottom-up origin.
    flipped = {path_id: canvas.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
               for path_id, canvas in canvases.items()}
    for obj in sprites.values():
        obj.assets_file._cache.update(flipped)
    return {key: obj.read().image.convert("RGBA").copy()
            for key, obj in sprites.items()}


def compare(sprites, before, after):
    rows = []
    for key, obj in sprites.items():
        count = changed_pixels(before[key], after[key])
        if count:
            rows.append({"name": obj.read().m_Name, "changedVisiblePixels": count,
                         "premultipliedRgbaRms": rms(visible(before[key]), visible(after[key]))})
    return sorted(rows, key=lambda row: row["name"])


def sheet(rows, path):
    canvas = Image.new("RGB", (1320, 860), (38, 42, 48))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 20)
    for index, title in enumerate(("Original Switch", "PC skin through PC mesh", "Package through Switch mesh")):
        draw.text((index * 440 + 12, 8), title, font=font, fill="white")
    y = 40
    for title, images in rows:
        draw.text((12, y), title, font=font, fill="white")
        y += 30
        for index, image in enumerate(images):
            image = image.copy().convert("RGBA")
            image.thumbnail((420, 220), Image.Resampling.NEAREST)
            canvas.paste(image, (index * 440 + (440 - image.width) // 2, y), image)
        y += 240
    canvas.save(path)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    package = ROOT / "SilkPorter/output"
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    skin_name = manifest["skinName"]
    skin_root = package / "atmosphere/contents/010013C00E930000/romfs/SilkModLoader/Mods/Skin" / skin_name
    source_roots = list((ROOT / "pc-mods").glob(f"*/{skin_name}"))
    if len(source_roots) != 1:
        raise ValueError("Expected exactly one matching PC skin directory")
    source_root = source_roots[0]
    core_bundle = SWITCH / "atlases_assets_assets/sprites/_atlases/core.spriteatlas.bundle"
    pc_bundle = ROOT / "samples/pc-original/core.spriteatlas.bundle"
    _env, _obj, atlas, textures, sprites, render_data = load_atlas(core_bundle)
    _pc_env, _pc_obj, _pc_atlas, pc_textures, pc_sprites, pc_render = load_atlas(pc_bundle)
    originals = {key: obj.read().image.convert("RGBA") for key, obj in textures.items()}
    before = render(sprites, originals)
    record = next(row for row in manifest["spriteTextures"] if row["semanticKey"] == "death-core:RenderDataKey-remap")
    png = skin_root / record["file"]
    if digest(png) != record["sha256"]:
        raise ValueError("Packaged Core hash differs from manifest")
    texture_id = next(key for key, obj in textures.items() if obj.read().m_Name == record["name"])
    packaged = Image.open(png).convert("RGBA")
    after = render(sprites, {**originals, texture_id: packaged})
    changes = compare(sprites, before, after)

    # Compare the PC skin's unmodified regions against the supplied PC bundle.
    # This is supporting evidence of layout compatibility, not a UV guarantee.
    pc_original = render(pc_sprites, {key: obj.read().image.convert("RGBA") for key, obj in pc_textures.items()})
    pc_sources = list((source_root / "Texture2D").glob("sactx-0-8192x4096-BC7-Core-*.png"))
    if len(pc_sources) != 1 or len(pc_textures) != 1:
        raise ValueError("Expected one PC Core source and atlas texture")
    pc_skin = render(pc_sprites, {next(iter(pc_textures)): Image.open(pc_sources[0]).convert("RGBA")})
    layout_errors = [rms(visible(pc_original[key]), visible(pc_skin[key])) for key in pc_sprites]

    # Diagnostic candidate: copy RGBA only inside the original target geometry.
    # Keep the original atlas outside those polygons, including other sprites
    # whose rectangular bounds overlap the selected sprites' bounds.
    candidate = originals[texture_id].copy()
    target_keys = [key for key, obj in sprites.items() if obj.read().m_Name in TARGETS]
    if len(target_keys) != 2:
        raise ValueError("Expected both Core death sprites")
    for key in target_keys:
        data = render_data[key]
        _sprite, _mesh, mask = tight_mesh_mask(sprites[key])
        box = texture_box(data.textureRect, candidate.height)
        candidate.paste(packaged.crop(box), box[:2], inverse_rotate(mask, data.settingsRaw))
    candidate_path = OUT / "core-polygon-candidate.png"
    candidate.save(candidate_path)
    candidate_images = render(sprites, {**originals, texture_id: candidate})
    candidate_changes = compare(sprites, before, candidate_images)
    preserved = {sprites[key].read().m_Name: changed_pixels(after[key], candidate_images[key])
                 for key in target_keys}
    unresolved_changes = []
    for key in set(render_data) - set(sprites):
        data = render_data[key]
        box = texture_box(data.textureRect, candidate.height)
        count = changed_pixels(originals[texture_id].crop(box), candidate.crop(box))
        if count:
            unresolved_changes.append({"key": str(key), "rect": box, "changedPixels": count})

    hero_bundle = SWITCH / "herostatic_assets_all.bundle"
    hero = UnityPy.load(str(hero_bundle))
    body = next(obj.read() for obj in hero.objects
                if obj.type.name == "Texture2D" and obj.peek_name() == BODY)
    body_record = next(row for row in manifest["spriteTextures"] if row["name"] == BODY)
    body_path = skin_root / body_record["file"]
    body_source = source_root / "Texture2D" / (BODY + ".png")
    body_output = Image.open(body_path).convert("RGBA")
    body_input = Image.open(body_source).convert("RGBA")
    rows = [("Normal body", [body.image.convert("RGBA"), body_input, body_output])]
    source_differences = {}
    for name in ("Hornet_death_pieces_0000s_0007_back_thread", "Hornet_death_pieces_0000s_0000_death_spider_core"):
        key = next(key for key, obj in sprites.items() if obj.read().m_Name == name)
        pc_image = pc_skin[key].resize(before[key].size, Image.Resampling.LANCZOS)
        source_differences[name] = changed_pixels(pc_image, after[key])
        rows.append(("Back thread" if name.endswith("thread") else "Inner core", [before[key], pc_image, after[key]]))
        for label, image in (("original", before[key]), ("pc-skin", pc_image), ("packaged", after[key])):
            image.save(OUT / f"{label}-{name}.png")
    sheet(rows, OUT / "rendered-comparison.png")

    # Re-read the original prefab. Do not infer current binding from old reports.
    dynamic_path = SWITCH / "herodynamic_assets_all.bundle"
    dynamic = UnityPy.load(str(dynamic_path))
    objects = {obj.path_id: obj for obj in dynamic.objects}
    root = next(obj for obj in dynamic.objects if obj.type.name == "GameObject" and obj.peek_name() == "Hornet Cocoon Corpse")
    hierarchy = []
    def walk(go, prefix=""):
        tree = go.read_typetree()
        path = (prefix + "/" if prefix else "") + tree["m_Name"]
        item = {"path": path, "active": tree["m_IsActive"], "components": []}
        children = []
        for ref in tree["m_Component"]:
            comp = objects[ref["component"]["m_PathID"]]
            data = comp.read_typetree()
            detail = {"type": comp.type.name, "pathId": comp.path_id}
            if comp.type.name == "Transform":
                detail.update({key: data[key] for key in ("m_LocalPosition", "m_LocalRotation", "m_LocalScale")})
                children = data["m_Children"]
            elif comp.type.name == "SpriteRenderer":
                detail.update({key: data[key] for key in ("m_Sprite", "m_Enabled", "m_Materials")})
            elif comp.type.name == "MonoBehaviour":
                detail["script"] = data["m_Script"]
                if "relativeToStartRotation" in data:
                    detail["rotation"] = {key: data[key] for key in ("min", "max", "relativeToStartRotation")}
                if "fsm" in data:
                    detail["states"] = [{"name": state["name"], "actions": state["actionData"]["actionNames"]}
                                        for state in data["fsm"]["states"]]
            item["components"].append(detail)
        hierarchy.append(item)
        for child in children:
            transform = objects[child["m_PathID"]].read_typetree()
            walk(objects[transform["m_GameObject"]["m_PathID"]], path)
    walk(root)
    files = [manifest_path, core_bundle, pc_bundle, hero_bundle, dynamic_path,
             png, pc_sources[0], body_source, body_path,
             package / "atmosphere/contents/010013C00E930000/exefs/subsdk9"]
    report = {
        "skinName": skin_name, "evidence": "Local pixels, live serialized data, and existing meshes only; no console execution proof.",
        "hashes": {str(path): digest(path) for path in files},
        "body": {"size": body_output.size, "packageHashMatchesManifest": digest(body_path) == body_record["sha256"],
                 "sourceToPackageChangedVisiblePixels": changed_pixels(body_input, body_output),
                 "originalToPackageChangedVisiblePixels": changed_pixels(body.image, body_output)},
        "core": {"name": record["name"], "renderDataEntries": len(render_data), "spriteObjectsChecked": len(sprites),
                 "renderEntriesWithoutSpriteObject": len(set(render_data) - set(sprites)),
                 "changedSprites": changes, "unintendedChangedSprites": [row for row in changes if row["name"] not in TARGETS]},
        "pcLayoutEvidence": {"spritesChecked": len(layout_errors), "visibleRmsUnder10": sum(value < 10 for value in layout_errors),
                             "medianVisibleRms": sorted(layout_errors)[len(layout_errors) // 2],
                             "targetRenderedSourceDifferences": source_differences},
        "diagnosticPolygonCandidate": {"file": str(candidate_path), "sha256": digest(candidate_path),
                                       "changedSprites": candidate_changes, "targetPixelsChangedVersusCurrentPackage": preserved,
                                       "unresolvedRenderEntryRectChanges": unresolved_changes,
                                       "deployed": False},
        "livePrefab": hierarchy,
    }
    (OUT / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"body": report["body"], "coreChecked": len(sprites),
                      "unintendedSprites": len(report["core"]["unintendedChangedSprites"]),
                      "pcLayout": report["pcLayoutEvidence"], "candidateChangedSprites": len(candidate_changes),
                      "candidateTargetDifferences": preserved, "report": str(OUT / "analysis.json")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
