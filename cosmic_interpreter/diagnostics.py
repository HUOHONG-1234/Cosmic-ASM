"""rustc 风格的诊断渲染（v9 主线 D）。

Cosmic 的报错一直是单行 ``line 12: message``。这个模块把它渲染成：

    error: unsupported expression syntax
      --> examples/foo.cos:12:14
       |
    12 |     math_run {a} + = ? -int64
       |                  ^ expected an operand after '+'
       |
    help: check the expression inside the braces

列号是「尽力而为」：诊断里带了列号就用，否则从消息里提到的标识符/字面量
推断，再退化到行首第一个非空白字符。永远不猜错方向——猜不到就不画尖号。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# 常见消息 → 可操作建议。只匹配明确的安全模式，避免误导。
_HELP_RULES: tuple[tuple[str, str], ...] = (
    (
        r"expected an operand",
        "check the expression between the braces for a missing value or operator",
    ),
    (
        r"is already declared",
        "use 'set name = ...' to update an existing variable",
    ),
    (
        r"cannot be used as a scalar value",
        "use 'name.field' to read one field of the record",
    ),
    (
        r"function bodies cannot access caller variables",
        "pass the value in as a parameter instead",
    ),
    (
        r"unknown component",
        "available components are: execute, math, atom",
    ),
    (
        r"is used before being imported",
        "add an 'import (component)' statement before using it",
    ),
    (
        r"array argument must be",
        "declare the value with 'array name = array_fill(...)' first",
    ),
    (
        r"expects \d+ argument",
        "check the number of arguments in this call",
    ),
)


@dataclass
class Diagnostic:
    message: str
    line: int = 1
    column: int | None = None
    path: str | None = None
    source: str | None = None
    help: str | None = None
    label: str | None = None
    severity: str = "error"


def _guess_column(message: str, source: str) -> int | None:
    """从消息里提到的引号内容推断列号，猜不到返回 None。"""
    for token in re.findall(r"'([^']+)'", message):
        position = source.find(token)
        if position >= 0:
            return position + 1
    return None


def _default_help(message: str) -> str | None:
    lowered = message.lower()
    for pattern, hint in _HELP_RULES:
        if re.search(pattern, lowered):
            return hint
    return None


def _caret_line(source: str, column: int, width: int) -> str:
    padding = " " * max(column - 1, 0)
    return padding + "^" + "~" * max(width - 1, 0)


def render(diagnostic: Diagnostic) -> str:
    """把一条诊断渲染成 rustc 风格的多行文本（不含结尾换行）。"""
    lines: list[str] = [f"{diagnostic.severity}: {diagnostic.message}"]

    location = ""
    if diagnostic.path:
        location = str(diagnostic.path)
    if diagnostic.line:
        location = f"{location}:{diagnostic.line}" if location else str(diagnostic.line)
    if diagnostic.column:
        location = f"{location}:{diagnostic.column}"
    # 只有知道文件或源码行时才画位置行，凭空出现在 `--> 1` 没有意义。
    if location and (diagnostic.path or diagnostic.source):
        lines.append(f"  --> {location}")

    source = diagnostic.source
    if source:
        source = source.rstrip("\r\n")
        gutter = str(diagnostic.line or 1)
        pad = " " * len(gutter)
        column = diagnostic.column or _guess_column(diagnostic.message, source)
        if column is None:
            stripped = len(source) - len(source.lstrip())
            column = stripped + 1
        column = max(1, min(column, len(source) + 1))
        lines.append(f"{pad} |")
        lines.append(f"{gutter} | {source}")
        caret = _caret_line(source, column, len(diagnostic.label or "") + 1)
        label = diagnostic.label or ""
        lines.append(f"{pad} | {caret}{' ' + label if label else ''}".rstrip())
        lines.append(f"{pad} |")

    hint = diagnostic.help or _default_help(diagnostic.message)
    if hint:
        lines.append(f"help: {hint}")
    return "\n".join(lines)


def from_error(
    error: BaseException,
    *,
    source_text: str | None = None,
    path: str | Path | None = None,
) -> Diagnostic:
    """从异常构造诊断：读取 line/column/message，并附上出错源码行。"""
    line = int(getattr(error, "line", 1) or 1)
    column = getattr(error, "column", None)
    message = str(getattr(error, "message", str(error)))
    source_line = None
    if source_text:
        lines = source_text.splitlines()
        if 1 <= line <= len(lines):
            source_line = lines[line - 1]
    return Diagnostic(
        message=message,
        line=line,
        column=int(column) if column else None,
        path=str(path) if path else None,
        source=source_line,
    )


_LINE_PREFIX_RE = re.compile(r"^line (\d+): (.*)$", re.S)


def format_message(
    message: str, *, source_text: str | None = None, path: str | Path | None = None
) -> str:
    """渲染 ``line 12: ...`` 形式的错误文本；没有行号就退化成单行 ``error: ...``。"""
    match = _LINE_PREFIX_RE.match(message.strip())
    if match is None:
        return f"error: {message}"
    line = int(match.group(1))
    detail = match.group(2)
    source_line = None
    if source_text:
        lines = source_text.splitlines()
        if 1 <= line <= len(lines):
            source_line = lines[line - 1]
    return render(
        Diagnostic(
            message=detail,
            line=line,
            path=str(path) if path else None,
            source=source_line,
        )
    )


def format_error(
    error: BaseException,
    *,
    source_text: str | None = None,
    path: str | Path | None = None,
) -> str:
    return render(from_error(error, source_text=source_text, path=path))
