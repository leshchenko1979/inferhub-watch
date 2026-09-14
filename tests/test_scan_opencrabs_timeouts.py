import json
import os
import tempfile
import unittest

from scripts.scan_opencrabs_timeouts import (
    determine_seek_offset,
    scan_log_delta,
    load_state,
    save_state,
)


class TestScanOpenCrabsTimeouts(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.temp_dir.name, "log_scan_state.json")
        self.log_file = os.path.join(self.temp_dir.name, "opencrabs.log")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_determine_seek_offset_cases(self):
        state = {
            "log_path": "/var/log/test.log",
            "inode": 1001,
            "last_offset": 5000,
        }

        # Normal delta seek
        off, reason = determine_seek_offset("/var/log/test.log", 1001, 8000, state)
        self.assertEqual(off, 5000)
        self.assertEqual(reason, "normal_delta_seek")

        # Midnight rotation - path changed
        off, reason = determine_seek_offset("/var/log/test-2.log", 1001, 8000, state)
        self.assertEqual(off, 0)
        self.assertEqual(reason, "path_changed_rotation")

        # Midnight rotation - inode changed on same path
        off, reason = determine_seek_offset("/var/log/test.log", 2002, 8000, state)
        self.assertEqual(off, 0)
        self.assertEqual(reason, "inode_changed_rotation")

        # In-place truncation - same inode, size dropped below last_offset
        off, reason = determine_seek_offset("/var/log/test.log", 1001, 200, state)
        self.assertEqual(off, 0)
        self.assertEqual(reason, "inplace_truncation_detected")

    def test_scan_log_delta_flow(self):
        # 1. Write initial log contents
        line1 = "2026-09-14T01:00:00.000000+00:00 INFO run_tool_loop{session_id=sess-123}: inferhub streaming request: model=ag/gemini-3.8-flash-high, messages=10\n"
        line2 = "2026-09-14T01:00:05.000000+00:00 INFO run_tool_loop{session_id=sess-123}: Retry attempt 1/4 after 931ms for error: HTTP request failed: error sending request for url (https://api.inferhub.dev/v1/chat/completions)\n"
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write(line1)
            f.write(line2)

        events, meta = scan_log_delta(self.log_file, state_path=self.state_file)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["session_id"], "sess-123")
        self.assertEqual(events[0]["model"], "ag/gemini-3.8-flash-high")
        self.assertEqual(events[0]["kind"], "retry")
        self.assertIn("error sending request", events[0]["detail"])
        self.assertEqual(meta["initial_offset"], 0)
        self.assertGreater(meta["new_offset"], 0)

        # 2. Re-scan with no new data -> 0 events, seek reason normal_delta_seek, 0 bytes scanned
        events2, meta2 = scan_log_delta(self.log_file, state_path=self.state_file)
        self.assertEqual(len(events2), 0)
        self.assertEqual(meta2["seek_reason"], "normal_delta_seek")
        self.assertEqual(meta2["initial_offset"], meta["new_offset"])
        self.assertEqual(meta2["bytes_scanned"], 0)

        # 3. Append another event
        line3 = "2026-09-14T01:01:00.000000+00:00 ERROR run_tool_loop{session_id=sess-123}: request error: deadline has elapsed\n"
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line3)

        events3, meta3 = scan_log_delta(self.log_file, state_path=self.state_file)
        self.assertEqual(len(events3), 1)
        self.assertEqual(events3[0]["kind"], "timeout_or_error")
        self.assertEqual(events3[0]["model"], "ag/gemini-3.8-flash-high")
        self.assertIn("deadline has elapsed", events3[0]["detail"])

        # 4. In-place truncation simulation (file truncated to 0, new line added)
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write("2026-09-14T01:05:00.000000+00:00 INFO run_tool_loop{session_id=sess-999}: inferhub streaming request: model=cb/deepseek-v4.1-flash, messages=5\n")
            f.write("2026-09-14T01:05:02.000000+00:00 WARN run_tool_loop{session_id=sess-999}: OpenAI response status: 504 Gateway Timeout\n")

        events4, meta4 = scan_log_delta(self.log_file, state_path=self.state_file)
        self.assertEqual(meta4["seek_reason"], "inplace_truncation_detected")
        self.assertEqual(meta4["initial_offset"], 0)
        self.assertEqual(len(events4), 1)
        self.assertEqual(events4[0]["session_id"], "sess-999")
        self.assertEqual(events4[0]["model"], "cb/deepseek-v4.1-flash")
        self.assertIn("504", events4[0]["detail"])

        # 5. Midnight file rotation simulation (new file, new inode)
        new_log_file = os.path.join(self.temp_dir.name, "opencrabs.2026-09-15")
        with open(new_log_file, "w", encoding="utf-8") as f:
            f.write("2026-09-15T00:00:01.000000+00:00 INFO run_tool_loop{session_id=sess-100}: inferhub streaming request: model=ag/gemini-3.7-flash-high, messages=2\n")
            f.write("2026-09-15T00:00:05.000000+00:00 ERROR run_tool_loop{session_id=sess-100}: connection timed out\n")

        events5, meta5 = scan_log_delta(new_log_file, state_path=self.state_file)
        self.assertEqual(meta5["seek_reason"], "path_changed_rotation")
        self.assertEqual(meta5["initial_offset"], 0)
        self.assertEqual(len(events5), 1)
        self.assertEqual(events5[0]["session_id"], "sess-100")
        self.assertEqual(events5[0]["model"], "ag/gemini-3.7-flash-high")
        self.assertIn("timed out", events5[0]["detail"])


if __name__ == "__main__":
    unittest.main()
