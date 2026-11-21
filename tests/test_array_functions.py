"""v9 A-3：数组作为函数参数与返回值，解释器与 C 后端行为一致。"""

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


class ArrayFunctionTests(unittest.TestCase):
    def run_source(self, source: str) -> str:
        output = io.StringIO()
        Interpreter(output=output, error_output=io.StringIO(), sleep=lambda _seconds: None).run(
            parse(source)
        )
        return output.getvalue()

    def assert_array_function(self, source: str, expected: str) -> None:
        self.assertEqual(self.run_source(source), expected)


@unittest.skipUnless(shutil.which("gcc"), "GCC is required for native compiler tests")
class CompiledArrayFunctionTests(ArrayFunctionTests):
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


PRINT_NUMBER = (
    "if up = (ok) goto (push ({shown} = {(in = up)}))\n"
    "print (<{shown}>)\n"
)


class ArrayFunctionCases:
    def test_array_parameter_length(self) -> None:
        self.assert_array_function(
            "array data = array_fill(3, 1)\n"
            "function count(values) = array_len(values)\n"
            "math_run count(data) = ?\n" + PRINT_NUMBER,
            "3\n",
        )

    def test_array_parameter_sum(self) -> None:
        self.assert_array_function(
            "array data = array_fill(4, 2)\n"
            "function total(values) = array_sum(values)\n"
            "math_run total(data) = ?\n" + PRINT_NUMBER,
            "8\n",
        )

    def test_array_parameter_annotation(self) -> None:
        self.assert_array_function(
            "array data = [1, 2, 3]\n"
            "function total(values : array) = array_sum(values)\n"
            "math_run total(data) = ?\n" + PRINT_NUMBER,
            "6\n",
        )

    def test_array_parameter_passed_to_builtin_sorting(self) -> None:
        self.assert_array_function(
            "array data = [3, 1, 2]\n"
            "function smallest(values) = array_at(sort_array(values), 0)\n"
            "math_run smallest(data) = ?\n" + PRINT_NUMBER,
            "1\n",
        )

    def test_array_return_value_used_inline(self) -> None:
        self.assert_array_function(
            "function build(size) = array_fill(size, 7)\n"
            "math_run array_sum(build(3)) = ?\n" + PRINT_NUMBER,
            "21\n",
        )

    def test_array_return_value_stored_in_array_variable(self) -> None:
        self.assert_array_function(
            "function build(size) = array_fill(size, 5)\n"
            "array data = build(2)\n"
            "math_run array_sum(data) = ?\n" + PRINT_NUMBER,
            "10\n",
        )

    def test_array_return_value_from_statement_body(self) -> None:
        self.assert_array_function(
            "function build(size)\n"
            "{\n"
            "return array_fill(size, 4)\n"
            "}\n"
            "math_run array_len(build(2)) = ?\n" + PRINT_NUMBER,
            "2\n",
        )

    def test_array_parameter_in_statement_body(self) -> None:
        self.assert_array_function(
            "array data = array_fill(3, 2)\n"
            "function total(values)\n"
            "{\n"
            "return array_sum(values)\n"
            "}\n"
            "math_run total(data) = ?\n" + PRINT_NUMBER,
            "6\n",
        )

    def test_array_function_chain(self) -> None:
        self.assert_array_function(
            "function build(size) = array_fill(size, 1)\n"
            "function total(values) = array_sum(array_push(values, 9))\n"
            "math_run total(build(3)) = ?\n" + PRINT_NUMBER,
            "12\n",
        )

    def test_scalar_parameter_rejects_array_argument(self) -> None:
        # 两边都必须报错，而不是悄悄生成错误的代码。
        with self.assertRaises(Exception):
            self.run_source(
                "function build(size) = array_fill(size, 1)\n"
                "function plain(value) = value + 1\n"
                "math_run plain(build(2)) = ?\n"
            )


class InterpreterArrayFunctionTests(ArrayFunctionCases, ArrayFunctionTests):
    pass


class CompiledArrayFunctionCases(ArrayFunctionCases, CompiledArrayFunctionTests):
    pass


del ArrayFunctionCases


if __name__ == "__main__":
    unittest.main()
