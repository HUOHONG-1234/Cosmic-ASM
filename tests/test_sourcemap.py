from __future__ import annotations

import dataclasses
import io
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cosmic_interpreter.c_backend import generate_c
from cosmic_interpreter.compiler import compile_source
from cosmic_interpreter.parser import parse
from cosmic_interpreter.runtime import Interpreter

SOURCE_LINE_COMMENT = re.compile(r"/\* \.cos line (\d+) \*/")

TOP_LEVEL_SOURCE = """\
var greeting = {"hello"}
call = (math)
print (<{greeting}>)
"""

BLOCK_SOURCE = """\
var greeting = {"hello"}
print (<{greeting}>)
call = (math)
father = (up)
start(normal) = {work}
{
    math_run (1) + (1) = ?
    if up = (ok) goto (push ({answer} = {(in = up)} ) )
    print (<{answer}>)
}
"""


def source_comments(generated: str) -> list[int]:
    """按出现顺序列出生成的 C 里的源码映射行号。"""
    return [int(match.group(1)) for match in SOURCE_LINE_COMMENT.finditer(generated)]


def run_interpreter(source: str) -> str:
    output = io.StringIO()
    Interpreter(output=output, sleep=lambda _seconds: None).run(parse(source))
    return output.getvalue()


class SourceMapTests(unittest.TestCase):
    def test_top_level_statements_get_line_comments(self) -> None:
        generated = generate_c(parse(TOP_LEVEL_SOURCE))
        self.assertEqual(source_comments(generated), [1, 2, 3])

        marked = generated.find("/* .cos line 3 */")
        self.assertNotEqual(marked, -1)
        following = generated[marked:].splitlines()[1].strip()
        # 注释必须紧跟在语句代码之前，不能落在表达式中间
        self.assertRegex(following, r"^if \(!context->vars\[\d+\]\.initialized\)")

    def test_block_statements_get_line_comments(self) -> None:
        generated = generate_c(parse(BLOCK_SOURCE))
        comments = source_comments(generated)
        # 第 5 行是 start 语句本身；第 6、10 行是块体花括号，不是语句
        self.assertEqual(sorted(comments), [1, 2, 3, 4, 5, 7, 8, 9])

        # 块内注释紧跟 line_N 标签之后，标签与语句之间仍保持合法 C
        self.assertIn("line_7: ;\n    /* .cos line 7 */\n", generated)

    def test_same_line_is_not_repeated(self) -> None:
        program = parse(TOP_LEVEL_SOURCE)
        duplicated = dataclasses.replace(
            program,
            statements=(program.statements[0], program.statements[2], program.statements[2]),
        )
        generated = generate_c(duplicated)
        self.assertEqual(source_comments(generated), [1, 3])

    def test_generated_program_matches_interpreter_output(self) -> None:
        self.assertEqual(run_interpreter(BLOCK_SOURCE), "hello\n2\n")

    @unittest.skipUnless(shutil.which("gcc"), "GCC is required for native compiler tests")
    def test_compiled_program_matches_interpreter(self) -> None:
        expected = run_interpreter(BLOCK_SOURCE)
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source_path = directory / "sourcemap.cos"
            executable_path = directory / "sourcemap.exe"
            source_path.write_text(BLOCK_SOURCE, encoding="utf-8")
            c_path, output_path = compile_source(source_path, output_path=executable_path)
            generated = c_path.read_text(encoding="utf-8")
            self.assertIn("/* .cos line 3 */", generated)
            self.assertIn("/* .cos line 7 */", generated)
            completed = subprocess.run(
                [str(output_path)],
                check=False,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
            self.assertEqual(completed.stdout.decode("utf-8").replace("\r\n", "\n"), expected)


if __name__ == "__main__":
    unittest.main()
