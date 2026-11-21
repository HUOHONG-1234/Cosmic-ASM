"""Cosmic ASM v8 的 TUI 层：纯 ANSI 渲染 + 平台按键适配。

设计原则：

* **零外部依赖**。只用标准库：``sys`` ``os`` ``ctypes`` ``msvcrt`` ``termios``
  ``tty`` ``unicodedata`` ``shutil`` ``time``。
* **不用 curses**。Windows 版 CPython 没有编进 ``_curses``，``import curses``
  直接 ``ModuleNotFoundError``；装 ``windows-curses`` 会当场破坏零依赖承诺。
* **渲染层是纯 ANSI 转义序列**，一份代码三平台：
  ``ESC[2J`` 清屏、``ESC[H`` 光标回左上、``ESC[<行>;<列>H`` 定位、
  ``ESC[7m``/``ESC[0m`` 反显/复位、``ESC[?1049h``/``ESC[?1049l`` 备用屏进出、
  ``ESC[?25l``/``ESC[?25h`` 光标隐藏/显示。
* **CJK 宽度必须按显示宽度算**。``东`` 占两格，``A`` 占一格，用 ``len()`` 算
  宽度会让中文框歪掉；所以 ``display_width`` / ``fit`` 是全模块唯一的度量口径。

平台分支与限制
--------------

Windows
    * 输出：Windows 10 1511+ 的控制台默认支持 VT 序列；需要显式打开时由
      :func:`enable_virtual_terminal` 用 ``ctypes`` 调 ``kernel32.SetConsoleMode``
      置 ``ENABLE_VIRTUAL_TERMINAL_PROCESSING (0x0004)``。任何失败都静默返回
      ``False``（旧控制台会把转义序列原样打印，但不会崩）。
    * 输入：``msvcrt.kbhit()`` + ``msvcrt.getch()``。方向键是 ``\\x00`` /
      ``\\xe0`` 前缀加 ``H``/``P``/``M``/``K`` 的两次读取，必须自行映射。
      非 ASCII 字符按 ``msvcrt`` 返回的原始字节拼成 UTF-8 再解码。
    * 限制：``msvcrt`` 读到的是控制台输入缓冲区的编码字节，控制台代码页不是
      UTF-8 时中文输入可能降级为 ``?``——这是 ``msvcrt`` 本身的限制。

POSIX（Linux / macOS）
    * 输入：``termios`` + ``tty`` 切 cbreak（保存旧设置，退出恢复），并把
      ``VMIN=0`` ``VTIME=0`` 让 ``os.read`` 变成非阻塞读，因此不需要 ``select``。
    * 输入：转义序列 ``ESC [ A/B/C/D`` 解析为方向键（含 ``ESC O P`` 形式的
      F1），``\\x7f`` 视作 Backspace。

明确不做：鼠标、真彩色主题、窗口管理、行内编辑控件（输入框由上层拼 ``Screen``）。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import unicodedata
from dataclasses import dataclass
from typing import Callable, Optional

__all__ = [
    "Key",
    "KeyReader",
    "Screen",
    "display_width",
    "fit",
    "enable_virtual_terminal",
    "ansi_move",
    "ANSI_RESET",
    "ANSI_REVERSE",
    "ANSI_CLEAR_SCREEN",
    "ANSI_HOME",
    "ANSI_ALT_SCREEN_ON",
    "ANSI_ALT_SCREEN_OFF",
    "ANSI_CURSOR_HIDE",
    "ANSI_CURSOR_SHOW",
]

# ---------------------------------------------------------------- ANSI 常量

ANSI_ESC = "\x1b"
ANSI_CLEAR_SCREEN = "\x1b[2J"      # 清屏
ANSI_HOME = "\x1b[H"               # 光标回左上
ANSI_RESET = "\x1b[0m"             # 复位属性
ANSI_REVERSE = "\x1b[7m"           # 反显
ANSI_CLEAR_EOL = "\x1b[K"          # 清到行尾
ANSI_ALT_SCREEN_ON = "\x1b[?1049h"  # 进备用屏（退出时恢复原屏）
ANSI_ALT_SCREEN_OFF = "\x1b[?1049l"
ANSI_CURSOR_HIDE = "\x1b[?25l"
ANSI_CURSOR_SHOW = "\x1b[?25h"

# ENABLE_VIRTUAL_TERMINAL_PROCESSING
_VT_PROCESSING = 0x0004
# STD_OUTPUT_HANDLE
_STD_OUTPUT_HANDLE = -11


def ansi_move(row: int, col: int) -> str:
    """定位序列 ``ESC[<行>;<列>H``（行、列均从 1 开始）。"""
    return f"\x1b[{max(1, int(row))};{max(1, int(col))}H"


def enable_virtual_terminal() -> bool:
    """打开 Windows 控制台的 VT 序列处理。

    返回是否成功。非 Windows 平台、旧控制台、句柄异常一律返回 ``False``，
    **不抛异常**——TUI 层不允许因为终端能力检测而崩掉。
    """
    try:
        if os.name != "nt":
            return False
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(_STD_OUTPUT_HANDLE)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | _VT_PROCESSING))
    except Exception:  # 静默降级：任何一种失败都只是"没有 VT"
        return False


# ---------------------------------------------------------------- CJK 宽度


def _cell_width(char: str) -> int:
    """单个字符占几格。宽/全角（W/F）算两格，其余算一格。"""
    return 2 if unicodedata.east_asian_width(char) in "WF" else 1


def display_width(text: str) -> int:
    """文本在终端里占多少格。中文/全角字符算 2。"""
    return sum(_cell_width(char) for char in text)


def _clip(text: str, cells: int) -> str:
    """按显示宽度截断，绝不留下半个宽字符。"""
    if cells <= 0:
        return ""
    kept: list[str] = []
    used = 0
    for char in text:
        width = _cell_width(char)
        if used + width > cells:
            break
        kept.append(char)
        used += width
    return "".join(kept)


def fit(text: str, cells: int) -> str:
    """把文本裁到/补到恰好 ``cells`` 格宽：先按显示宽度截断，再用空格补齐。"""
    cells = max(0, int(cells))
    clipped = _clip(text, cells)
    return clipped + " " * (cells - display_width(clipped))


# ---------------------------------------------------------------- 屏幕缓冲


class Screen:
    """二维字符网格。所有写入都按显示宽度进行，宽字符占两格（第二格留空标记）。"""

    def __init__(self, width: Optional[int] = None, height: Optional[int] = None) -> None:
        if width is None or height is None:
            size = shutil.get_terminal_size(fallback=(80, 24))
            width = size.columns if width is None else width
            height = size.lines if height is None else height
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.buffer: list[list[str]] = []
        self.clear()

    # -- 基础 ----------------------------------------------------------

    def clear(self) -> None:
        """整屏填空格。"""
        self.buffer = [[" "] * self.width for _ in range(self.height)]

    def _clear_cell(self, row: int, col: int) -> None:
        """把一个格子恢复成空格，同时修好被它牵连的宽字符另一半。"""
        if not (0 <= row < self.height and 0 <= col < self.width):
            return
        cell = self.buffer[row][col]
        if cell == "":
            # 它是某个宽字符的右半边：把左半边也抹掉
            if col > 0:
                self.buffer[row][col - 1] = " "
        elif _cell_width(cell) == 2 and col + 1 < self.width:
            # 它是宽字符的左半边：右半边也要抹掉
            self.buffer[row][col + 1] = " "
        self.buffer[row][col] = " "

    def put(self, row: int, col: int, text: str) -> None:
        """在 ``(row, col)`` 写入文本；越界部分丢弃，宽字符放不下就整个不写。"""
        if not (0 <= row < self.height) or not text:
            return
        cursor = max(0, int(col))
        for char in text:
            if cursor >= self.width:
                break
            width = _cell_width(char)
            if width == 2:
                if cursor + 1 >= self.width:
                    break  # 只剩一格，宁可留白也不要半个汉字
                self._clear_cell(row, cursor)
                self._clear_cell(row, cursor + 1)
                self.buffer[row][cursor] = char
                self.buffer[row][cursor + 1] = ""
                cursor += 2
            else:
                self._clear_cell(row, cursor)
                self.buffer[row][cursor] = char
                cursor += 1

    def row_text(self, row: int) -> str:
        """取一整行的文本（含行尾空格，显示宽度恒等于 ``width``）。"""
        if not (0 <= row < self.height):
            return ""
        return "".join(self.buffer[row])

    # -- 线条 ----------------------------------------------------------

    def hline(self, row: int, col: int, length: int, char: str = "─") -> None:
        self.put(row, col, char * max(0, int(length)))

    def vline(self, row: int, col: int, length: int, char: str = "│") -> None:
        for offset in range(max(0, int(length))):
            self.put(row + offset, col, char)

    def box(self, x: int, y: int, w: int, h: int, title: Optional[str] = None) -> None:
        """画一个框，``x`` 是列、``y`` 是行。标题按显示宽度居中。"""
        w, h = int(w), int(h)
        if w < 2 or h < 2:
            return
        self.put(y, x, "┌" + "─" * (w - 2) + "┐")
        self.put(y + h - 1, x, "└" + "─" * (w - 2) + "┘")
        for row in range(y + 1, y + h - 1):
            self.put(row, x, "│")
            self.put(row, x + w - 1, "│")
        if title:
            inner = w - 2
            title_width = display_width(title)
            if title_width and title_width + 2 <= inner:
                offset = (inner - title_width) // 2
                self.put(y, x + 1 + offset, fit(title, title_width))

    # -- 输出 ----------------------------------------------------------

    def render(self) -> str:
        """输出完整一帧：``ESC[H`` + 逐行定位/内容/清行。

        每条行内容都裁到 ``width - 1`` 格，避免占满最后一列时终端自动换行。
        """
        parts = [ANSI_HOME]
        limit = self.width - 1
        for index in range(self.height):
            line = self.row_text(index).rstrip()
            if display_width(line) > limit:
                line = _clip(line, limit)
            parts.append(ansi_move(index + 1, 1))
            parts.append(line)
            parts.append(ANSI_CLEAR_EOL)
        return "".join(parts)

    def paint(self, write: Optional[Callable[[str], None]] = None) -> str:
        """渲染并（可选）写出，返回帧字符串。"""
        frame = self.render()
        if write is not None:
            write(frame)
        else:
            try:
                sys.stdout.write(frame)
                sys.stdout.flush()
            except (OSError, ValueError):
                pass
        return frame


# ---------------------------------------------------------------- 输入层


@dataclass(frozen=True)
class Key:
    """上层唯一能看到的按键事件。``name == "char"`` 时 ``text`` 才有内容。"""

    name: str
    text: str = ""


_CHAR_KEYS = {
    "\r": "enter",
    "\n": "enter",
    "\x08": "backspace",
    "\x7f": "backspace",
    "\x1b": "escape",
    "\x03": "ctrl_c",
    "\x0c": "ctrl_l",
    "\x11": "ctrl_q",
}

# Windows 扫描码（\x00 / \xe0 之后的第二个字节）
_WINDOWS_SCAN = {
    "H": "up",
    "P": "down",
    "M": "right",
    "K": "left",
    ";": "f1",
}

# ANSI CSI/SS3 里的最终字节
_ANSI_MAP = {
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "P": "f1",
}

_ANSI_F1_SLOW = {"\x1b[11~": "f1", "\x1b[[A": "f1"}


def _decode_char(text: str) -> Key:
    """单字符 → Key。控制字符映射成功能键，其余当普通字符。"""
    name = _CHAR_KEYS.get(text)
    if name is not None:
        return Key(name)
    return Key("char", text)


def _decode_windows_scan(code: bytes | str) -> Optional[Key]:
    """Windows 前缀之后的第二个字节 → Key（方向键/F1）。"""
    if isinstance(code, bytes):
        text = code.decode("latin-1", "replace")
    else:
        text = code
    name = _WINDOWS_SCAN.get(text)
    return Key(name) if name else None


def _decode_ansi(sequence: bytes | str) -> Key:
    """POSIX 转义序列 → Key。无法识别时退化为 Esc。"""
    text = sequence.decode("latin-1", "replace") if isinstance(sequence, bytes) else sequence
    if text in _ANSI_F1_SLOW:
        return Key(_ANSI_F1_SLOW[text])
    if len(text) >= 2:
        name = _ANSI_MAP.get(text[-1])
        if name and text[-2] in "[O":
            return Key(name)
    return Key("escape")


def _utf8_length(first: int) -> int:
    if first < 0x80:
        return 1
    if first >> 5 == 0b110:
        return 2
    if first >> 4 == 0b1110:
        return 3
    if first >> 3 == 0b11110:
        return 4
    return 1


class KeyReader:
    """薄输入适配：上层只看到 :class:`Key`，不关心平台。

    ``key_source`` 与 ``write`` 都可注入，测试时不需要真实终端：

    >>> reader = KeyReader(key_source=lambda: Key("up"))
    >>> reader.read_key().name
    'up'
    """

    def __init__(
        self,
        key_source: Optional[Callable[[], Optional[Key]]] = None,
        write: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._key_source = key_source
        self._write_impl = write
        self._fd: Optional[int] = None
        self._saved_attrs: Optional[list] = None

    # -- 生命周期 ------------------------------------------------------

    def __enter__(self) -> "KeyReader":
        self.setup()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.restore()
        return False

    def setup(self) -> None:
        """进入交互模式。POSIX 切 cbreak 并保存旧设置；Windows 无需准备。"""
        if os.name == "nt" or self._saved_attrs is not None:
            return
        try:
            import termios
            import tty

            fd = sys.stdin.fileno()
            saved = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            # 关掉阻塞（VMIN=0/VTIME=0），让 os.read 无输入时立刻返回 b""
            current = termios.tcgetattr(fd)
            current[6][termios.VMIN] = 0
            current[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSADRAIN, current)
        except Exception:
            self._fd = -1  # 标成不可用：read_key() 会安全返回 None
            return
        self._fd = fd
        self._saved_attrs = saved

    def restore(self) -> None:
        """恢复终端旧设置。可重复调用。"""
        if self._saved_attrs is None:
            return
        try:
            import termios

            if self._fd is not None and self._fd >= 0:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved_attrs)
        except Exception:
            pass
        finally:
            self._saved_attrs = None
            self._fd = None

    # -- 输出 ----------------------------------------------------------

    def write(self, text: str) -> None:
        """写出一段文本（默认 stdout，可用构造参数 ``write`` 替换）。"""
        if self._write_impl is not None:
            self._write_impl(text)
            return
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except (OSError, ValueError):
            pass

    # -- 读取 ----------------------------------------------------------

    def read_key(self) -> Optional[Key]:
        """非阻塞读一个键；没有输入返回 ``None``。"""
        if self._key_source is not None:
            return self._key_source()
        if os.name == "nt":
            return self._read_windows()
        return self._read_posix()

    def _read_windows(self) -> Optional[Key]:
        try:
            import msvcrt
        except ImportError:
            return None
        try:
            if not msvcrt.kbhit():
                return None
            first = msvcrt.getch()
            if first in (b"\x00", b"\xe0"):
                if not msvcrt.kbhit():
                    return None
                return _decode_windows_scan(msvcrt.getch())
            if first[0] >= 0x80:
                need = max(0, _utf8_length(first[0]) - 1)
                rest = b"".join(msvcrt.getch() for _ in range(need))
                return Key("char", (first + rest).decode("utf-8", "replace"))
            return _decode_char(first.decode("latin-1"))
        except (OSError, ValueError):
            return None

    def _read_posix(self) -> Optional[Key]:
        if self._saved_attrs is None:
            self.setup()
        if self._fd is None or self._fd < 0:
            return None
        try:
            data = os.read(self._fd, 1)
        except OSError:
            return None
        if not data:
            return None
        if data == b"\x1b":
            return _decode_ansi(data + self._read_more(2))
        if data[0] >= 0x80:
            rest = self._read_more(max(0, _utf8_length(data[0]) - 1))
            return Key("char", (data + rest).decode("utf-8", "replace"))
        return _decode_char(data.decode("latin-1"))

    def _read_more(self, count: int) -> bytes:
        """跟着读若干字节（转义序列/多字节字符的剩余部分），有限重试。"""
        chunks: list[bytes] = []
        for _ in range(max(0, count)):
            chunk = self._read_byte_short()
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def _read_byte_short(self) -> bytes:
        if self._fd is None or self._fd < 0:
            return b""
        for _ in range(20):
            try:
                data = os.read(self._fd, 1)
            except OSError:
                return b""
            if data:
                return data
            time.sleep(0.001)
        return b""
