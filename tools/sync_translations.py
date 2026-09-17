"""Generate ``translations/en.json`` from ``strings.json``.

Home Assistant loads an integration's strings from ``translations/<language>.json``.
``strings.json`` is the version that ships in the source tree and is not read at
runtime, so the two files have to hold the same document or an entity's name
disappears and Home Assistant falls back to the device class.

Run from the repository root:

    python tools/sync_translations.py

``tests/test_entities.py`` asserts the two files match, so a forgotten run fails
the suite rather than silently renaming entities.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: Languages generated from ``strings.json``. English is the source language.
LANGUAGES = ("en",)


def render(document: dict) -> str:
    """Return the canonical serialisation of a translation document."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def sync(package: Path) -> list[Path]:
    """Write every generated translation file and return the paths written."""
    source = package / "strings.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    target_dir = package / "translations"
    target_dir.mkdir(exist_ok=True)
    written: list[Path] = []
    for language in LANGUAGES:
        target = target_dir / f"{language}.json"
        target.write_text(render(document), encoding="utf-8")
        written.append(target)
    return written


def main(argv: list[str]) -> int:
    """Sync the translations of the package given on the command line."""
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    package = root / "custom_components" / "generic_3dprinter"
    for path in sync(package):
        print(f"wrote {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
