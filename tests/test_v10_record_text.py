"""v10 阶段 6（B7）：记录字段可以是文本，双后端 parity。

自举需要 AST 节点带文本字段（标识符名、字面量内容），所以记录必须能装 text。
C 侧为此把记录从「字段数组」换成了真 struct。
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


class V10ExampleTests(unittest.TestCase):
    """examples/v10_omega.cos 锁住 v10 的四项能力（输出双后端一致）。"""

    def test_example_matches_between_backends(self) -> None:
        from cosmic_interpreter.source_loader import load_source

        source_path = Path(__file__).resolve().parent.parent / "examples" / "v10_omega.cos"
        interpreted = io.StringIO()
        Interpreter(
            output=interpreted, error_output=io.StringIO(), sleep=lambda _s: None
        ).run(parse(load_source(source_path)))
        expected = interpreted.getvalue().replace("\r\n", "\n")
        self.assertEqual(
            expected.splitlines(),
            [
                "depth(1000) = 1000",
                "list = item1,item2,item3,item4",
                "joined = 10 / 20 / 30",
                "token = let:x@3",
                "token line 2",
                "token line 1",
            ],
        )
        if shutil.which("gcc"):
            self.assertEqual(run_compiled(load_source(source_path)), expected)


class RecordTextFieldTests(unittest.TestCase):
    def assert_parity(self, source: str, expected: str) -> None:
        self.assertEqual(run_interpreted(source), expected)
        if shutil.which("gcc"):
            self.assertEqual(run_compiled(source), expected)

    def test_text_field_local(self) -> None:
        self.assert_parity(
            "record Node { kind : text, value }\n"
            "function demo()\n"
            "{\n"
            'let n = Node("id", 7)\n'
            "return text_concat(n.kind, to_text(n.value))\n"
            "}\n"
            "math_run demo() = ?\n"
            "print (<{(in = up)}>)\n",
            "id7\n",
        )

    def test_text_field_as_parameter(self) -> None:
        self.assert_parity(
            "record Node { kind : text, value }\n"
            "function describe(n : Node) -> text = text_concat(n.kind, to_text(n.value))\n"
            'math_run describe(Node("num", 42)) = ?\n'
            "print (<{(in = up)}>)\n",
            "num42\n",
        )

    def test_text_field_as_return_value(self) -> None:
        self.assert_parity(
            "record Node { kind : text, value }\n"
            "function mk(k : text, v) -> Node = Node(k, v)\n"
            "function demo() -> text\n"
            "{\n"
            'let a = mk("x", 1)\n'
            "return a.kind\n"
            "}\n"
            "math_run demo() = ?\n"
            "print (<{(in = up)}>)\n",
            "x\n",
        )

    def test_record_chained_through_two_functions(self) -> None:
        self.assert_parity(
            "record Node { kind : text, value }\n"
            "function mk(k : text, v) -> Node = Node(k, v)\n"
            "function bump(n : Node) -> Node = Node(n.kind, n.value + 1)\n"
            "function demo() -> text\n"
            "{\n"
            'let a = mk("n", 1)\n'
            "let b = bump(a)\n"
            "return text_concat(b.kind, to_text(b.value))\n"
            "}\n"
            "math_run demo() = ?\n"
            "print (<{(in = up)}>)\n",
            "n2\n",
        )

    def test_mixed_field_types(self) -> None:
        self.assert_parity(
            "record Token { text : text, line, column }\n"
            "function label(t : Token) -> text\n"
            "{\n"
            'return text_concat(t.text, text_concat("@", text_concat(to_text(t.line), text_concat(":", to_text(t.column)))))\n'
            "}\n"
            'math_run label(Token("let", 3, 11)) = ?\n'
            "print (<{(in = up)}>)\n",
            "let@3:11\n",
        )

    def test_text_field_can_be_set(self) -> None:
        self.assert_parity(
            "record Node { kind : text, value }\n"
            "function demo()\n"
            "{\n"
            'let n = Node("id", 7)\n'
            'set n.kind = "code"\n'
            "set n.value = 9\n"
            "return text_concat(n.kind, to_text(n.value))\n"
            "}\n"
            "math_run demo() = ?\n"
            "print (<{(in = up)}>)\n",
            "code9\n",
        )

    def test_record_copy_keeps_text_fields_independent(self) -> None:
        self.assert_parity(
            "record Node { kind : text, value }\n"
            "function demo() -> text\n"
            "{\n"
            'let a = Node("first", 1)\n'
            "let b = a\n"
            'set b.kind = "second"\n'
            "return text_concat(a.kind, b.kind)\n"
            "}\n"
            "math_run demo() = ?\n"
            "print (<{(in = up)}>)\n",
            "firstsecond\n",
        )

    def test_numeric_only_records_still_work(self) -> None:
        self.assert_parity(
            "record Point { x, y }\n"
            "operator + Point = { left.x + right.x, left.y + right.y }\n"
            "function addp(a : Point, b : Point) -> Point = a + b\n"
            "function demo()\n"
            "{\n"
            "let p = Point(1, 2)\n"
            "let q = Point(10, 20)\n"
            "let r = addp(p, q)\n"
            "return r.x + r.y\n"
            "}\n"
            "math_run demo() = ?\n"
            "if up = (ok) goto (push ({r} = {(in = up)}))\n"
            "print (<{r}>)\n",
            "33\n",
        )

    def test_unknown_field_type_is_a_parse_error(self) -> None:
        with self.assertRaises(Exception) as caught:
            run_interpreted("record Bad { name : string }\n")
        self.assertIn("unknown type", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
