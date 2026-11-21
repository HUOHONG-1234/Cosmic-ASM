"""Cosmic ASM v8 的 TUI REPL。

设计约束（配合 :mod:`cosmic_interpreter.tui`）：

* **零第三方依赖**。只用标准库。
* **不碰真实终端**。渲染全部走 :meth:`Screen.paint`，输入全部走
  :meth:`KeyReader.read_key`；测试只要注入 ``key_source`` / ``write`` 即可，
  不需要伪终端。
* **按显示宽度排版**。中文字符占两格，光标列与换行都用
  :func:`tui.display_width` 计算，绝不把宽字符劈成两半。
* **单条语句失败不崩 REPL**。执行器抛出的异常会被兜住，转成历史区里的一行
  错误提示，并经过 :func:`hop.humanize` 把内部标识符还原成人能读的名字。

布局（高度允许时）::

    ┌─────────────────────────────┐
    │ 历史输出区（自动滚动）        │  rows 0 .. height-3
    ├─────────────────────────────┤  row  height-2
    │cosmic> 输入行                │  row  height-1
    └─────────────────────────────┘

按 ``F1`` 时，历史区被帮助面板覆盖，输入行仍然保留在最下面。
"""

from __future__ import annotations

import io
import sys
import time
from typing import Callable, Optional, TextIO

from . import hop
from .parser import ParseError, parse
from .runtime import ExitRequest, Interpreter, RuntimeError as CosmicRuntimeError
from .tui import (
    ANSI_CURSOR_SHOW,
    Key,
    KeyReader,
    Screen,
    ansi_move,
    display_width,
    enable_virtual_terminal,
    fit,
)

__all__ = ["PROMPT", "ReplSession", "run_repl"]

PROMPT = "cosmic> "

# 历史输出区最多保留多少行（超出后丢最旧的）。
HISTORY_LIMIT = 500

# 无输入时的轮询间隔（秒），只在真实终端下使用。
_POLL_INTERVAL = 0.02

ExecuteFn = Callable[[str], "tuple[str, str]"]
KeySource = Callable[[], Optional[Key]]


# ---------------------------------------------------------------- 执行器


def _format_error(error: BaseException) -> str:
    """把异常整理成一行可读文本，并还原 HOP 的内部标识符。"""
    message = hop.humanize(str(getattr(error, "message", str(error))))
    line = getattr(error, "line", None)
    if isinstance(line, int):
        return f"line {line}: {message}"
    return message


def default_execute(source: str) -> tuple[str, str]:
    """默认执行器：``parse()`` + ``Interpreter(output=StringIO)``。

    返回 ``(输出文本, 错误文本)``。解析/运行时错误不抛出，转成错误文本；
    这样 REPL 只负责展示，不负责分辨异常类型。
    """
    output = io.StringIO()
    errors = io.StringIO()
    try:
        program = parse(source)
        Interpreter(output=output, error_output=errors, no_input=True).run(program)
    except ExitRequest:
        # REPL 里 exit 不结束会话，只结束这一条语句。
        pass
    except (ParseError, CosmicRuntimeError) as error:
        return output.getvalue(), _format_error(error)
    except Exception as error:  # 兜底：执行器自身的意外也不许拖垮 REPL
        return output.getvalue(), hop.humanize(str(error))
    return output.getvalue(), hop.humanize(errors.getvalue())


# ---------------------------------------------------------------- 排版工具


def _wrap(text: str, cells: int) -> list[str]:
    """按显示宽度折行。宽字符整体移动到下一行，绝不拆开。"""
    if cells <= 0:
        return [text]
    if not text:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    used = 0
    for char in text:
        width = display_width(char)
        if current and used + width > cells:
            lines.append("".join(current))
            current = []
            used = 0
        if width > cells:
            # 单字符比整行还宽：丢弃，避免半字。
            continue
        current.append(char)
        used += width
    lines.append("".join(current))
    return lines


def _slice_from(text: str, cells: int) -> str:
    """丢掉左侧 ``cells`` 格宽的内容（用于输入行水平滚动）。"""
    if cells <= 0:
        return text
    used = 0
    for index, char in enumerate(text):
        width = display_width(char)
        if used + width > cells:
            return text[index:]
        used += width
        if used == cells:
            return text[index + 1 :]
    return ""


# ---------------------------------------------------------------- 帮助面板

_HELP_TITLE = "Help - Cosmic ASM v8 REPL"

_HELP_LINES: tuple[str, ...] = (
    "Key bindings",
    "  F1         open/close this help",
    "  Esc        close help",
    "  Enter      run the current input",
    "  Left/Right move the cursor",
    "  Home/End   jump to start/end of line",
    "  Backspace  delete the character before the cursor",
    "  Up/Down    browse input history",
    "  Ctrl+L     clear the screen",
    "  Ctrl+Q     quit the REPL",
    "",
    "Examples",
    '  print (<{"hello, cosmic"}>)',
    "  math_run 1 + 1 = ? -int64",
    "  array_push({a}, 5)",
)


