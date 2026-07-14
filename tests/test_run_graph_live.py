from __future__ import annotations

import importlib.util
import tempfile
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
    def test_resolve_live_api_key_prefers_project_dotenv(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "OPENAI_API_KEY=dotenv-key\n",
                encoding="utf-8",
            )

            api_key = module.resolve_live_api_key(
                "inherited-shell-key",
                env_path=env_path,
            )

        self.assertEqual(api_key, "dotenv-key")

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
