from __future__ import annotations

import os
import re
import unittest

from cosmic_interpreter.tui import (
    ANSI_HOME,
    Key,
    KeyReader,
    Screen,
    _decode_ansi,
    _decode_char,
    _decode_windows_scan,
    display_width,
    enable_virtual_terminal,
    fit,
)


class WidthTests(unittest.TestCase):
    def test_display_width_counts_cjk_as_two_cells(self) -> None:
        self.assertEqual(display_width("东A"), 3)
        self.assertEqual(display_width("cosmic"), 6)
        self.assertEqual(display_width(""), 0)
        self.assertEqual(display_width("标题"), 4)

    def test_fit_truncates_by_display_width(self) -> None:
        self.assertEqual(fit("cosmic", 3), "cos")
        self.assertEqual(fit("cosmic", 6), "cosmic")
        self.assertEqual(fit("", 2), "  ")

    def test_fit_never_leaves_half_a_wide_char(self) -> None:
        self.assertEqual(fit("东A", 1), " ")
        self.assertEqual(fit("东A", 2), "东")
        self.assertEqual(fit("东A", 3), "东A")
        self.assertEqual(fit("东A", 4), "东A ")
        for cells in range(0, 8):
            self.assertEqual(display_width(fit("a东b西c", cells)), cells)


class ScreenBoxTests(unittest.TestCase):
    def test_box_rows_align_by_display_width(self) -> None:
        screen = Screen(30, 10)
        screen.box(1, 1, 20, 6, title="东A标题")
        left_edge = 1
        right_edge = 1 + 20 - 1

        top = screen.row_text(1)
        self.assertEqual(top.index("┌"), left_edge)
        self.assertEqual(display_width(top[: top.rindex("┐")]), right_edge)
        self.assertIn("东A标题", top)

        bottom = screen.row_text(6)
        self.assertEqual(bottom.index("└"), left_edge)
        self.assertEqual(display_width(bottom[: bottom.rindex("┘")]), right_edge)

        for row in range(2, 6):
            text = screen.row_text(row)
            self.assertEqual(text.index("│"), left_edge)
            # 右边框的显示列必须在每一行都一样——这就是"中文框不歪"
            self.assertEqual(display_width(text[: text.rindex("│")]), right_edge)

    def test_every_row_keeps_grid_width(self) -> None:
        screen = Screen(24, 6)
        screen.box(0, 0, 24, 6, title="中文标题测试")
        for row in range(screen.height):
            self.assertEqual(display_width(screen.row_text(row)), screen.width)

    def test_put_drops_wide_char_that_does_not_fit(self) -> None:
        screen = Screen(10, 2)
        screen.put(0, 9, "中")
        self.assertEqual(screen.row_text(0), " " * 10)

    def test_overwriting_half_of_a_wide_char_repairs_the_row(self) -> None:
        screen = Screen(10, 2)
        screen.put(0, 0, "中文")
        screen.put(0, 1, "X")  # 压在"中"的右半边上
        self.assertEqual(screen.row_text(0)[:3], " X文")
        self.assertEqual(display_width(screen.row_text(0)), 10)


class RenderTests(unittest.TestCase):
    def test_render_emits_home_and_no_nul(self) -> None:
        screen = Screen(12, 3)
        screen.put(0, 0, "东A")
        frame = screen.render()
        self.assertTrue(frame.startswith(ANSI_HOME))
        self.assertNotIn("\x00", frame)
        self.assertIn("\x1b[2;1H", frame)

    def test_render_lines_never_reach_last_column(self) -> None:
        screen = Screen(10, 2)
        screen.put(0, 0, "abcdefghijkl")
        frame = screen.render()
        chunks = re.split(r"\x1b\[\d+;1H", frame)[1:]
        self.assertEqual(len(chunks), screen.height)
        for chunk in chunks:
            body = chunk.split("\x1b[K", 1)[0]
            self.assertLessEqual(display_width(body), screen.width - 1)


class KeyDecodingTests(unittest.TestCase):
    def test_ansi_arrows_and_f1(self) -> None:
        self.assertEqual(_decode_ansi(b"\x1b[A").name, "up")
        self.assertEqual(_decode_ansi(b"\x1b[B").name, "down")
        self.assertEqual(_decode_ansi(b"\x1b[C").name, "right")
        self.assertEqual(_decode_ansi(b"\x1b[D").name, "left")
        self.assertEqual(_decode_ansi(b"\x1bOP").name, "f1")
        self.assertEqual(_decode_ansi(b"\x1b[11~").name, "f1")
        self.assertEqual(_decode_ansi(b"\x1b").name, "escape")

    def test_windows_scan_codes(self) -> None:
        self.assertEqual(_decode_windows_scan(b"H").name, "up")
        self.assertEqual(_decode_windows_scan(b"P").name, "down")
        self.assertEqual(_decode_windows_scan(b"M").name, "right")
        self.assertEqual(_decode_windows_scan(b"K").name, "left")
        self.assertEqual(_decode_windows_scan(b";").name, "f1")
        self.assertIsNone(_decode_windows_scan(b"X"))

    def test_control_characters(self) -> None:
        self.assertEqual(_decode_char("\r").name, "enter")
        self.assertEqual(_decode_char("\x7f").name, "backspace")
        self.assertEqual(_decode_char("\x08").name, "backspace")
        self.assertEqual(_decode_char("\x03").name, "ctrl_c")
        self.assertEqual(_decode_char("\x11").name, "ctrl_q")
        self.assertEqual(_decode_char("\x0c").name, "ctrl_l")
        self.assertEqual(_decode_char("\x1b").name, "escape")

    def test_printable_char_carries_text(self) -> None:
        key = _decode_char("东")
        self.assertEqual(key.name, "char")
        self.assertEqual(key.text, "东")


class KeyReaderTests(unittest.TestCase):
    def test_injected_source_drives_read_key(self) -> None:
        sequence = [
            Key("up"),
            Key("down"),
            Key("left"),
            Key("right"),
            Key("enter"),
            Key("backspace"),
            Key("ctrl_q"),
            Key("ctrl_l"),
            Key("f1"),
            Key("char", "东"),
        ]
        pending = list(sequence)
        reader = KeyReader(key_source=lambda: pending.pop(0) if pending else None)

        read = [reader.read_key() for _ in range(len(sequence))]
        self.assertEqual([key.name for key in read], [key.name for key in sequence])
        self.assertEqual(read[-1].text, "东")
        self.assertIsNone(reader.read_key())

    def test_injected_write_is_used(self) -> None:
        sink: list[str] = []
        reader = KeyReader(key_source=lambda: None, write=sink.append)
        reader.write("东")
        self.assertEqual(sink, ["东"])

    def test_reader_is_usable_without_a_terminal(self) -> None:
        reader = KeyReader(key_source=lambda: Key("enter"))
        with reader as active:
            self.assertEqual(active.read_key().name, "enter")
        # 注入模式下不碰真实终端，恢复过程也必须无副作用
        reader.restore()


class PlatformTests(unittest.TestCase):
    def test_enable_virtual_terminal_never_raises(self) -> None:
        result = enable_virtual_terminal()
        self.assertIsInstance(result, bool)
        if os.name != "nt":
            self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
