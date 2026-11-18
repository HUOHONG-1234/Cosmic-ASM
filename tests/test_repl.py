from __future__ import annotations

import unittest

from cosmic_interpreter import repl
from cosmic_interpreter.repl import PROMPT, ReplSession, run_repl
from cosmic_interpreter.tui import ANSI_HOME, Key, display_width


def type_text(text: str) -> list[Key]:
    """把字符串拆成逐字符按键。"""
    return [Key("char", char) for char in text]


def make_session(
    keys: list[Key],
    *,
    width: int = 40,
    height: int = 12,
    execute=None,
) -> tuple[ReplSession, list[str]]:
    pending = list(keys)
    sink: list[str] = []
    session = ReplSession(
        execute=execute,
        key_source=lambda: pending.pop(0) if pending else None,
        write=sink.append,
        width=width,
        height=height,
    )
    return session, sink


def last_frame(output: list[str]) -> str:
    """取最后一帧（渲染以 ``ESC[H`` 起始）。"""
    return "".join(output).split(ANSI_HOME)[-1]


class PrintExecutionTests(unittest.TestCase):
    def test_print_output_lands_in_history(self) -> None:
        source = 'print (<{"hi"}>)'
        session, _ = make_session(type_text(source) + [Key("enter")])

        self.assertEqual(session.run(), 0)

        # 回显行里必然含源码，所以断言的是"独立的一行输出"
        self.assertIn("hi", session.output_lines)
        self.assertIn(f"{PROMPT}{source}", session.output_lines)

    def test_run_repl_injects_execute_and_streams_frames(self) -> None:
        pending = list(type_text("abc") + [Key("enter"), Key("ctrl_q")])
        sink: list[str] = []
        code = run_repl(
            stream_out=_Sink(sink),
            key_source=lambda: pending.pop(0) if pending else None,
            execute=lambda source: (f"got:{source}", ""),
        )

        self.assertEqual(code, 0)
        self.assertIn("got:abc", "".join(sink))

    def test_execute_exception_is_shown_not_raised(self) -> None:
        def boom(source: str) -> tuple[str, str]:
            raise ValueError("解析崩了")

        session, _ = make_session(type_text("x") + [Key("enter")], execute=boom)

        self.assertEqual(session.run(), 0)
        self.assertTrue(any("错误:" in line for line in session.output_lines))
        self.assertTrue(any("解析崩了" in line for line in session.output_lines))

    def test_default_executor_reports_parse_error(self) -> None:
        session, _ = make_session(type_text("这 不是 语句") + [Key("enter")])

        self.assertEqual(session.run(), 0)
        self.assertTrue(any("错误:" in line for line in session.output_lines))


class LineEditingTests(unittest.TestCase):
    def _feed(self, keys: list[Key]) -> ReplSession:
        session, _ = make_session(keys)
        for key in keys:
            session.handle_key(key)
        return session

    def test_insert_and_backspace(self) -> None:
        session = self._feed(type_text("abc") + [Key("backspace")])

        self.assertEqual(session.input_buffer, "ab")
        self.assertEqual(session.cursor, 2)

    def test_left_right_cursor_moves(self) -> None:
        keys = type_text("abc") + [Key("left"), Key("left"), Key("char", "X")]
        session = self._feed(keys)

        self.assertEqual(session.input_buffer, "aXbc")
        self.assertEqual(session.cursor, 2)

        session.handle_key(Key("right"))
        self.assertEqual(session.cursor, 3)

    def test_home_and_end(self) -> None:
        session = self._feed(type_text("ab") + [Key("home")])
        self.assertEqual(session.cursor, 0)

        session.handle_key(Key("char", "Z"))
        self.assertEqual(session.input_buffer, "Zab")

        session.handle_key(Key("end"))
        self.assertEqual(session.cursor, 3)
        session.handle_key(Key("char", "!"))
        self.assertEqual(session.input_buffer, "Zab!")

    def test_editing_cjk_keeps_whole_characters(self) -> None:
        session = self._feed(type_text("中文") + [Key("left"), Key("char", "好")])

        self.assertEqual(session.input_buffer, "中好文")
        self.assertEqual(session.cursor, 2)

        session.handle_key(Key("backspace"))
        self.assertEqual(session.input_buffer, "中文")


