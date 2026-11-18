"""Entry point for `python -m cosmic_interpreter`.

Runs the interpreter, so `python -m cosmic_interpreter program.cos`
behaves exactly like `python -m cosmic_interpreter.cli program.cos`.

The other two tools are reachable as their own modules:

    python -m cosmic_interpreter.compiler program.cos -o out.exe
    python -m cosmic_interpreter.cosp --help
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
