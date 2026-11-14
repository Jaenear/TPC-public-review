"""Bounded ISO BMFF movie-header inspection; never accepts client duration.

This reads container timing, not codec validity or continuous attendance. An
unknown/truncated/unsupported header stays unknown rather than becoming 0%.
"""

import math
import struct
from pathlib import Path


def mp4_duration(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return None
    try:
        size = path.stat().st_size
        count = 0
        with path.open("rb") as stream:

            def boxes(start, end):
                nonlocal count
                offset = start
                while offset < end:
                    count += 1
                    if count > 4096 or end - offset < 8:
                        raise ValueError("Invalid atom boundary")
                    stream.seek(offset)
                    header = stream.read(8)
                    if len(header) != 8:
                        raise ValueError("Truncated atom")
                    length, kind = struct.unpack(">I4s", header)
                    width = 8
                    if length == 1:
                        raw = stream.read(8)
                        if len(raw) != 8:
                            raise ValueError("Truncated large atom")
                        length = struct.unpack(">Q", raw)[0]
                        width = 16
                    elif length == 0:
                        length = end - offset
                    if length < width or length > end - offset:
                        raise ValueError("Atom outside parent")
                    yield kind, offset + width, offset + length
                    offset += length

            duration = None
            for kind, start, end in boxes(0, size):
                if kind != b"moov":
                    continue
                for child, body, finish in boxes(start, end):
                    if child != b"mvhd":
                        continue
                    if duration is not None:
                        return None
                    stream.seek(body)
                    raw = stream.read(min(32, finish - body))
                    if not raw:
                        return None
                    if raw[0] == 0 and len(raw) >= 20:
                        scale, units = struct.unpack(">II", raw[12:20])
                        unknown = 0xFFFFFFFF
                    elif raw[0] == 1 and len(raw) >= 32:
                        scale, units = struct.unpack(">IQ", raw[20:32])
                        unknown = 0xFFFFFFFFFFFFFFFF
                    else:
                        return None
                    if not scale or not units or units == unknown:
                        return None
                    duration = units / scale
            return (
                duration
                if duration is not None
                and math.isfinite(duration)
                and 0 < duration <= 86400
                else None
            )
    except (OSError, ValueError, struct.error, OverflowError):
        return None
