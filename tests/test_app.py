import unittest
from unittest.mock import patch

import app


class AppEntryTests(unittest.TestCase):
    def test_direct_start_requires_explicit_standalone_fallback(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(SystemExit) as raised:
            app.main([])
        self.assertEqual(raised.exception.code, 2)

    def test_bowxt_managed_start_uses_control_plane_paths(self):
        with patch.dict("os.environ", {"BOWXT_MANAGED": "1"}, clear=True), patch(
            "app.run"
        ) as run:
            app.main(["--config", "/tmp/managed.json", "--env", "/tmp/managed.env"])
        run.assert_called_once()
        self.assertEqual(str(run.call_args.args[0]), "/tmp/managed.json")
        self.assertEqual(str(run.call_args.args[1]), "/tmp/managed.env")

    def test_managed_process_rejects_standalone_flag(self):
        with patch.dict("os.environ", {"BOWXT_MANAGED": "1"}, clear=True), self.assertRaises(
            SystemExit
        ) as raised:
            app.main(["--standalone"])
        self.assertEqual(raised.exception.code, 2)

    def test_standalone_fallback_remains_available(self):
        with patch.dict("os.environ", {}, clear=True), patch("app.run") as run:
            app.main(["--standalone", "--config", "/tmp/fallback.json"])
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