class HistoryNavigationTests(unittest.TestCase):
    def test_up_down_walks_input_history(self) -> None:
        keys = type_text("first") + [Key("enter")] + type_text("second") + [Key("enter")]
        session, _ = make_session(keys)
        session.run()

        self.assertEqual(session.input_history, ["first", "second"])

        session.handle_key(Key("up"))
        self.assertEqual(session.input_buffer, "second")
        session.handle_key(Key("up"))
        self.assertEqual(session.input_buffer, "first")
        # 到顶后继续 Up 停在第一条
        session.handle_key(Key("up"))
        self.assertEqual(session.input_buffer, "first")

        session.handle_key(Key("down"))
        self.assertEqual(session.input_buffer, "second")
        session.handle_key(Key("down"))
        self.assertEqual(session.input_buffer, "")

    def test_down_restores_the_draft(self) -> None:
        session, _ = make_session(type_text("keep") + [Key("enter")])
        session.run()

        for char in "draft":
            session.handle_key(Key("char", char))
        session.handle_key(Key("up"))
        self.assertEqual(session.input_buffer, "keep")
        session.handle_key(Key("down"))
        self.assertEqual(session.input_buffer, "draft")


class ControlCommandTests(unittest.TestCase):
    def test_ctrl_q_returns_zero(self) -> None:
        code = run_repl(
            stream_out=_Sink([]),
            key_source=lambda: Key("ctrl_q"),
            execute=lambda source: ("", ""),
        )
        self.assertEqual(code, 0)

    def test_ctrl_q_stops_before_later_keys(self) -> None:
        executed: list[str] = []

        def execute(source: str) -> tuple[str, str]:
            executed.append(source)
            return "", ""

        pending = list([Key("ctrl_q")] + type_text("never") + [Key("enter")])
        code = run_repl(
            stream_out=_Sink([]),
            key_source=lambda: pending.pop(0) if pending else Key("ctrl_q"),
            execute=execute,
        )

        self.assertEqual(code, 0)
        self.assertEqual(executed, [])
        # 退出之后不应继续消费按键
        self.assertTrue(pending)

    def test_ctrl_l_clears_history_area(self) -> None:
        session, _ = make_session(type_text('print (<{"a"}>)') + [Key("enter")])
        session.run()
        self.assertTrue(session.output_lines)

        session.handle_key(Key("ctrl_l"))
        self.assertEqual(session.output_lines, [])


class HelpPanelTests(unittest.TestCase):
    def test_f1_shows_help_and_escape_hides_it(self) -> None:
        sink: list[str] = []
        session = ReplSession(
            key_source=lambda: Key("ctrl_q"),
            write=sink.append,
            width=60,
            height=16,
        )

        session.handle_key(Key("f1"))
        session.render()
        showing = last_frame(sink)
        self.assertIn("快捷键", showing)
        self.assertIn("Ctrl+Q", showing)

        session.handle_key(Key("escape"))
        session.render()
        hidden = last_frame(sink)
        self.assertNotIn("快捷键", hidden)
        self.assertIn(PROMPT.rstrip(), hidden)

    def test_help_does_not_break_the_grid(self) -> None:
        session, _ = make_session([], width=31, height=11)
        session.help_visible = True
        session.render()

        for row in range(session.screen.height):
            self.assertEqual(display_width(session.screen.row_text(row)), session.screen.width)


class RenderingTests(unittest.TestCase):
    def test_cjk_input_never_leaves_half_a_character(self) -> None:
        session, _ = make_session(
            type_text("中文输入测试一二三四五六七八九十"), width=20, height=8
        )
        session.handle_key(Key("enter"))
        session.render()

        self.assertEqual(session.screen.width, 20)
        for row in range(session.screen.height):
            text = session.screen.row_text(row)
            self.assertEqual(display_width(text), session.screen.width)
            self.assertNotIn("\ufffd", text)

    def test_long_line_scrolls_instead_of_crashing(self) -> None:
        session, _ = make_session([], width=16, height=6)
        for index in range(40):
            session._append(f"第{index}行输出")

        session.render()

        for row in range(session.screen.height):
            self.assertEqual(display_width(session.screen.row_text(row)), session.screen.width)

    def test_input_scrolls_when_longer_than_the_row(self) -> None:
        session, _ = make_session([], width=16, height=5)
        session._set_input("abcdefghijklmnopqrstuvwxyz")
        session.render()

        row = session.screen.row_text(session.screen.height - 1)
        self.assertEqual(display_width(row), session.screen.width)
        self.assertTrue(row.startswith(PROMPT))
        # 光标所在列必须仍然在屏幕内
        self.assertLess(session._render_input(session.screen.height - 1), session.screen.width)


class DefaultExecutorTests(unittest.TestCase):
    def test_default_executor_runs_real_code(self) -> None:
        output, error = repl.default_execute('print (<{"hi"}>)')
        self.assertEqual(error, "")
        self.assertIn("hi", output)

    def test_default_executor_returns_error_text(self) -> None:
        output, error = repl.default_execute('print (<{"x"}')
        self.assertEqual(output, "")
        self.assertTrue(error)


class _Sink:
    """带 ``write`` 的最小流对象，模拟 ``sys.stdout``。"""

    def __init__(self, store: list[str]) -> None:
        self.store = store

    def write(self, text: str) -> int:
        self.store.append(text)
        return len(text)


if __name__ == "__main__":
    unittest.main()
