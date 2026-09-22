import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_CANDIDATE = PROJECT_ROOT.parent
DEFAULT_ROOT = (
    WORKSPACE_CANDIDATE
    if (WORKSPACE_CANDIDATE / "SilkRuntime").is_dir()
    else PROJECT_ROOT
)
ROOT = Path(os.environ.get("SILK_WORKSPACE_ROOT", DEFAULT_ROOT)).resolve()


def load_module(relative_path: str, name: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Ips32GeneratorTests(unittest.TestCase):
    def test_runtime_skin_generator_writes_ips32_header(self):
        module = load_module(
            "SilkPorter/scripts/stage_runtime_skin.py", "stage_runtime_skin"
        )
        patch = module.make_ips32(0x5B583A8, 0x94BB8FB2)
        self.assertEqual(patch[:5], b"IPS32")
        self.assertEqual(len(patch), 19)
        self.assertEqual(patch[5:9], bytes.fromhex("05B583A8"))
        self.assertEqual(patch[11:15], bytes.fromhex("B28FBB94"))
        self.assertEqual(patch[-4:], b"EEOF")

    def test_skin_png_resolution_uses_selected_skin_not_report_path(self):
        module = load_module(
            "SilkPorter/scripts/stage_runtime_skin.py", "stage_runtime_skin_resolver"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            skin_dir = root / "selected-skin"
            source = skin_dir / "Texture2D" / "custom.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"selected")
            report_path = root / "old-skin" / "Texture2D" / "custom.png"
            index = module.build_skin_png_index(skin_dir)

            resolved = module.resolve_skin_png(
                skin_dir,
                index,
                report_path,
                "fallback-name",
                "test texture",
            )

            self.assertEqual(resolved, source)

if __name__ == "__main__":
    unittest.main()
