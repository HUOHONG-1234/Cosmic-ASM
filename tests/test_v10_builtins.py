"""v10 阶段 3（B5）：文本拼接三内置，双后端 parity。

没有通用拼接就写不了「往缓冲区追加」的代码生成函数，所以这三个内置是自举的前置条件。
"""

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


def run_interpreted(source: str) -> str:
    output = io.StringIO()
    Interpreter(
        output=output, error_output=io.StringIO(), sleep=lambda _seconds: None
    ).run(parse(source))
    return output.getvalue().replace("\r\n", "\n")


@unittest.skipUnless(shutil.which("gcc"), "GCC is required for native compiler tests")
def run_compiled(source: str) -> str:
    with tempfile.TemporaryDirectory() as temporary_directory:
        directory = Path(temporary_directory)
        source_path = directory / "program.cos"
        source_path.write_bytes(source.encode("utf-8"))
        _c_path, executable = compile_source(
            source_path, output_path=directory / "program.exe"
        )
        completed = subprocess.run(
            [str(executable)], check=False, capture_output=True, timeout=120
        )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.decode(errors="replace"))
    return completed.stdout.decode(errors="replace").replace("\r\n", "\n")


class TextBuilderTests(unittest.TestCase):
    def assert_parity(self, source: str, expected: str) -> None:
        interpreted = run_interpreted(source)
        self.assertEqual(interpreted, expected)
        if shutil.which("gcc"):
            self.assertEqual(run_compiled(source), expected)

    def test_text_concat(self) -> None:
        self.assert_parity(
            'math_run text_concat("ab", "cd") = ?\nprint (<{(in = up)}>)\n',
            "abcd\n",
        )

    def test_to_text_uses_output_precision(self) -> None:
        self.assert_parity(
            "math_run to_text(1 / 3) = ?\nprint (<{(in = up)}>)\n",
            "0.333333333333333\n",
        )

    def test_text_join_numbers_with_separator(self) -> None:
        self.assert_parity(
            'math_run text_join([1, 2, 3], "-") = ?\nprint (<{(in = up)}>)\n',
            "1-2-3\n",
        )

    def test_nested_builders(self) -> None:
        self.assert_parity(
            'math_run text_concat(to_text(7), "!") = ?\nprint (<{(in = up)}>)\n',
            "7!\n",
        )

    def test_builders_inside_text_function(self) -> None:
        self.assert_parity(
            "function tag(n) -> text = text_concat(to_text(n), \" items\")\n"
            "math_run tag(3) = ?\n"
            "print (<{(in = up)}>)\n",
            "3 items\n",
        )

    def test_accumulating_buffer_in_a_loop(self) -> None:
        # 这就是「代码生成」的最小模型：不断往缓冲区追加文本。
        self.assert_parity(
            "function build(n)\n"
            "{\n"
            'let acc = ""\n'
            "let i = 0\n"
            "while (i < n)\n"
            "{\n"
            "set acc = text_concat(acc, to_text(i))\n"
            "set i = i + 1\n"
            "}\n"
            "return acc\n"
            "}\n"
            "math_run build(6) = ?\n"
            "print (<{(in = up)}>)\n",
            "012345\n",
        )

    def test_one_megabyte_string_still_works(self) -> None:
        # 1 MiB 压测：结果必须完整，不能截断也不能超限报错。
        source = (
            "function grow(times)\n"
            "{\n"
            'let acc = ""\n'
            "let i = 0\n"
            "while (i < times)\n"
            "{\n"
            'set acc = text_concat(acc, "0123456789abcdef")\n'
            "set i = i + 1\n"
            "}\n"
            "return acc\n"
            "}\n"
            "math_run strlen(grow(65536)) = ?\n"
            "if up = (ok) goto (push ({r} = {(in = up)}))\n"
            "print (<{r}>)\n"
        )
        self.assert_parity(source, "1048576\n")

    def test_user_function_call_nested_in_text_result(self) -> None:
        # 文本结果的 math_run 也要认得用户函数（回归：曾经只传内置表）。
        self.assert_parity(
            "function twice(n) = n * 2\n"
            'math_run text_concat("r=", to_text(twice(21))) = ?\n'
            "print (<{(in = up)}>)\n",
            "r=42\n",
        )

    def test_text_argument_parameters_still_infer_as_text(self) -> None:
        # text_concat 的两个实参都是文本位置，未标注参数要按 text 推断。
        self.assert_parity(
            "function wrap(a, b) = text_concat(a, b)\n"
            'math_run wrap("<", ">") = ?\n'
            "if up = (ok) goto (push ({r} = {(in = up)}))\n"
            "print (<{r}>)\n",
            "<>\n",
        )


if __name__ == "__main__":
    unittest.main()
