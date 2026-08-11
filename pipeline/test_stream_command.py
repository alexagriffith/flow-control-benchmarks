import io
import re
import unittest

import stream_command


class StreamCommandTests(unittest.TestCase):
    def test_write_filtered_handles_split_lines(self) -> None:
        output = io.BytesIO()
        pattern = re.compile(b"keep")
        pending = stream_command.write_filtered(output, b"drop\nke", b"", pattern)
        pending = stream_command.write_filtered(output, b"ep one\nkeep two\n", pending, pattern)
        self.assertEqual(pending, b"")
        self.assertEqual(output.getvalue(), b"keep one\nkeep two\n")

    def test_write_filtered_passes_all_bytes_without_pattern(self) -> None:
        output = io.BytesIO()
        pending = stream_command.write_filtered(output, b"all bytes", b"", None)
        self.assertEqual(pending, b"")
        self.assertEqual(output.getvalue(), b"all bytes")


if __name__ == "__main__":
    unittest.main()
