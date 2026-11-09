# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Estimate how much a game folder would shrink with btrfs zstd compression,
and pick the files worth compressing.

How the estimate works (read-only, nothing is changed):
  * Files that are already compressed by their format (videos, audio, images,
    archives) are skipped: zstd cannot shrink them.
  * From every other file, a few 128 KiB pieces are read, spread through the
    file. btrfs compresses in 128 KiB pieces and only keeps a compressed piece
    if it saves at least one 4 KiB block, so the estimate copies that rule.
  * Parts the drive has already compressed (btrfs mounted with compress=...)
    are found with the FIEMAP call and not counted again.
  * The total amount read is capped (64 MB by default), so even a 100 GB game
    takes seconds, not minutes.
"""
from __future__ import annotations

try:
    import fcntl
except ImportError:  # Windows: this module's Linux-only parts are never used there
    fcntl = None
import os
import struct
import zlib
from typing import Dict, List, Optional, Tuple

try:  # Python 3.14+ has zstd built in; older versions use zlib as a stand-in
    from compression import zstd as _zstd  # type: ignore
    ALGO = "zstd"

    def _pack(b: bytes) -> int:
        return len(_zstd.compress(b, 3))
except ImportError:  # pragma: no cover - depends on the Python version
    ALGO = "zlib (stand-in for zstd)"

    def _pack(b: bytes) -> int:
        return len(zlib.compress(b, 3))

CHUNK = 128 * 1024
BLOCK = 4096
SMALL = 64 * 1024          # btrfs often stores tiny files inline; ignore them
SAMPLE_BUDGET = 64 * 1024 * 1024
MAX_CHUNKS_PER_FILE = 16
MIN_FILE_GAIN = 0.03       # skip files that would shrink by less than 3 %
SKIP_EXT = {
    # video
    ".bk2", ".bik", ".usm", ".mp4", ".m4v", ".webm", ".mkv", ".avi", ".mov", ".wmv", ".ogv",
    # audio
    ".ogg", ".opus", ".mp3", ".m4a", ".aac", ".flac", ".wma",
    # images
    ".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".ktx2", ".basis",
    # archives and packed data
    ".zip", ".7z", ".rar", ".gz", ".xz", ".bz2", ".zst", ".lz4", ".cab", ".jar", ".apk",
}

FS_IOC_FIEMAP = 0xC020660B
FIEMAP_EXTENT_LAST = 0x1
FIEMAP_EXTENT_ENCODED = 0x8
_EXT_FMT = "=QQQQQIIII"     # struct fiemap_extent, 56 bytes
_HDR_FMT = "=QQIIII"        # struct fiemap, 32 bytes


def chunk_saving(data: bytes) -> int:
    """Bytes btrfs would save on this piece (0 if it would store it as is)."""
    saved = 0
    for i in range(0, len(data), CHUNK):
        piece = data[i:i + CHUNK]
        if len(piece) < BLOCK:
            continue
        packed = -(-_pack(piece) // BLOCK) * BLOCK
        if packed < len(piece):
            saved += len(piece) - packed
    return saved


def parse_fiemap(buf: bytes, start: int, end: int) -> Tuple[int, int, bool]:
    """(encoded bytes in [start,end), next offset, last extent seen)."""
    mapped = struct.unpack_from("=I", buf, 20)[0]
    enc, nxt, last = 0, end, False
    for i in range(mapped):
        lo, _ph, ln, _r1, _r2, fl, _a, _b, _c = struct.unpack_from(_EXT_FMT, buf, 32 + 56 * i)
        if fl & FIEMAP_EXTENT_ENCODED:
            enc += max(0, min(lo + ln, end) - max(lo, start))
        nxt = lo + ln
        if fl & FIEMAP_EXTENT_LAST:
            last = True
    if mapped == 0:
        last = True
    return enc, nxt, last


def encoded_fraction(path: str, size: int) -> Optional[float]:
    """Share of the file the file system already stores compressed, or None
    if the file system cannot tell us."""
    if size <= 0:
        return 0.0
    n = 128
    enc, start = 0, 0
    try:
        with open(path, "rb") as f:
            for _ in range(10000):
                buf = bytearray(struct.pack(_HDR_FMT, start, size - start, 0, 0, n, 0) + b"\0" * (56 * n))
                fcntl.ioctl(f.fileno(), FS_IOC_FIEMAP, buf, True)
                e, nxt, last = parse_fiemap(bytes(buf), start, size)
                enc += e
                if last or nxt <= start or nxt >= size:
                    break
                start = nxt
    except OSError:
        return None
    return min(1.0, enc / size)


def _sample(path: str, size: int, chunks: int) -> Tuple[int, int]:
    """(bytes read, bytes btrfs would save) for `chunks` pieces of the file."""
    chunks = max(1, min(chunks, MAX_CHUNKS_PER_FILE, -(-size // CHUNK)))
    read = saved = 0
    with open(path, "rb") as f:
        for k in range(chunks):
            off = (size - CHUNK) * k // max(1, chunks - 1) if chunks > 1 and size > CHUNK else 0
            off -= off % BLOCK
            f.seek(off)
            data = f.read(CHUNK)
            read += len(data)
            saved += chunk_saving(data)
    return read, saved


def walk_files(folder: str) -> List[Tuple[str, int]]:
    out = []
    for dp, dns, fns in os.walk(folder):
        dns[:] = [d for d in dns if not os.path.islink(os.path.join(dp, d))]
        for n in fns:
            p = os.path.join(dp, n)
            try:
                if os.path.isfile(p) and not os.path.islink(p):
                    out.append((p, os.lstat(p).st_size))
            except OSError:
                pass
    return out


def estimate(folder: str, progress=None, check=None, budget: int = SAMPLE_BUDGET) -> Dict[str, object]:
    files = walk_files(folder)
    total = sum(s for _, s in files)
    skipped_fmt = skipped_small = already = est_saved = sampled = 0
    worth: List[str] = []
    unknown_encoding = False
    cand = [(p, s) for p, s in files
            if s >= SMALL and os.path.splitext(p)[1].lower() not in SKIP_EXT]
    for p, s in files:
        if s < SMALL:
            skipped_small += s
        elif os.path.splitext(p)[1].lower() in SKIP_EXT:
            skipped_fmt += s
    cand_total = sum(s for _, s in cand) or 1
    done = 0
    for p, s in cand:
        if check:
            check()
        enc = encoded_fraction(p, s)
        if enc is None:
            unknown_encoding, enc = True, 0.0
        already += int(s * enc)
        if enc > 0.9:
            done += s
            continue
        chunks = max(1, int(budget * s / cand_total) // CHUNK)
        try:
            r, sv = _sample(p, s, chunks)
        except OSError:
            continue
        sampled += r
        ratio = sv / r if r else 0.0
        if ratio >= MIN_FILE_GAIN:
            worth.append(p)
            est_saved += int(s * (1 - enc) * ratio)
        done += s
        if progress:
            progress(done, cand_total)
    worth_set = set(worth)
    pct = 100.0 * est_saved / total if total else 0.0
    if est_saved < 200 * 1024 ** 2 or pct < 3:
        verdict = "not worth it"
    elif pct < 10:
        verdict = "small gain"
    else:
        verdict = "worth it"
    return {"total": total, "saved": est_saved, "pct": round(pct, 1), "verdict": verdict,
            "already": already, "skipped_formats": skipped_fmt, "skipped_small": skipped_small,
            "sampled": sampled, "files": len(files), "worth_files": worth,
            "worth_bytes": sum(s for p, s in cand if p in worth_set),
            "algo": ALGO, "encoding_known": not unknown_encoding}
