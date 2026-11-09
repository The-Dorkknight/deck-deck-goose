# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Reader and writer for Valve's text KeyValues files (.vdf / .acf).

Only the text format Steam uses for appmanifest_*.acf and libraryfolders.vdf.
Keys and values are strings; blocks become dicts (insertion order kept).
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Union

Value = Union[str, "Node"]
Node = Dict[str, Value]

_ESC = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def _tokens(text: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n﻿":
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
        elif c in "{}":
            out.append((c, c))
            i += 1
        elif c == '"':
            i += 1
            buf = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    buf.append(_ESC.get(text[i + 1], "\\" + text[i + 1]))
                    i += 2
                else:
                    buf.append(text[i])
                    i += 1
            if i >= n:
                raise ValueError("unterminated string in VDF")
            out.append(("s", "".join(buf)))
            i += 1
        elif c == "[":  # platform conditional such as [$WIN32]: ignored
            j = text.find("]", i)
            i = n if j < 0 else j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n{}"':
                j += 1
            out.append(("s", text[i:j]))
            i = j
    return out


def loads(text: str) -> Node:
    toks = _tokens(text)
    pos = 0

    def block(top: bool) -> Node:
        nonlocal pos
        node: Node = {}
        while pos < len(toks):
            kind, val = toks[pos]
            if kind == "}":
                if top:
                    raise ValueError("unexpected } in VDF")
                pos += 1
                return node
            if kind != "s":
                raise ValueError("expected a key in VDF")
            pos += 1
            if pos >= len(toks):
                raise ValueError("key without value in VDF")
            k2, v2 = toks[pos]
            if k2 == "{":
                pos += 1
                node[val] = block(False)
            elif k2 == "s":
                pos += 1
                node[val] = v2
            else:
                raise ValueError("unexpected } in VDF")
        if not top:
            raise ValueError("missing } in VDF")
        return node

    return block(True)


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def dumps(node: Node, indent: int = 0) -> str:
    lines: List[str] = []
    tab = "\t" * indent
    for k, v in node.items():
        if isinstance(v, dict):
            lines.append(tab + _q(k))
            lines.append(tab + "{")
            inner = dumps(v, indent + 1)
            if inner:
                lines.append(inner.rstrip("\n"))
            lines.append(tab + "}")
        else:
            lines.append(tab + _q(k) + "\t\t" + _q(str(v)))
    return "\n".join(lines) + ("\n" if lines else "")


def load_file(path, strict: bool = False) -> Node:
    """strict=True refuses files that are not valid UTF-8 (used before
    rewriting a file, so no characters get mangled)."""
    try:
        with open(str(path), encoding="utf-8", errors="strict" if strict else "replace") as f:
            return loads(f.read())
    except UnicodeDecodeError as e:
        raise ValueError("not valid UTF-8 text (%s)" % e)


def dump_file(node: Node, path) -> None:
    """Write atomically: temp file next to it, then rename."""
    import os
    tmp = str(path) + ".thispc-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(dumps(node))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, str(path))


# ------------------------------------------------------------ binary format
# Used by Steam for shortcuts.vdf (non-Steam games). Each item starts with a
# type byte and a NUL-terminated key: 0x00 = nested block, 0x01 = text,
# 0x02 = 32-bit number, 0x03 = 32-bit float, 0x07 = 64-bit number; 0x08 ends
# a block. Unknown types make the reader refuse, so we never rewrite a file
# we do not fully understand.
import struct as _struct


class U64(int):
    """A 64-bit number field (type 0x07), kept as such when written back."""


class F32(float):
    """A float field (type 0x03), kept as such when written back."""


def loads_binary(data: bytes) -> Node:
    pos = 0

    def cstr() -> str:
        nonlocal pos
        end = data.index(b"\0", pos)
        s = data[pos:end].decode("utf-8")
        pos = end + 1
        return s

    def block() -> Node:
        nonlocal pos
        node: Node = {}
        while True:
            if pos >= len(data):
                raise ValueError("binary VDF ended early")
            t = data[pos]
            pos += 1
            if t == 0x08:
                return node
            key = cstr()
            if t == 0x00:
                node[key] = block()
            elif t == 0x01:
                node[key] = cstr()
            elif t == 0x02:
                node[key] = _struct.unpack_from("<i", data, pos)[0]
                pos += 4
            elif t == 0x03:
                node[key] = F32(_struct.unpack_from("<f", data, pos)[0])
                pos += 4
            elif t == 0x07:
                node[key] = U64(_struct.unpack_from("<Q", data, pos)[0])
                pos += 8
            else:
                raise ValueError("unknown binary VDF type 0x%02x" % t)

    try:
        out = block()
    except (IndexError, _struct.error, UnicodeDecodeError) as e:
        raise ValueError("damaged binary VDF (%s)" % e)
    if pos < len(data) and data[pos:].strip(b"\x08\0"):
        raise ValueError("unexpected data after the end of the binary VDF")
    return out


def dumps_binary(node: Node) -> bytes:
    out = bytearray()

    def key(k: str) -> bytes:
        return str(k).encode("utf-8") + b"\0"

    def block(n: Node) -> None:
        for k, v in n.items():
            if isinstance(v, dict):
                out.append(0x00)
                out.extend(key(k))
                block(v)
            elif isinstance(v, U64):
                out.append(0x07)
                out.extend(key(k))
                out.extend(_struct.pack("<Q", int(v)))
            elif isinstance(v, F32):
                out.append(0x03)
                out.extend(key(k))
                out.extend(_struct.pack("<f", float(v)))
            elif isinstance(v, bool) or isinstance(v, int):
                out.append(0x02)
                out.extend(key(k))
                out.extend(_struct.pack("<i", int(v)))
            else:
                out.append(0x01)
                out.extend(key(k))
                out.extend(str(v).encode("utf-8") + b"\0")
        out.append(0x08)

    block(node)   # Steam's files end with the root block's 0x08 (after the entries' own 0x08s)
    return bytes(out)