# ---------------------------------------------------------------- 会话


class ReplSession:
    """一个 REPL 会话实例。

    把状态和行为放进类里，测试就能直接检查 ``input_buffer`` / ``screen``，
    而不用去解析 ANSI 帧；:func:`run_repl` 只是它的一层薄包装。
    """

    def __init__(
        self,
        *,
        execute: Optional[ExecuteFn] = None,
        key_source: Optional[KeySource] = None,
        write: Optional[Callable[[str], None]] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        history_limit: int = HISTORY_LIMIT,
    ) -> None:
        self.screen = Screen(width, height)
        self.execute: ExecuteFn = execute or default_execute
        self.reader = KeyReader(key_source=key_source, write=write)
        self._write = write
        # 注入了 key_source 就说明没有真实终端：不要 setup（POSIX 下会改终端模式）。
        self._injected = key_source is not None

        self.output_lines: list[str] = []
        self.input_history: list[str] = []
        self.input_buffer = ""
        self.cursor = 0
        self.help_visible = False

        self._history_index: Optional[int] = None
        self._draft = ""
        self._history_limit = max(1, int(history_limit))

        self._append("Cosmic ASM v8 REPL")
        self._append("Type a statement and press Enter; F1 for help, Ctrl+Q to quit.")
        self._append("")

    # -- 生命周期 ------------------------------------------------------

    def run(self) -> int:
        """主循环。``Ctrl+Q`` 或被注入的按键序列耗尽时返回 0。"""
        self.render()
        try:
            if self._injected:
                return self._loop()
            with self.reader:
                return self._loop()
        finally:
            self._close()

    def _loop(self) -> int:
        while True:
            key = self.reader.read_key()
            if key is None:
                if self._injected:
                    return 0  # 测试序列走完：正常收尾
                time.sleep(_POLL_INTERVAL)
                continue
            exit_code = self.handle_key(key)
            if exit_code is not None:
                return exit_code
            self.render()

    def _close(self) -> None:
        try:
            self.reader.write(ANSI_CURSOR_SHOW + "\r\n")
        except Exception:
            pass

    # -- 按键 ----------------------------------------------------------

    def handle_key(self, key: Key) -> Optional[int]:
        """处理一个按键。返回非 ``None`` 表示要退出（值为退出码）。"""
        name = key.name
        if name == "char":
            if key.text and key.text.isprintable():
                self._insert(key.text)
            return None
        if name == "enter":
            self._submit()
            return None
        if name == "backspace":
            self._backspace()
            return None
        if name == "delete":
            self._delete()
            return None
        if name == "left":
            self.cursor = max(0, self.cursor - 1)
            return None
        if name == "right":
            self.cursor = min(len(self.input_buffer), self.cursor + 1)
            return None
        if name == "home":
            self.cursor = 0
            return None
        if name == "end":
            self.cursor = len(self.input_buffer)
            return None
        if name == "up":
            self._history_prev()
            return None
        if name == "down":
            self._history_next()
            return None
        if name == "f1":
            self.help_visible = True
            return None
        if name == "escape":
            self.help_visible = False
            return None
        if name == "ctrl_l":
            self.output_lines.clear()
            self._history_index = None
            return None
        if name == "ctrl_c":
            self._set_input("")
            return None
        if name == "ctrl_q":
            return 0
        return None

    def _insert(self, text: str) -> None:
        self.input_buffer = (
            self.input_buffer[: self.cursor] + text + self.input_buffer[self.cursor :]
        )
        self.cursor += len(text)

    def _backspace(self) -> None:
        if self.cursor <= 0:
            return
        self.input_buffer = (
            self.input_buffer[: self.cursor - 1] + self.input_buffer[self.cursor :]
        )
        self.cursor -= 1

    def _delete(self) -> None:
        if self.cursor >= len(self.input_buffer):
            return
        self.input_buffer = (
            self.input_buffer[: self.cursor] + self.input_buffer[self.cursor + 1 :]
        )

    def _set_input(self, text: str) -> None:
        self.input_buffer = text
        self.cursor = len(text)

    # -- 历史 ----------------------------------------------------------

    def _history_prev(self) -> None:
        if not self.input_history:
            return
        if self._history_index is None:
            self._draft = self.input_buffer
            self._history_index = len(self.input_history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        else:
            return
        self._set_input(self.input_history[self._history_index])

    def _history_next(self) -> None:
        if self._history_index is None:
            return
        if self._history_index + 1 < len(self.input_history):
            self._history_index += 1
            self._set_input(self.input_history[self._history_index])
        else:
            self._history_index = None
            self._set_input(self._draft)

    # -- 执行 ----------------------------------------------------------

    def _submit(self) -> None:
        source = self.input_buffer
        self._set_input("")
        self._history_index = None
        self._draft = ""
        if not source.strip():
            return
        self.input_history.append(source)
        self._append(f"{PROMPT}{source}")

        output, error = self._run(source)
        for line in output.splitlines():
            self._append(line)
        for index, line in enumerate(error.splitlines()):
            self._append(("error: " + line) if index == 0 else "  " + line)

    def _run(self, source: str) -> tuple[str, str]:
        try:
            result = self.execute(source)
        except Exception as error:  # 单条语句的意外不能终止会话
            return "", hop.humanize(_format_error(error))
        try:
            output, error = result
        except (TypeError, ValueError):
            return str(result), ""
        return str(output or ""), str(error or "")

    def _append(self, line: str) -> None:
        self.output_lines.append(line)
        overflow = len(self.output_lines) - self._history_limit
        if overflow > 0:
            del self.output_lines[:overflow]

    # -- 渲染 ----------------------------------------------------------

    def render(self) -> str:
        """画一帧并（通过注入的 write）输出，返回帧字符串。"""
        screen = self.screen
        width, height = screen.width, screen.height
        screen.clear()

        if height >= 3:
            output_rows = height - 2
            separator_row: Optional[int] = height - 2
        else:
            output_rows = 0
            separator_row = None
        input_row = height - 1

        if self.help_visible:
            self._render_help()
        else:
            self._render_history(output_rows)
            if separator_row is not None:
                screen.hline(separator_row, 0, width)

        cursor_col = self._render_input(input_row)

        frame = screen.paint(self._write)
        try:
            self.reader.write(ansi_move(input_row + 1, cursor_col + 1))
        except Exception:
            pass
        return frame

    def _render_history(self, rows: int) -> None:
        if rows <= 0:
            return
        width = self.screen.width
        visible: list[str] = []
        for line in reversed(self.output_lines):
            chunks = _wrap(line, width)
            visible[:0] = chunks
            if len(visible) >= rows:
                break
        tail = visible[-rows:]
        start = rows - len(tail)
        for offset, line in enumerate(tail):
            self.screen.put(start + offset, 0, fit(line, width))

    def _render_help(self) -> None:
        screen = self.screen
        height = screen.height
        box_height = height - 1 if height >= 3 else height
        screen.box(0, 0, screen.width, box_height, title=_HELP_TITLE)
        row = 1
        for line in _HELP_LINES:
            if row >= box_height - 1:
                break
            screen.put(row, 2, fit(line, max(0, screen.width - 4)))
            row += 1

    def _render_input(self, row: int) -> int:
        """画输入行，返回光标所在的 0 基列。"""
        screen = self.screen
        width = screen.width
        prompt_width = display_width(PROMPT)
        avail = max(0, width - prompt_width)
        cursor_cells = display_width(self.input_buffer[: self.cursor])
        if cursor_cells >= avail:
            start = cursor_cells - avail + 1
        else:
            start = 0
        visible = _slice_from(self.input_buffer, start)
        line = PROMPT + fit(visible, avail)
        screen.put(row, 0, fit(line, width))
        cursor_col = prompt_width + (cursor_cells - start)
        return max(0, min(width - 1, cursor_col))


# ---------------------------------------------------------------- 入口


def run_repl(
    stream_in: Optional[TextIO] = None,
    stream_out: Optional[TextIO] = None,
    key_source: Optional[KeySource] = None,
    *,
    execute: Optional[ExecuteFn] = None,
) -> int:
    """启动 TUI REPL，返回退出码（正常退出为 0）。

    可注入点：

    * ``key_source`` —— ``Callable[[], Key | None]``，逐键驱动，测试不碰终端；
      返回 ``None`` 表示没有键（注入模式下即视为输入结束）。
    * ``execute`` —— ``Callable[[str], tuple[str, str]]``，把源码变成
      ``(输出文本, 错误文本)``；默认用 ``parse()`` + ``Interpreter``。
    * ``stream_out`` —— 帧输出目标，默认 ``sys.stdout``。

    ``stream_in`` 为调用方保持签名对称保留；真实终端的按键始终由
    :class:`KeyReader` 读取（它自己处理平台差异）。
    """
    del stream_in  # 真实输入由 KeyReader 负责，这里仅保留参数对称性
    out = stream_out if stream_out is not None else sys.stdout
    # 容错：传进来的对象不可写就退回默认输出，别让 REPL 起不来。
    write = getattr(out, "write", None)
    session = ReplSession(execute=execute, key_source=key_source, write=write)
    return session.run()


if __name__ == "__main__":
    enable_virtual_terminal()
    raise SystemExit(run_repl())
