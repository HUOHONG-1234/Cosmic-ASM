"""统一的 ``cosmic`` 命令行入口（v8 主线 E1）。

三个旧入口合并成一个可执行文件：

* ``cosmic run <文件>``      → 解释执行（等价旧的 CosmicInterpreter.exe）
* ``cosmic run --repl``      → 打开 TUI REPL
* ``cosmic build <文件>``    → 翻译成 C 并调用 GCC（等价旧的 CosmicCompiler.exe）
* ``cosmic pkg <子命令>``    → 包管理器（等价旧的 cosp.exe）
* ``cosmic index``           → 列出远端索引里的包（``cosmic pkg index`` 的简写）
* ``cosmic tui``             → 打开 TUI REPL
* ``cosmic version``         → 版本信息

为了不破坏既有脚本，旧入口仍可通过 ``cosmic_interpreter.cli`` /
``cosmic_interpreter.compiler`` / ``cosmic_interpreter.cosp`` 直接调用。
"""

from __future__ import annotations

import sys

__version__ = "0.1.0"

_EPILOG = """\
示例:
  cosmic run program.cos            解释执行
  cosmic run --repl                 打开 REPL
  cosmic build program.cos -o app   编译为原生程序
  cosmic pkg install                按 cosp.lock 安装依赖
  cosmic index                      列出远端索引里的包
"""


def _run(args: list[str]) -> int:
    if args and args[0] in {"--repl", "repl"}:
        return _repl()
    from .cli import main as interpreter_main

    return interpreter_main(args)


def _build(args: list[str]) -> int:
    from .compiler import main as compiler_main

    return compiler_main(args)


def _pkg(args: list[str]) -> int:
    from .cosp import main as cosp_main

    return cosp_main(args)


def _repl() -> int:
    from .repl import run_repl

    return run_repl()


def _version() -> int:
    print(f"cosmic {__version__}")
    print(f"python {sys.version.split()[0]} ({sys.platform})")
    return 0


_USAGE = """\
用法: cosmic <命令> [参数...]

命令:
  run <文件> [参数]    解释执行 .cos 文件
  run --repl           打开 TUI REPL
  build <文件> [...]   编译成原生可执行文件（其余参数透传给编译器）
  pkg <子命令> [...]   包管理器（参数透传给 cosp）
  index [--index URL]  列出远端索引里的包
  tui                  打开 TUI REPL
  version              显示版本信息

""" + _EPILOG


def main(argv: list[str] | None = None) -> int:
    # 不用 argparse 的子命令：这里要把 `build x.cos -o app`、`pkg add x url`
    # 之类的参数**按原顺序**透传，argparse 的 REMAINDER 遇到前置 --flag 会报
    # unrecognized arguments。
    tokens = list(sys.argv[1:] if argv is None else argv)
    if not tokens or tokens[0] in {"-h", "--help", "help"}:
        print(_USAGE, end="")
        return 0 if tokens else 2
    if tokens[0] == "--version":
        return _version()
    command, rest = tokens[0], tokens[1:]
    if command == "run":
        return _run(rest)
    if command == "build":
        return _build(rest)
    if command == "pkg":
        return _pkg(rest)
    if command == "index":
        return _pkg(["index", *rest])
    if command == "tui":
        return _repl()
    if command == "version":
        return _version()
    print(f"cosmic: 未知命令 '{command}'\n", file=sys.stderr)
    print(_USAGE, end="", file=sys.stderr)
    return 2


def _entry() -> int:
    return main()


if __name__ == "__main__":
    raise SystemExit(main())
