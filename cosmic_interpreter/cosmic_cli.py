"""Unified ``cosmic`` command line entry point (v8 track E1).

One executable replaces the three legacy entry points:

* ``cosmic run <file>``      - run a source file with the interpreter
* ``cosmic run --repl``      - open the TUI REPL
* ``cosmic build <file>``    - translate to C and invoke GCC
* ``cosmic build``           - build the current project's ``entry`` (like cosp build)
* ``cosmic init/new/add/remove/install/index/test`` - package manager (cosp)
* ``cosmic pkg <sub>``       - same package manager commands under the old namespace
* ``cosmic tui``             - open the TUI REPL
* ``cosmic version``         - version information

The legacy entry points (``cosmic_interpreter.cli`` / ``.compiler`` / ``.cosp``)
keep working, so existing scripts do not break.
"""

from __future__ import annotations

import sys

__version__ = "2.0.0"

_EPILOG = """\
Examples:
  cosmic run program.cos            run with the interpreter
  cosmic run --repl                 open the REPL
  cosmic build program.cos -o app   compile to a native executable
  cosmic new demo                   create a project
  cosmic install mathx              add a package from the index and install it
  cosmic install                    install everything from cosp.toml
  cosmic index                      list packages in the remote index
"""

# cosp subcommands exposed directly at the top level.
_COSP_COMMANDS = frozenset(
    {"init", "new", "add", "remove", "install", "index", "test", "pkg"}
)


def _run(args: list[str]) -> int:
    if args and args[0] in {"--repl", "repl"}:
        return _repl()
    from .cli import main as interpreter_main

    return interpreter_main(args)


def _build(args: list[str]) -> int:
    """``build <file>`` compiles that file; bare ``build`` builds the project entry."""
    looks_like_file = bool(args) and (
        args[0].lower().endswith(".cos") or args[0] in {"-h", "--help"}
    )
    if args and not looks_like_file:
        # 第一个参数不是源文件时按项目构建处理，交给 cosp。
        from .cosp import main as cosp_main

        return cosp_main(["build", *args], prog="cosmic")
    from .compiler import main as compiler_main

    return compiler_main(args)


def _pkg(args: list[str]) -> int:
    from .cosp import main as cosp_main

    return cosp_main(args, prog="cosmic")


def _repl() -> int:
    from .repl import run_repl

    return run_repl()


def _version() -> int:
    print(f"cosmic {__version__}")
    print(f"python {sys.version.split()[0]} ({sys.platform})")
    return 0


def _usage() -> str:
    return (
        "usage: cosmic <command> [args...]\n"
        "\n"
        "commands:\n"
        "  run <file> [args]        run a .cos file with the interpreter\n"
        "  run --repl               open the TUI REPL\n"
        "  repl                     open the TUI REPL (same as tui)\n"
        "  build <file> [options]   compile a .cos file to a native executable\n"
        "  build                    build the current project's entry file\n"
        "  init [--name NAME]       initialize a project in the current directory\n"
        "  new <name> [--template]  create a project from a scaffold\n"
        "  add <name> [path|url]    add a dependency (index, local path, or HTTPS ZIP)\n"
        "  remove <name>            remove a dependency\n"
        "  install [names...]       install dependencies; names are resolved from the index\n"
        "  index [--index URL]      list packages published in the remote index\n"
        "  test [args]              run the project's Python unittest suite\n"
        "  pkg <subcommand> [...]   package manager commands under the old namespace\n"
        "  tui                      open the TUI REPL\n"
        "  version                  print version information\n"
        "\n" + _EPILOG
    )


def main(argv: list[str] | None = None) -> int:
    # No argparse subparsers here: arguments such as `build x.cos -o app` must be
    # forwarded in their original order, and argparse's REMAINDER rejects leading
    # option-like tokens.
    tokens = list(sys.argv[1:] if argv is None else argv)
    if not tokens or tokens[0] in {"-h", "--help", "help"}:
        print(_usage(), end="")
        return 0 if tokens else 2
    if tokens[0] == "--version":
        return _version()
    command, rest = tokens[0], tokens[1:]
    if command == "run":
        return _run(rest)
    if command == "build":
        return _build(rest)
    if command in {"tui", "repl"}:
        return _repl()
    if command == "version":
        return _version()
    if command in _COSP_COMMANDS:
        if command == "pkg":
            # `cosmic pkg install x` → cosp 收到 ["install", "x"]（pkg 只是命名空间）
            if not rest:
                return _pkg(["--help"])
            return _pkg(rest)
        return _pkg([command, *rest])
    print(f"cosmic: unknown command '{command}'\n", file=sys.stderr)
    print(_usage(), end="", file=sys.stderr)
    return 2


def _entry() -> int:
    return main()


if __name__ == "__main__":
    raise SystemExit(main())
