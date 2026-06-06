from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from extensions.package_radar_chrome import EXTENSION_FILES, build_extension_package


EXTENSION_DIR = Path("extensions/radar-chrome")


class ChromeExtensionManifestTest(unittest.TestCase):
    def test_manifest_is_named_radar_extension(self) -> None:
        manifest = json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest["name"], "Radar Extension")
        self.assertEqual(manifest["background"]["service_worker"], "service_worker.js")
        self.assertEqual(manifest["action"]["default_popup"], "popup.html")
        self.assertIn("http://127.0.0.1:47321/*", manifest["host_permissions"])

    def test_content_script_runs_on_http_and_https_pages(self) -> None:
        manifest = json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
        content_script = manifest["content_scripts"][0]

        self.assertEqual(content_script["js"], ["content_script.js"])
        self.assertEqual(content_script["run_at"], "document_idle")
        self.assertEqual(content_script["matches"], ["http://*/*", "https://*/*"])


class ChromeExtensionSyntaxTest(unittest.TestCase):
    def test_javascript_files_parse(self) -> None:
        for path in (
            EXTENSION_DIR / "service_worker.js",
            EXTENSION_DIR / "content_script.js",
            EXTENSION_DIR / "popup.js",
        ):
            with self.subTest(path=path):
                subprocess.run(["node", "--check", str(path)], check=True)


class ChromeExtensionPackageTest(unittest.TestCase):
    def test_package_contains_installable_extension_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            package = build_extension_package(output_dir=Path(tmpdir))

            self.assertTrue((package.unpacked_dir / "manifest.json").is_file())
            self.assertTrue(package.zip_path.is_file())
            with zipfile.ZipFile(package.zip_path) as archive:
                self.assertEqual(set(archive.namelist()), set(EXTENSION_FILES))


if __name__ == "__main__":
    unittest.main()
