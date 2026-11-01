from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .parser import ParseError, parse
from .runtime import ExitRequest, Interpreter, RuntimeError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a Cosmic ASM source file.")
    parser.add_argument("source", type=Path, help="path to a .cos or text source file")
    parser.add_argument(
        "--no-external-commands",
        action="store_true",
        help="reject bash command statements instead of running them",
    )
    parser.add_argument(
        "--max-loop-iterations",
        type=int,
        help="optional safety limit for each start(loop) or while block",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="only parse and validate the file, never execute it",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="with --check, report diagnostics as JSON on stdout",
    )
    args = parser.parse_args(argv)
    if args.max_loop_iterations is not None and args.max_loop_iterations < 1:
        parser.error("--max-loop-iterations must be greater than zero")

    diagnostics: list[dict[str, object]] = []
    try:
        source = args.source.read_text(encoding="utf-8-sig")
        program = parse(source)
        if args.check:
            _static_check(program, diagnostics)
        else:
            try:
                Interpreter(
                    allow_external_commands=not args.no_external_commands,
                    max_loop_iterations=args.max_loop_iterations,
                ).run(program)
            except ExitRequest as request:
                return request.code
    except (OSError, UnicodeError, ParseError, RuntimeError) as error:
        line = getattr(error, "line", 1)
        message = getattr(error, "message", str(error))
        diagnostics.append({"line": line, "message": message, "severity": "error"})

    if args.check:
        if args.json:
            print(json.dumps({"diagnostics": diagnostics}, ensure_ascii=False))
        else:
            for item in diagnostics:
                print(
                    f"line {item['line']}: {item['message']}",
                    file=sys.stderr,
                )
        return 1 if diagnostics else 0

    if diagnostics:
        print(f"line {diagnostics[0]['line']}: {diagnostics[0]['message']}", file=sys.stderr)
        return 1
    return 0


def _static_check(program, diagnostics: list[dict[str, object]]) -> None:
    """Cheap structural checks that do not execute anything."""
    known_imports = {"execute", "math", "pe", "atom"}

    def walk(statements, imports: set[str]) -> None:
        for statement in statements:
            kind = statement.kind
            line = statement.line
            if kind == "import":
                component = str(statement.data.get("component", "")).lower()
                if component in known_imports:
                    imports = imports | {component}
                elif component not in known_imports:
                    diagnostics.append(
                        {
                            "line": line,
                            "message": f"unknown component '{component}'",
                            "severity": "warning",
                        }
                    )
            elif kind == "call":
                component = str(statement.data.get("component", "")).lower()
                if component not in imports and component not in known_imports:
                    diagnostics.append(
                        {
                            "line": line,
                            "message": f"component '{component}' is used before being imported",
                            "severity": "warning",
                        }
                    )
            elif kind == "jump":
                target = statement.data.get("target_line")
                if isinstance(target, int) and target < 1:
                    diagnostics.append(
                        {
                            "line": line,
                            "message": "jump target must be a positive line number",
                            "severity": "error",
                        }
                    )
            if getattr(statement, "body", ()):
                walk(statement.body, imports)

    walk(program.statements, set())


if __name__ == "__main__":
    raise SystemExit(main())
