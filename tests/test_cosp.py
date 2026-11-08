from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from cosmic_interpreter.cosp import CospError, _install, _parse_manifest, _render_template, _safe_extract


class CospTests(unittest.TestCase):
    def test_builtin_scaffold_writes_manifest_and_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "nebula"
            root.mkdir()
            _render_template("nebula", root, "app")
            manifest = _parse_manifest(root / "cosp.toml")
            self.assertEqual(manifest["package"]["name"], "nebula")
            self.assertTrue((root / "main.cos").is_file())
            self.assertIn("nebula", (root / "README.md").read_text(encoding="utf-8"))

    def test_local_directory_dependency_installs_and_locks_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            package = root / "local_lib"
            project.mkdir()
            package.mkdir()
            (package / "cosp.toml").write_text(
                '[package]\nname = "local_lib"\nversion = "1.2.3"\nentry = "lib.cos"\n',
                encoding="utf-8",
            )
            (package / "lib.cos").write_text("math_run 1 = ?\n", encoding="utf-8")
            manifest = {
                "package": {"name": "project", "version": "0.1.0", "entry": "main.cos"},
                "dependencies": {"local_lib": "../local_lib"},
            }
            from cosmic_interpreter.cosp import _write_manifest

            _write_manifest(project, manifest)
            _install(project, manifest)
            lock = json.loads((project / "cosp.lock").read_text(encoding="utf-8"))
            self.assertEqual(lock["packages"]["local_lib"]["version"], "1.2.3")
            self.assertEqual(
                (project / ".cosp" / "packages" / "local_lib" / "lib.cos").read_text(encoding="utf-8"),
                "math_run 1 = ?\n",
            )
            self.assertRegex(lock["packages"]["local_lib"]["sha256"], r"^[0-9a-f]{64}$")
            installed_file = project / ".cosp" / "packages" / "local_lib" / "lib.cos"
            original_installed_content = installed_file.read_text(encoding="utf-8")
            (package / "lib.cos").write_text("math_run 2 = ?\n", encoding="utf-8")
            with self.assertRaisesRegex(CospError, "differs from cosp.lock"):
                _install(project, manifest)
            self.assertEqual(installed_file.read_text(encoding="utf-8"), original_installed_content)
            _install(project, manifest, refresh=True)
            self.assertEqual(installed_file.read_text(encoding="utf-8"), "math_run 2 = ?\n")

    def test_zip_extraction_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../outside.txt", "unsafe")
            with self.assertRaisesRegex(CospError, "unsafe archive member"):
                _safe_extract(archive, root / "destination")
            self.assertFalse((root / "outside.txt").exists())

    def test_zip_dependency_installs_without_executing_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            archive = root / "safe_package.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr(
                    "safe_package/cosp.toml",
                    '[package]\nname = "safe_package"\nversion = "1.0.0"\nentry = "lib.cos"\n',
                )
                output.writestr("safe_package/lib.cos", "# dependency source\n")
            manifest = {
                "package": {"name": "project", "version": "0.1.0", "entry": "main.cos"},
                "dependencies": {"safe_package": "../safe_package.zip"},
            }
            from cosmic_interpreter.cosp import _write_manifest

            _write_manifest(project, manifest)
            _install(project, manifest)
            installed = project / ".cosp" / "packages" / "safe_package"
            self.assertTrue((installed / "lib.cos").is_file())
            self.assertNotEqual(list(installed.iterdir()), [])

    def test_manifest_rejects_parent_entry_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "cosp.toml"
            manifest.write_text(
                '[package]\nname = "demo"\nversion = "0.1.0"\nentry = "../outside.cos"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CospError, "project-relative"):
                _parse_manifest(manifest)

    def test_custom_template_only_renders_listed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "files").mkdir()
            (root / "files" / "main.cos").write_text("# ${project_name}\n", encoding="utf-8")
            (root / "template.json").write_text(
                json.dumps({"files": ["main.cos"]}), encoding="utf-8"
            )
            output = root / "output"
            output.mkdir()
            _render_template("project_x", output, str(root))
            self.assertEqual(
                (output / "main.cos").read_text(encoding="utf-8"),
                "# project_x\n",
            )
            self.assertTrue((output / "cosp.toml").is_file())


if __name__ == "__main__":
    unittest.main()
