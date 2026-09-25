"""Frame splitting for the multipart JPEG streams printer cameras serve.

Shared by the adapters rather than owned by one, because the registry imports each
adapter on its own and one adapter importing another would let a broken module
take a second protocol down with it.
"""

from __future__ import annotations

from typing import Final

JPEG_SOI: Final = b"\xff\xd8"
JPEG_EOI: Final = b"\xff\xd9"


def jpeg_frames(buffer: bytearray, *, first_only: bool = False) -> list[bytes]:
    """Remove every complete JPEG from the front of ``buffer``.

    The printer's camera emits a full JPEG per multipart part, so a frame is
    delimited by the start-of-image and end-of-image markers rather than by the
    part headers, which are not reliable on this build.
    """
    frames: list[bytes] = []
    while True:
        start = buffer.find(JPEG_SOI)
        if start < 0:
            buffer.clear()
            return frames
        end = buffer.find(JPEG_EOI, start + len(JPEG_SOI))
        if end < 0:
            if start:
                del buffer[:start]
            return frames
        frames.append(bytes(buffer[start : end + len(JPEG_EOI)]))
        del buffer[: end + len(JPEG_EOI)]
        if first_only:
            return frames
