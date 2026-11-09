# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ddg import compress  # noqa: E402


def fiemap_buf(extents):
    hdr = struct.pack("=QQIIII", 0, 0, 0, len(extents), len(extents), 0)
    body = b"".join(struct.pack("=QQQQQIIII", lo, 0, ln, 0, 0, fl, 0, 0, 0) for lo, ln, fl in extents)
    return hdr + body


class CompressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_chunk_rule(self):
        self.assertGreater(compress.chunk_saving(b"\0" * compress.CHUNK), 120 * 1024)
        self.assertEqual(compress.chunk_saving(os.urandom(compress.CHUNK)), 0)
        self.assertEqual(compress.chunk_saving(b"\0" * 1000), 0)   # below one block: stored as is

    def test_parse_fiemap(self):
        buf = fiemap_buf([(0, 4096, 0), (4096, 8192, compress.FIEMAP_EXTENT_ENCODED),
                          (12288, 4096, compress.FIEMAP_EXTENT_LAST)])
        enc, nxt, last = compress.parse_fiemap(buf, 0, 16384)
        self.assertEqual((enc, nxt, last), (8192, 16384, True))
        self.assertEqual(compress.parse_fiemap(fiemap_buf([]), 0, 10)[2], True)

    def test_encoded_fraction_on_this_disk(self):
        p = self.d / "f"
        p.write_bytes(os.urandom(300000))
        frac = compress.encoded_fraction(str(p), 300000)
        self.assertIn(frac, (0.0, None))      # ordinary file systems: nothing encoded / unknown
        self.assertEqual(compress.encoded_fraction(str(p), 0), 0.0)

    def test_estimate_skips_media_and_random(self):
        (self.d / "intro.bk2").write_bytes(b"\0" * 2_000_000)      # skipped by extension even if compressible
        (self.d / "random.pak").write_bytes(os.urandom(2_000_000))
        (self.d / "levels.dat").write_bytes(b"abcabcabd" * 300_000)
        (self.d / "tiny.cfg").write_text("x=1")
        os.symlink("levels.dat", str(self.d / "link.dat"))
        e = compress.estimate(str(self.d))
        names = [os.path.basename(p) for p in e["worth_files"]]
        self.assertEqual(names, ["levels.dat"])
        self.assertEqual(e["skipped_formats"], 2_000_000)
        self.assertGreater(e["saved"], 2_000_000)
        self.assertLess(e["sampled"], e["total"])
        self.assertEqual(e["files"], 4)     # the symlink is not counted

    def test_estimate_respects_drive_compression(self):
        (self.d / "levels.dat").write_bytes(b"abcabcabd" * 300_000)
        orig = compress.encoded_fraction
        compress.encoded_fraction = lambda p, s: 1.0
        try:
            e = compress.estimate(str(self.d))
        finally:
            compress.encoded_fraction = orig
        self.assertEqual(e["saved"], 0)
        self.assertEqual(e["worth_files"], [])
        self.assertEqual(e["already"], 2_700_000)
        self.assertEqual(e["verdict"], "not worth it")

    def test_sampling_budget(self):
        (self.d / "big.dat").write_bytes(b"xyz" * 4_000_000)
        e = compress.estimate(str(self.d), budget=compress.CHUNK * 4)
        self.assertLessEqual(e["sampled"], compress.CHUNK * 4)

    def test_empty_folder(self):
        e = compress.estimate(str(self.d))
        self.assertEqual((e["total"], e["saved"], e["verdict"]), (0, 0, "not worth it"))


if __name__ == "__main__":
    unittest.main()
