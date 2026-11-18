from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from cosmic_interpreter import hop
from cosmic_interpreter.parser import ParseError, parse
from cosmic_interpreter.runtime import Interpreter, RuntimeError


class HopRewriteTests(unittest.TestCase):
    def test_sources_without_hop_verbs_are_untouched(self):
        source = '#%math = int64\nmath_run 1 + 1 = ? -int64\nprint (<{up}>)\n'
        rewritten, line_map = hop.rewrite(source)
        self.assertEqual(rewritten, source)
        self.assertIsNone(line_map)

    def test_text_verb_expands_to_existing_statements(self):
        rewritten, line_map = hop.rewrite('行数 文本 存 总行\n')
        self.assertIn("math_run line_count({", rewritten)
        self.assertIn("if up = (ok) goto (push ({", rewritten)
        self.assertIn("= {(in = up)}))", rewritten)
        self.assertEqual(len(line_map), len(rewritten.splitlines()))

    def test_particles_are_ignored(self):
        rewritten, _ = hop.rewrite('替换文本 文本 里的 "旧" 为 "新" 存 结果\n')
        self.assertIn("replace({zh_", rewritten)
        self.assertIn('"旧", "新"', rewritten)
        self.assertNotIn("里的", rewritten)
        self.assertNotIn("为", rewritten)

    def test_array_verbs_use_bare_names_for_arrays(self):
        rewritten, _ = hop.rewrite("数组求和 数据 存 总量\n追加元素 数据 5 存 更多\n")
        self.assertIn("math_run array_sum(zh_", rewritten)
        self.assertNotIn("array_sum({", rewritten)
        self.assertIn("= array_push(zh_", rewritten)
        self.assertIn("array ", rewritten)

    def test_math_verbs_wrap_variables(self):
        rewritten, _ = hop.rewrite("平方根 数值 存 根\n幂 底 指数 存 结果\n")
        self.assertIn("math_run sqrt({zh_", rewritten)
        self.assertIn("math_run pow({zh_", rewritten)

    def test_consecutive_steps_get_require_ok_guards(self):
        rewritten, _ = hop.rewrite("行数 文本 存 甲\n行数 文本 存 乙\n行数 文本 存 丙\n")
        self.assertIn("require_ok 2", rewritten)
        self.assertIn("require_ok 3", rewritten)
        self.assertEqual(rewritten.count("require_ok"), 2)

    def test_first_step_has_no_guard(self):
        rewritten, _ = hop.rewrite("行数 文本 存 甲\n")
        self.assertNotIn("require_ok", rewritten)

    def test_string_literals_are_not_mangled(self):
        source = '打开 "文本.txt" 存 文本\n'
        rewritten, _ = hop.rewrite(source)
        self.assertIn('"文本.txt"', rewritten)

    def test_chinese_names_are_mangled_consistently(self):
        source = "行数 文本 存 总行\nprint (<{总行}>)\n"
        rewritten, _ = hop.rewrite(source)
        self.assertNotIn("文本", rewritten)
        self.assertNotIn("总行", rewritten)
        self.assertIn("zh_", rewritten)
        # 同一个中文名在任何位置都必须映射到同一个内部名
        import re

        ids = re.findall(r"zh_[0-9a-f]{10}", rewritten)
        distinct = set(ids)
        self.assertEqual(len(distinct), 2)  # 文本 与 总行 各一个内部名
        # 总行 被写入一次、读取一次，必须使用同一个内部名
        self.assertEqual(len([name for name in distinct if ids.count(name) == 2]), 1)

    def test_missing_target_keyword_is_a_parse_error(self):
        with self.assertRaisesRegex(ParseError, "存"):
            parse("行数 文本\n")

    def test_wrong_argument_count_is_a_parse_error(self):
        with self.assertRaisesRegex(ParseError, "需要 1 个参数"):
            parse("行数 文本 多余 存 结果\n")

    def test_unknown_verb_passes_through(self):
        source = "这不是动词 参数\n"
        rewritten, line_map = hop.rewrite(source)
        self.assertEqual(rewritten, source)
        self.assertIsNone(line_map)


