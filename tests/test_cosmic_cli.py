from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from cosmic_interpreter import cosmic_cli


class CosmicCliTests(unittest.TestCase):
    def test_version_reports_tool_and_python(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(cosmic_cli.main(["version"]), 0)
        output = buffer.getvalue()
        self.assertIn("cosmic 0.1.0", output)
        self.assertIn("python", output)

    def test_run_dispatches_to_interpreter(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "hello.cos"
            source.write_text('print (<{"hi"}>)\n', encoding="utf-8")
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = cosmic_cli.main(["run", str(source)])
        self.assertEqual(code, 0)
        self.assertEqual(buffer.getvalue().strip(), "hi")

    def test_run_reports_errors_with_nonzero_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bad.cos"
            source.write_text("行数 文本\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                code = cosmic_cli.main(["run", str(source)])
        self.assertEqual(code, 1)

    def test_build_compiles_a_program(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "prog.cos"
            source.write_text(
                '#%math = int64\nmath_run 1 + 1 = ? -int64\n'
                'if up = (ok) goto (push ({r} = {(in = up)}))\nprint (<{r}>)\n',
                encoding="utf-8",
            )
            executable = directory / "prog.exe"
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = cosmic_cli.main(
                    ["build", str(source), "-o", str(executable)]
                )
            self.assertEqual(code, 0, buffer.getvalue())
            self.assertTrue(executable.is_file())

    def test_pkg_and_index_dispatch_to_cosp(self):
        calls: list[list[str]] = []

        def fake_cosp(argv: list[str]) -> int:
            calls.append(list(argv))
            return 0

        original = cosmic_cli._pkg
        cosmic_cli._pkg = fake_cosp
        try:
            self.assertEqual(cosmic_cli.main(["pkg", "install"]), 0)
            self.assertEqual(cosmic_cli.main(["index", "--index", "x.json"]), 0)
        finally:
            cosmic_cli._pkg = original
        self.assertEqual(calls[0], ["install"])
        self.assertEqual(calls[1], ["index", "--index", "x.json"])

    def test_repl_subcommands_use_repl_module(self):
        called: list[int] = []

        def fake_repl() -> int:
            called.append(1)
            return 0

        original = cosmic_cli._repl
        cosmic_cli._repl = fake_repl
        try:
            self.assertEqual(cosmic_cli.main(["tui"]), 0)
            self.assertEqual(cosmic_cli.main(["run", "--repl"]), 0)
        finally:
            cosmic_cli._repl = original
        self.assertEqual(len(called), 2)


if __name__ == "__main__":
    unittest.main()
