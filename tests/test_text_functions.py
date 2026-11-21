"""v9 A-2：用户函数的文本参数 / 文本返回，解释器与 C 后端行为一致。"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cosmic_interpreter.compiler import compile_source
from cosmic_interpreter.parser import parse
from cosmic_interpreter.runtime import Interpreter


class TextFunctionTests(unittest.TestCase):
    """解释器侧行为。"""

    def run_source(self, source: str) -> str:
        output = io.StringIO()
        Interpreter(output=output, error_output=io.StringIO(), sleep=lambda _seconds: None).run(
            parse(source)
        )
        return output.getvalue()

    def assert_text_function(self, source: str, expected: str) -> None:
        self.assertEqual(self.run_source(source), expected)


@unittest.skipUnless(shutil.which("gcc"), "GCC is required for native compiler tests")
class CompiledTextFunctionTests(TextFunctionTests):
    """C 后端必须和解释器给出同样的输出。"""

    def run_source(self, source: str) -> str:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source_path = directory / "program.cos"
            source_path.write_text(source, encoding="utf-8")
            _c_path, executable = compile_source(
                source_path, output_path=directory / "program.exe"
            )
            completed = subprocess.run(
                [str(executable)], check=False, capture_output=True, timeout=60
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        return completed.stdout.decode(errors="replace").replace("\r\n", "\n")


class TextFunctionCases:
    def test_annotated_parameter_and_return(self) -> None:
        self.assert_text_function(
            "function greet(name : text) -> text = upper(name)\n"
            'math_run greet("cosmic") = ?\n'
            "print (<{(in = up)}>)\n",
            "COSMIC\n",
        )

    def test_unannotated_parameter_is_inferred_from_usage(self) -> None:
        self.assert_text_function(
            "function shout(word) = upper(word)\n"
            'math_run shout("hi") = ?\n'
            "print (<{(in = up)}>)\n",
            "HI\n",
        )

    def test_braced_parameter_reference(self) -> None:
        self.assert_text_function(
            "function greet(name : text) -> text = upper({name})\n"
            'math_run greet("zz") = ?\n'
            "print (<{(in = up)}>)\n",
            "ZZ\n",
        )

    def test_statement_body_text_function(self) -> None:
        self.assert_text_function(
            "function tidy(raw : text) -> text\n"
            "{\n"
            "let trimmed = trim(raw)\n"
            "return lower(trimmed)\n"
            "}\n"
            'math_run tidy("  Cosmic  ") = ?\n'
            "print (<{(in = up)}>)\n",
            "cosmic\n",
        )

    def test_mixed_parameter_types(self) -> None:
        self.assert_text_function(
            "function pad(count, text_value : text) -> text = repeat_text(text_value, count)\n"
            'math_run pad(3, "ab") = ?\n'
            "print (<{(in = up)}>)\n",
            "ababab\n",
        )

    def test_text_function_returning_number(self) -> None:
        self.assert_text_function(
            "function measure(text_value : text) = strlen(text_value)\n"
            'math_run measure("abcd") = ?\n'
            "if up = (ok) goto (push ({size} = {(in = up)}))\n"
            "print (<{size}>)\n",
            "4\n",
        )

    def test_text_function_feeds_another_text_function(self) -> None:
        self.assert_text_function(
            "function inner(raw) = trim(raw)\n"
            "function outer(raw) = lower(inner(raw))\n"
            'math_run outer("  ABC  ") = ?\n'
            "print (<{(in = up)}>)\n",
            "abc\n",
        )

    def test_text_literal_return(self) -> None:
        self.assert_text_function(
            'function tag() = {"#[v9]"}\n'
            "math_run tag() = ?\n"
            "print (<{(in = up)}>)\n",
            "#[v9]\n",
        )

    def test_two_text_arguments_inside_one_function(self) -> None:
        self.assert_text_function(
            'function swap_case(raw) = replace(replace(raw, "a", "A"), "b", "B")\n'
            'math_run swap_case("aabb") = ?\n'
            "print (<{(in = up)}>)\n",
            "AABB\n",
        )


class InterpreterTextFunctionTests(TextFunctionCases, TextFunctionTests):
    pass


class CompiledTextFunctionCases(TextFunctionCases, CompiledTextFunctionTests):
    pass


del TextFunctionCases


if __name__ == "__main__":
    unittest.main()
