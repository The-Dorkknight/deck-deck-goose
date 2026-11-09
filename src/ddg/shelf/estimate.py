# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Estimate how much a game folder would shrink with Windows' file
compression (compact /EXE), and pick the files worth compressing.

Read-only. How it works:
  * Files already compressed by their format (video, audio, images,
    archives) are skipped: compression cannot shrink them.
  * Files Windows already stores compressed are measured exactly (their real
    on-disk size) and counted as "already compressed".
  * From each other file, up to 16 pieces of 128 KiB are read, spread through
    the file (64 MB at most per game). Windows compresses in chunks of 4, 8,
    16 or 32 KiB depending on the algorithm and keeps a chunk compressed only
    if it got smaller, so the estimate copies that. zlib stands in for
    Microsoft's XPRESS/LZX coders (a rough stand-in; the real result is
    measured exactly after compressing).
  * Games that use DirectStorage are flagged: compressing them can cost
    performance (CompactGUI's advice), so they are left alone by default.
"""
from __future__ import annotations

import os
import zlib
from typing import Callable, Dict, List, Optional, Tuple

CHUNKS = {"XPRESS4K": 4096, "XPRESS8K": 8192, "XPRESS16K": 16384, "LZX": 32768}
CLUSTER = 4096
PIECE = 128 * 1024
SMALL = 16 * 1024          # tiny files hardly gain anything and cost a lot of calls
SAMPLE_BUDGET = 64 * 1024 * 1024
MAX_PIECES_PER_FILE = 16
MIN_FILE_GAIN = 0.03
ALREADY = 0.97             # on-disk size below 97 % of the file size = already compressed
DIRECTSTORAGE_DLLS = {"dstorage.dll", "dstoragecore.dll"}
SKIP_EXT = {
    ".bk2", ".bik", ".usm", ".mp4", ".m4v", ".webm", ".mkv", ".avi", ".mov", ".wmv", ".ogv",
    ".ogg", ".opus", ".mp3", ".m4a", ".aac", ".flac", ".wma",
    ".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".ktx2", ".basis",
    ".zip", ".7z", ".rar", ".gz", ".xz", ".bz2", ".zst", ".lz4", ".cab", ".jar", ".apk", ".msi",
}


def _level(algo: str) -> int:
    return 9 if algo == "LZX" else 1


def model_size(data: bytes, algo: str) -> int:
    """Bytes Windows would use for this data with this algorithm (model)."""
    if not data:
        return 0
    chunk = CHUNKS.get(algo, 8192)
    lvl = _level(algo)
    used = 0
    for i in range(0, len(data), chunk):
        piece = data[i:i + chunk]
        used += min(len(piece), len(zlib.compress(piece, lvl)))
    used = -(-used // CLUSTER) * CLUSTER
    plain = -(-len(data) // CLUSTER) * CLUSTER
    return min(used, plain)


def piece_saving(data: bytes, algo: str) -> int:
    chunk = CHUNKS.get(algo, 8192)
    lvl = _level(algo)
    saved = 0
    for i in range(0, len(data), chunk):
        piece = data[i:i + chunk]
        saved += max(0, len(piece) - len(zlib.compress(piece, lvl)))
    return saved


def walk_files(folder: str) -> List[Tuple[str, int]]:
    out = []
    from .plat import is_reparse_point
    for dp, dns, fns in os.walk(folder):
        # never walk into links or junctions (they can point outside the game)
        dns[:] = [d for d in dns if not is_reparse_point(os.path.join(dp, d))]
        for n in fns:
            p = os.path.join(dp, n)
            try:
                if os.path.isfile(p) and not is_reparse_point(p):
                    out.append((p, os.lstat(p).st_size))
            except OSError:
                pass
    return out


def uses_directstorage(folder: str) -> bool:
    for _dp, _dns, fns in os.walk(folder):
        if any(n.lower() in DIRECTSTORAGE_DLLS for n in fns):
            return True
    return False


def _sample(path: str, size: int, pieces: int, algo: str) -> Tuple[int, int]:
    pieces = max(1, min(pieces, MAX_PIECES_PER_FILE, -(-size // PIECE)))
    read = saved = 0
    with open(path, "rb") as f:
        for k in range(pieces):
            off = (size - PIECE) * k // max(1, pieces - 1) if pieces > 1 and size > PIECE else 0
            off -= off % CLUSTER
            f.seek(off)
            data = f.read(PIECE)
            read += len(data)
            saved += piece_saving(data, algo)
    return read, saved


def estimate(folder: str, algo: str = "XPRESS8K",
             on_disk: Optional[Callable[[str], Optional[int]]] = None,
             progress=None, check=None, budget: int = SAMPLE_BUDGET) -> Dict[str, object]:
    files = walk_files(folder)
    total = sum(s for _, s in files)
    skipped_fmt = skipped_small = already = est_saved = sampled = 0
    worth: List[str] = []
    measured = True
    cand = []
    for p, s in files:
        if s < SMALL:
            skipped_small += s
        elif os.path.splitext(p)[1].lower() in SKIP_EXT:
            skipped_fmt += s
        else:
            cand.append((p, s))
    cand_total = sum(s for _, s in cand) or 1
    done = 0
    for p, s in cand:
        if check:
            check()
        disk = on_disk(p) if on_disk else None
        if disk is None:
            measured = False
        elif disk < s * ALREADY:
            already += s - disk
            done += s
            continue
        pieces = max(1, int(budget * s / cand_total) // PIECE)
        try:
            r, sv = _sample(p, s, pieces, algo)
        except OSError:
            continue
        sampled += r
        ratio = sv / r if r else 0.0
        if ratio >= MIN_FILE_GAIN:
            worth.append(p)
            est_saved += int(s * ratio)
        done += s
        if progress:
            progress(done, cand_total)
    pct = 100.0 * est_saved / total if total else 0.0
    if est_saved < 200 * 1024 ** 2 or pct < 3:
        verdict = "not worth it"
    elif pct < 10:
        verdict = "small gain"
    else:
        verdict = "worth it"
    ds = uses_directstorage(folder)
    worth_set = set(worth)
    return {"total": total, "saved": est_saved, "pct": round(pct, 1), "verdict": verdict,
            "already": already, "skipped_formats": skipped_fmt, "skipped_small": skipped_small,
            "sampled": sampled, "files": len(files), "worth_files": worth,
            "worth_bytes": sum(s for p, s in cand if p in worth_set), "algo": algo,
            "measured": measured, "directstorage": ds}


def on_disk_total(folder: str, on_disk: Callable[[str], Optional[int]]) -> Tuple[int, int]:
    """(file sizes, real on-disk sizes) for the whole folder."""
    size = disk = 0
    for p, s in walk_files(folder):
        size += s
        d = on_disk(p)
        disk += s if d is None else d
    return size, disk
