"""Package the Radar Chrome extension for local install or distribution."""

from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = REPO_ROOT / "extensions" / "radar-chrome"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "dist"
PACKAGE_NAME = "radar-extension"
EXTENSION_FILES = (
    "manifest.json",
    "service_worker.js",
    "content_script.js",
    "popup.html",
    "popup.css",
    "popup.js",
)
ZIP_ENTRY_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class ExtensionPackage:
    unpacked_dir: Path
    zip_path: Path


def validate_extension_source(source_dir: Path) -> None:
    missing = [name for name in EXTENSION_FILES if not (source_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"missing extension files: {', '.join(missing)}")

    manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("manifest_version") != 3:
        raise ValueError("Radar Extension must use manifest_version 3")
    if manifest.get("name") != "Radar Extension":
        raise ValueError("manifest name must be 'Radar Extension'")


def build_extension_package(
    source_dir: Path = DEFAULT_SOURCE_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> ExtensionPackage:
    source_dir = source_dir.resolve()
    output_dir = output_dir.resolve()
    validate_extension_source(source_dir)

    unpacked_dir = output_dir / PACKAGE_NAME
    zip_path = output_dir / f"{PACKAGE_NAME}.zip"

    if unpacked_dir.exists():
        shutil.rmtree(unpacked_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    unpacked_dir.mkdir(parents=True)

    for filename in EXTENSION_FILES:
        shutil.copy2(source_dir / filename, unpacked_dir / filename)

    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in EXTENSION_FILES:
            info = zipfile.ZipInfo(filename, ZIP_ENTRY_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (unpacked_dir / filename).read_bytes())

    return ExtensionPackage(unpacked_dir=unpacked_dir, zip_path=zip_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package the Radar Chrome extension.")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help="Extension source directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for the unpacked copy and zip artifact.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    package = build_extension_package(args.source_dir, args.output_dir)
    print(f"unpacked={package.unpacked_dir}")
    print(f"zip={package.zip_path}")


if __name__ == "__main__":
    main()
