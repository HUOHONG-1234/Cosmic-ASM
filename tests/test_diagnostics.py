"""v9 主线 D：rustc 风格诊断渲染。"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from cosmic_interpreter.diagnostics import (
    Diagnostic,
    format_message,
    render,
)
from cosmic_interpreter.parser import ParseError


class RenderTests(unittest.TestCase):
    def test_render_points_at_the_given_column(self) -> None:
        text = render(
            Diagnostic(
                message="unsupported expression syntax",
                line=12,
                column=14,
                path="examples/foo.cos",
                source="    math_run {a} + = ? -int64",
                help="check the expression inside the braces",
            )
        )
        self.assertIn("error: unsupported expression syntax", text)
        self.assertIn("--> examples/foo.cos:12:14", text)
        self.assertIn("12 |     math_run {a} + = ? -int64", text)
        caret_line = next(line for line in text.splitlines() if "^" in line)
        self.assertEqual(caret_line.index("^"), len("12 | ") + 13)
        self.assertIn("help: check the expression inside the braces", text)

    def test_missing_column_falls_back_to_first_token(self) -> None:
        text = render(
            Diagnostic(
                message="unsupported scientific function 'nope'",
                line=3,
                source="math_run nope(1) = ?",
            )
        )
        caret_line = next(line for line in text.splitlines() if "^" in line)
        # 尖号落在 'nope' 的第一个字符下面。
        self.assertEqual(caret_line.index("^"), len("3 | ") + "math_run nope(1) = ?".index("nope"))

    def test_known_message_gets_a_help_line(self) -> None:
        text = render(
            Diagnostic(message="local 'u' is already declared", line=4, source="let u = 1")
        )
        self.assertIn("help: use 'set name = ...' to update an existing variable", text)

    def test_format_message_without_line_prefix_is_single_line(self) -> None:
        self.assertEqual(
            format_message("GCC is not installed"), "error: GCC is not installed"
        )

    def test_format_message_reads_the_offending_source_line(self) -> None:
        text = format_message(
            "line 2: unknown component 'blas'",
            source_text="import (math)\nimport (blas)\n",
            path="demo.cos",
        )
        self.assertIn("--> demo.cos:2", text)
        self.assertIn("2 | import (blas)", text)
        self.assertIn("help: available components are: execute, math, atom", text)

    def test_error_without_line_still_renders(self) -> None:
        diagnostic = Diagnostic(message="something broke")
        self.assertEqual(render(diagnostic), "error: something broke")


class CliIntegrationTests(unittest.TestCase):
    def test_run_reports_a_rustc_style_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "bad.cos"
            source.write_bytes(b"math_run {a} + = ? -int64\n")
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
            completed = subprocess.run(
                [sys.executable, "-m", "cosmic_interpreter.cli", str(source)],
                capture_output=True,
                text=True,
                env=environment,
                timeout=120,
            )
        self.assertEqual(completed.returncode, 1)
        self.assertRegex(completed.stderr, r"^error: ")
        self.assertIn("--> ", completed.stderr)
        self.assertIn("1 | math_run {a} + = ? -int64", completed.stderr)
        self.assertIn("^", completed.stderr)


class RuntimeErrorDiagnosticTests(unittest.TestCase):
    def test_parse_error_carries_the_line_number(self) -> None:
        from cosmic_interpreter.cli import main

        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "bad.cos"
            source.write_bytes(b"math_run {a} + = ? -int64\n")
            buffer = io.StringIO()
            with redirect_stderr(buffer):
                code = main([str(source)])
        self.assertEqual(code, 1)
        self.assertIn("error: ", buffer.getvalue())

    def test_parse_error_type_has_line_attribute(self) -> None:
        error = ParseError(7, "boom")
        self.assertEqual(error.line, 7)
        self.assertTrue(re.search("boom", str(error)))


if __name__ == "__main__":
    unittest.main()
