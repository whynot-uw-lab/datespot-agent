from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tests" / "run_graph_live.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "run_graph_live",
        MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("graph live module spec 생성 실패")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GraphLiveBrowserConfigTests(unittest.TestCase):
    def test_build_browser_service_uses_external_chrome_cdp_profile(self):
        module = load_module()

        service = module.build_browser_service(default_headless=True)

        self.assertFalse(service._headless)
        self.assertIsNone(service._user_data_dir)
        self.assertIsNotNone(service._cdp_launcher)
        self.assertEqual(
            service._cdp_launcher.executable_path,
            Path(
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            ),
        )
        self.assertEqual(
            service._cdp_launcher.user_data_dir,
            Path.home()
            / ".cache"
            / "datespot-agent"
            / "chrome-profile",
        )


if __name__ == "__main__":
    unittest.main()
