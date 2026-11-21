"""Guard: user-facing CLI text must be English.

The Cosmic ASM *program* targets an international audience, so every message a user
can see (exceptions, printed diagnostics, help text) must be plain English. Chinese
remains fully supported in the language itself (HOP verbs, source code) and in the
documentation; only the program's own UI is constrained here.

This test scans .py sources for string literals that contain CJK characters and are
handed to `raise`/`print`/`sys.stderr`, ignoring comments and docstrings.
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "cosmic_interpreter"

CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _allowed_cjk_literals() -> set[str]:
    """Chinese HOP verbs/keywords are language surface, not program UI."""
    from cosmic_interpreter import hop

    allowed = set(hop._VERBS)
    allowed.update({"存", "里的", "为", "到", "用", "把"})
    return allowed


def _string_literals(path: Path) -> list[tuple[int, str]]:
    """Return (line, value) for every real string literal, skipping docstrings.

    巨型模板字面量（例如生成的 C 运行时源码）按长度排除：里面的中文是 C 注释，
    属于「生成的源码」而不是程序 UI 文案。
    """
    found: list[tuple[int, str]] = []
    source = path.read_text(encoding="utf-8")
    doc_positions = {
        (node.lineno, node.col_offset)
        for node in _docstring_nodes(source, path)
    }
    with path.open("rb") as handle:
        try:
            tokens = list(tokenize.tokenize(handle.readline))
        except (tokenize.TokenError, IndentationError, SyntaxError):  # pragma: no cover
            return found
    for token in tokens:
        if token.type != tokenize.STRING:
            continue
        if (token.start[0], token.start[1]) in doc_positions:
            continue
        try:
            value = ast.literal_eval(token.string)
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, str) and len(value) <= 1500:
            found.append((token.start[0], value))
    return found


def _docstring_nodes(source: str, path: Path):
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover
        return []
    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                nodes.append(body[0].value)
    return nodes


class EnglishUiTests(unittest.TestCase):
    def test_user_facing_strings_are_english(self):
        allowed = _allowed_cjk_literals()
        offenders: list[str] = []
        for path in sorted(PACKAGE_ROOT.glob("*.py")):
            for line, value in _string_literals(path):
                if CJK.search(value) and value.strip() not in allowed:
                    offenders.append(f"{path.name}:{line}: {value[:60]}")
        self.assertEqual(
            offenders,
            [],
            "user-visible strings must be English:\n" + "\n".join(offenders),
        )

    def test_cli_help_and_errors_are_english(self):
        from cosmic_interpreter import cosmic_cli

        buffer = io.StringIO()
        original = sys.stdout
        sys.stdout = buffer
        try:
            cosmic_cli.main(["--help"])
        finally:
            sys.stdout = original
        self.assertFalse(CJK.search(buffer.getvalue()))

        stderr = io.StringIO()
        original_err = sys.stderr
        sys.stderr = stderr
        try:
            code = cosmic_cli.main(["definitely-not-a-command"])
        finally:
            sys.stderr = original_err
        self.assertEqual(code, 2)
        self.assertIn("unknown command", stderr.getvalue())
        self.assertFalse(CJK.search(stderr.getvalue()))


if __name__ == "__main__":
    unittest.main()
