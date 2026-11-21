from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .c_backend import estimate_memory, generate_c
from .diagnostics import format_message
from .parser import ParseError, parse
from . import hop
from .source_loader import load_source


class CompileError(Exception):
    pass


def _wsl_path(path: Path, distro: str) -> str:
    try:
        converted = subprocess.run(
            ["wsl.exe", "-d", distro, "--", "wslpath", "-a", str(path).replace("\\", "/")],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CompileError(f"cannot convert Windows path for WSL: {error}") from error
    result = converted.stdout.strip()
    if converted.returncode != 0 or not result.startswith("/"):
        detail = (converted.stderr or converted.stdout).strip()
        raise CompileError(f"WSL path conversion failed for '{path}': {detail}")
    return result


def compile_source(
    source_path: Path,
    *,
    output_path: Path | None = None,
    c_path: Path | None = None,
    gcc: str | None = None,
    wsl_distro: str = "Ubuntu-22.04",
    max_loop_iterations: int | None = None,
    no_input: bool = False,
    report_heap: bool = False,
    simd: bool = False,
    fast_math: bool = False,
    keep_symbols: bool = False,
    memory_report_path: Path | None = None,
    max_context_bytes: int | None = None,
    blas_library: str = "blas",
    lapack_library: str = "lapack",
) -> tuple[Path, Path]:
    if max_loop_iterations is not None and max_loop_iterations < 1:
        raise CompileError("max_loop_iterations must be greater than zero")
    if max_context_bytes is not None and max_context_bytes < 1:
        raise CompileError("max_context_bytes must be greater than zero")
    if not re.fullmatch(r"[A-Za-z0-9_+-]+", blas_library):
        raise CompileError("blas_library must be a library name without paths or linker flags")
    if not re.fullmatch(r"[A-Za-z0-9_+-]+", lapack_library):
        raise CompileError("lapack_library must be a library name without paths or linker flags")
    default_output = output_path is None
    source_path = source_path.resolve()
    if output_path is None:
        output_path = source_path.with_suffix(".exe" if os.name == "nt" else "")
    else:
        output_path = output_path.resolve()
    if c_path is None:
        c_path = output_path.with_suffix(".c")
    else:
        c_path = c_path.resolve()
    if output_path == source_path:
        raise CompileError("output executable path must not overwrite the source file")
    if c_path in {source_path, output_path}:
        raise CompileError("generated C path must be different from the source and executable paths")
    if memory_report_path is not None:
        memory_report_path = memory_report_path.resolve()
        if memory_report_path in {source_path, output_path, c_path}:
            raise CompileError("memory report path must be different from source, C, and executable paths")

    try:
        source = load_source(source_path)
    except (OSError, UnicodeError) as error:
        raise CompileError(f"cannot read source file '{source_path}': {error}") from error
    except ParseError as error:
        raise CompileError(hop.humanize(str(error))) from error
    try:
        program = parse(source)
        if program.target in {"riscv64-linux-gnu", "aarch64-linux-gnu"}:
            # 两种 Linux 交叉目标：RISC-V 与 ARM64 各自使用对应的 GNU 工具链
            selected_gcc = gcc or f"{program.target.removesuffix('-linux-gnu')}-linux-gnu-gcc"
            if default_output and os.name == "nt":
                output_path = source_path.with_suffix("")
                if c_path is None:
                    c_path = output_path.with_suffix(".c")
        else:
            selected_gcc = gcc or "gcc"
        generated = generate_c(
            program,
            max_loop_iterations=max_loop_iterations,
            no_input=no_input,
            report_heap=report_heap,
        )
        memory_report = estimate_memory(program)
        if (
            max_context_bytes is not None
            and int(memory_report["single_context_bytes"]) > max_context_bytes
        ):
            raise CompileError(
                f"static Context requires {memory_report['single_context_bytes']} bytes "
                f"for target '{memory_report['target']}', exceeding the configured "
                f"limit of {max_context_bytes} bytes"
            )
    except ParseError as error:
        raise CompileError(hop.humanize(str(error))) from error
    except (ValueError, KeyError) as error:
        raise CompileError(f"cannot generate C code: {error}") from error

    try:
        c_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        c_path.write_text(generated, encoding="utf-8")
    except OSError as error:
        raise CompileError(f"cannot write generated C file '{c_path}': {error}") from error

    # 两种交叉目标都是 Linux 产物：无 .exe 后缀、需要 -ldl/-pthread、经 WSL 调用
    linux_target = program.target in {"riscv64-linux-gnu", "aarch64-linux-gnu"}
    command = [selected_gcc, "-std=c11", "-D_DEFAULT_SOURCE"]
    if program.target == "riscv64-linux-gnu":
        command.extend(["-march=rv64gc", "-mabi=lp64d"])
    elif program.target == "aarch64-linux-gnu":
        command.append("-march=armv8-a")
    if simd:
        command.extend(["-O3", "-ftree-vectorize"])
    else:
        command.append("-O2")
    if program.features:
        command.extend(["-ffunction-sections", "-fdata-sections"])
    if fast_math:
        command.append("-ffast-math")
    if not keep_symbols:
        # 剥离符号表：mingw 默认不剥，产物里 70% 是调试符号
        command.append("-s")
    command.append(str(c_path))
    uses_blas = bool(re.search(r"\bblas_dot\s*\(", source, re.I))
    uses_lapack = bool(re.search(r"\blapack_determinant2\s*\(", source, re.I))
    if uses_lapack:
        command.append(f"-l{lapack_library}")
    if uses_blas or uses_lapack:
        command.append(f"-l{blas_library}")
    if program.features:
        command.append("-Wl,--gc-sections")
    command.extend(["-lm", "-o", str(output_path)])
    if linux_target and os.name == "nt":
        command = [
            "wsl.exe", "-d", wsl_distro, "--", selected_gcc,
            *command[1:],
        ]
        command[command.index(str(c_path))] = _wsl_path(c_path, wsl_distro)
        command[command.index(str(output_path))] = _wsl_path(output_path, wsl_distro)
    used_dll = "cosmic_dll_load" in generated
    if used_dll and (os.name != "nt" or linux_target):
        command.append("-ldl")
    if used_dll:
        for library in program.link_libraries:
            command.append(f"-l{library}")
    if "cosmic_atom_init" in generated and (os.name != "nt" or linux_target):
        command.append("-pthread")
    if os.name == "nt" and not linux_target:
        # Windows host 的 CSPRNG 走 BCryptGenRandom，需要链接 libbcrypt
        command.append("-lbcrypt")
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except FileNotFoundError as error:
        tool = "WSL" if linux_target and os.name == "nt" else selected_gcc
        raise CompileError(f"compiler executable not found: {tool}") from error
    except subprocess.TimeoutExpired as error:
        raise CompileError("GCC compilation timed out") from error
    except OSError as error:
        raise CompileError(f"could not start GCC: {error}") from error
    if completed.returncode != 0:
        diagnostic = (completed.stderr or completed.stdout).strip()
        raise CompileError(f"GCC failed with exit code {completed.returncode}:\n{diagnostic}")
    if memory_report_path is not None:
        report_path = memory_report_path.resolve()
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(memory_report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            raise CompileError(f"cannot write memory report '{report_path}': {error}") from error
    return c_path, output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Translate Cosmic ASM to C and compile it with GCC."
    )
    parser.add_argument("source", type=Path, help="path to a .cos or text source file")
    parser.add_argument("-o", "--output", type=Path, help="output executable path")
    parser.add_argument(
        "--c-output",
        type=Path,
        help="generated C path (kept by default; defaults to source.c)",
    )
    parser.add_argument("--gcc", help="GCC executable name or path (defaults to target toolchain)")
    parser.add_argument(
        "--wsl-distro",
        default="Ubuntu-22.04",
        help="WSL distribution used for RISC-V Linux cross-compilation on Windows",
    )
    parser.add_argument(
        "--no-input",
        action="store_true",
        help="make compiled ask statements fail without reading stdin",
    )
    parser.add_argument(
        "--simd",
        action="store_true",
        help="enable GCC optimization and automatic SIMD vectorization",
    )
    parser.add_argument(
        "--fast-math",
        action="store_true",
        help="enable GCC -ffast-math; this may change IEEE-754 NaN and infinity behavior",
    )
    parser.add_argument(
        "--keep-symbols",
        action="store_true",
        help="keep the symbol table in the binary (default: stripped with -s, output is ~70%% smaller)",
    )
    parser.add_argument(
        "--memory-report",
        nargs="?",
        const="",
        metavar="PATH",
        help="write a JSON estimate of one generated Context's static storage",
    )
    parser.add_argument(
        "--max-context-bytes",
        type=int,
        help="reject programs whose main static Context exceeds this byte limit",
    )
    parser.add_argument(
        "--heap-report",
        action="store_true",
        help="compile a binary that prints its peak temporary allocation bytes to stderr on exit",
    )
    parser.add_argument(
        "--blas-library",
        default="blas",
        help="system BLAS library name used by blas_dot (default: blas)",
    )
    parser.add_argument(
        "--lapack-library",
        default="lapack",
        help="system LAPACK library name used by lapack_determinant2 (default: lapack)",
    )
    parser.add_argument(
        "--max-loop-iterations",
        type=int,
        help="optional safety cap for start(loop) blocks and while loops",
    )
    args = parser.parse_args(argv)
    memory_report_path = None
    if args.memory_report is not None:
        memory_report_path = (
            Path(args.memory_report)
            if args.memory_report
            else (args.output or args.source).with_suffix(".memreport")
        )
    try:
        c_path, output_path = compile_source(
            args.source,
            output_path=args.output,
            c_path=args.c_output,
            gcc=args.gcc,
            wsl_distro=args.wsl_distro,
            max_loop_iterations=args.max_loop_iterations,
            no_input=args.no_input,
            simd=args.simd,
            fast_math=args.fast_math,
            keep_symbols=args.keep_symbols,
            memory_report_path=memory_report_path,
            max_context_bytes=args.max_context_bytes,
            report_heap=args.heap_report,
            blas_library=args.blas_library,
            lapack_library=args.lapack_library,
        )
    except CompileError as error:
        message = str(error)
        source_text = None
        try:
            source_text = load_source(args.source)
        except (OSError, UnicodeError, ParseError):
            source_text = None
        print(
            format_message(message, source_text=source_text, path=args.source),
            file=sys.stderr,
        )
        return 1
    print(f"Generated C: {c_path}")
    print(f"Executable: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
