"""Regression checks for reference traversal and narrow ASTC block replacement."""
import sys
import unittest
from pathlib import Path

import UnityPy
from UnityPy.export import Texture2DConverter

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
from audit_runtime_application import walk_refs
from inspect_hero_death_assets import refs
from rebuild_repacked_bundle import restrict_astc_regions


class ReferenceTests(unittest.TestCase):
    def test_material_texenv_tuple_is_traversed(self):
        pointer = {"m_FileID": 6, "m_PathID": 8429042907869783626}
        tree = {"m_SavedProperties": {"m_TexEnvs": [("_MainTex", {"m_Texture": pointer})]}}
        self.assertEqual(list(walk_refs(tree))[0][1], pointer)
        self.assertEqual(list(refs(tree))[0]["m_FileID"], 6)


class AstcRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
        workspace = Path(__file__).resolve().parents[2]
        env = UnityPy.load(str(workspace / "romfs/Data/StreamingAssets/aa/Switch/atlases_assets_assets/sprites/_atlases/hornet_map.spriteatlas.bundle"))
        cls.env = env
        cls.obj = next(obj for obj in env.objects if obj.path_id == -8895439953096435575)

    def test_only_selected_block_changes_with_vertical_flip(self):
        texture = self.obj.read()
        swizzler = Texture2DConverter.TextureSwizzler
        bw, bh = swizzler.TEXTURE_FORMAT_BLOCK_SIZE_MAP[texture.m_TextureFormat]
        gobs = swizzler.get_switch_gobs_per_block(texture.m_PlatformBlob)
        width, height = swizzler.get_padded_texture_size(texture.m_Width, texture.m_Height, bw, bh, gobs)
        original = bytes(texture.get_image_data())
        linear = bytes(swizzler.deswizzle(original, width, height, bw, bh, gobs))
        changed = bytes(byte ^ 0xff for byte in linear)
        encoded = bytes(swizzler.swizzle(changed, width, height, bw, bh, gobs))
        # PIL y=4090..4096 maps to raw y=0..6: exactly one 6x6 block.
        merged, count = restrict_astc_regions(self.obj, encoded, [[0, 4090, 6, 4096]])
        got = bytes(swizzler.deswizzle(merged, width, height, bw, bh, gobs))
        self.assertEqual(count, 1)
        self.assertEqual(got[:16], changed[:16])
        self.assertEqual(got[16:], linear[16:])

    def test_invalid_rectangle_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "区域越界"):
            restrict_astc_regions(self.obj, bytes(self.obj.read().get_image_data()), [[-1, 0, 5, 6]])


if __name__ == "__main__":
    unittest.main()