class HopExecutionTests(unittest.TestCase):
    def run_source(self, source: str) -> tuple[Interpreter, str]:
        output = io.StringIO()
        interpreter = Interpreter(output=output)
        interpreter.run(parse(source))
        return interpreter, output.getvalue()

    def test_text_pipeline_runs_in_interpreter(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "log.txt"
            log.write_bytes("第一行\n第二行\n第三行".encode("utf-8"))
            source = (
                "#%math = int64\n"
                f'打开 "{log.as_posix()}" 存 文本\n'
                "行数 文本 存 总行\n"
                '查找位置 文本 "第二行" 存 位置\n'
                '替换文本 文本 里的 "第二行" 为 "SECOND" 存 结果\n'
                "print (<{总行}>)\n"
                "print (<{位置}>)\n"
                "print (<{结果}>)\n"
            )
            _interpreter, output = self.run_source(source)
        lines = output.splitlines()
        self.assertEqual(lines[0], "3")
        self.assertEqual(lines[1], "10")
        self.assertEqual(lines[2], "第一行")
        self.assertEqual(lines[3], "SECOND")
        self.assertEqual(lines[4], "第三行")

    def test_math_and_array_verbs_run(self):
        source = (
            "#%math = int64\n"
            "array 数据 = [1, 2, 3]\n"
            "数组求和 数据 存 总量\n"
            "数组长度 数据 存 个数\n"
            "填充 4 7 存 满\n"
            "数组求和 满 存 满和\n"
            "print (<{总量}>)\n"
            "print (<{个数}>)\n"
            "print (<{满和}>)\n"
        )
        _interpreter, output = self.run_source(source)
        self.assertEqual(output.splitlines(), ["6", "3", "28"])

    def test_error_reports_original_line_and_name(self):
        source = "#%math = int64\n\n\n行数 文本 存 结果\n"
        with self.assertRaises(RuntimeError) as caught:
            self.run_source(source)
        message = hop.humanize(caught.exception.message)
        self.assertEqual(caught.exception.line, 4)
        self.assertIn("文本", message)
        self.assertNotIn("zh_", message)

    def test_failed_step_stops_the_pipeline(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.txt"
            source = (
                f'打开 "{missing.as_posix()}" 存 文本\n'
                "行数 文本 存 总行\n"
                'print (<{"不该执行"}>)\n'
            )
            with self.assertRaises((RuntimeError, ParseError)) as caught:
                self.run_source(source)
        self.assertNotIn("不该执行", str(caught.exception))

    def test_require_ok_stops_after_error_status(self):
        source = 'ask ({x})\nrequire_ok 2\nprint (<{"不该执行"}>)\n'
        output = io.StringIO()
        with self.assertRaisesRegex(RuntimeError, "HOP"):
            Interpreter(output=output, no_input=True).run(parse(source))
        self.assertEqual(output.getvalue(), "")

    def test_require_ok_is_a_noop_when_previous_step_succeeded(self):
        _interpreter, output = self.run_source('push ({a} = {"ok"})\nrequire_ok 1\nprint (<{a}>)\n')
        self.assertEqual(output.strip(), "ok")


class HopBackendParityTests(unittest.TestCase):
    def test_hop_program_compiles_to_identical_output(self):
        from cosmic_interpreter.compiler import compile_source

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            log = directory / "log.txt"
            log.write_bytes("a\nb\nc".encode("utf-8"))
            source_path = directory / "hop.cos"
            source_path.write_text(
                "#%math = int64\n"
                f'打开 "{log.as_posix()}" 存 文本\n'
                "行数 文本 存 总行\n"
                "平方根 16 存 根\n"
                "print (<{总行}>)\n"
                "print (<{根}>)\n",
                encoding="utf-8",
            )
            executable = directory / "hop.exe"
            _c_path, output_path = compile_source(source_path, output_path=executable)
            import subprocess

            completed = subprocess.run(
                [str(output_path)], capture_output=True, timeout=60, check=False
            )
            interpreter_output = io.StringIO()
            Interpreter(output=interpreter_output).run(
                parse(source_path.read_text(encoding="utf-8"))
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(
            completed.stdout.decode(errors="replace").replace("\r\n", "\n"),
            interpreter_output.getvalue(),
        )
        self.assertEqual(interpreter_output.getvalue().splitlines(), ["3", "4"])


if __name__ == "__main__":
    unittest.main()
