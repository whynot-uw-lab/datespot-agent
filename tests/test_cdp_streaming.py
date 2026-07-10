from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "poc" / "1-5-cdp-streaming" / "stream_browser.py"


def load_module():
    spec = importlib.util.spec_from_file_location("cdp_streaming", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CdpStreamingTests(unittest.TestCase):
    def test_build_frame_message_wraps_cdp_payload(self):
        module = load_module()

        message = module.build_frame_message(
            frame="abc123",
            session_id=7,
            metadata={"timestamp": 12.5, "deviceWidth": 1280},
        )

        self.assertEqual(message["type"], "frame")
        self.assertEqual(message["sessionId"], 7)
        self.assertEqual(message["data"], "abc123")
        self.assertEqual(message["metadata"]["deviceWidth"], 1280)

    def test_stream_stats_counts_frames_and_fps(self):
        module = load_module()
        stats = module.StreamStats(started_at=10.0)

        stats.record_received()
        stats.record_received()
        stats.record_broadcast(3)
        stats.finished_at = 12.0

        self.assertEqual(stats.frames_received, 2)
        self.assertEqual(stats.frames_broadcast, 3)
        self.assertEqual(stats.duration_seconds(), 2.0)
        self.assertEqual(stats.average_fps(), 1.0)

    def test_validate_result_marks_threshold_failure(self):
        module = load_module()
        result = {
            "ok": False,
            "framesReceived": 2,
            "framesBroadcast": 2,
            "errors": [],
        }

        validated = module.validate_result(result, min_frames=30)

        self.assertFalse(validated["ok"])
        self.assertIn("threshold", validated["errors"][0])


if __name__ == "__main__":
    unittest.main()
