from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from .c_backend import generate_c
from .parser import ParseError, parse


class CompileError(Exception):
    pass


def compile_source(
    source_path: Path,
    *,
    output_path: Path | None = None,
    c_path: Path | None = None,
    gcc: str = "gcc",
    max_loop_iterations: int | None = None,
    simd: bool = False,
    fast_math: bool = False,
    keep_symbols: bool = False,
    blas_library: str = "blas",
    lapack_library: str = "lapack",
) -> tuple[Path, Path]:
    if max_loop_iterations is not None and max_loop_iterations < 1:
        raise CompileError("max_loop_iterations must be greater than zero")
    if not re.fullmatch(r"[A-Za-z0-9_+-]+", blas_library):
        raise CompileError("blas_library must be a library name without paths or linker flags")
    if not re.fullmatch(r"[A-Za-z0-9_+-]+", lapack_library):
        raise CompileError("lapack_library must be a library name without paths or linker flags")
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

    try:
        source = source_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise CompileError(f"cannot read source file '{source_path}': {error}") from error
    try:
        program = parse(source)
        generated = generate_c(program, max_loop_iterations=max_loop_iterations)
    except ParseError as error:
        raise CompileError(str(error)) from error
    except (ValueError, KeyError) as error:
        raise CompileError(f"cannot generate C code: {error}") from error

    try:
        c_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        c_path.write_text(generated, encoding="utf-8")
    except OSError as error:
        raise CompileError(f"cannot write generated C file '{c_path}': {error}") from error

    command = [gcc, "-std=c11", "-D_DEFAULT_SOURCE"]
    if simd:
        command.extend(["-O3", "-ftree-vectorize"])
    else:
        command.append("-O2")
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
    command.extend(["-lm", "-o", str(output_path)])
    used_dll = "cosmic_dll_load" in generated
    if used_dll and os.name != "nt":
        command.append("-ldl")
    if used_dll:
        for library in program.link_libraries:
            command.append(f"-l{library}")
    if "cosmic_atom_init" in generated and os.name != "nt":
        command.append("-pthread")
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
        raise CompileError(f"GCC executable not found: {gcc}") from error
    except subprocess.TimeoutExpired as error:
        raise CompileError("GCC compilation timed out") from error
    except OSError as error:
        raise CompileError(f"could not start GCC: {error}") from error
    if completed.returncode != 0:
        diagnostic = (completed.stderr or completed.stdout).strip()
        raise CompileError(f"GCC failed with exit code {completed.returncode}:\n{diagnostic}")
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
    parser.add_argument("--gcc", default="gcc", help="GCC executable name or path")
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
    try:
        c_path, output_path = compile_source(
            args.source,
            output_path=args.output,
            c_path=args.c_output,
            gcc=args.gcc,
            max_loop_iterations=args.max_loop_iterations,
            simd=args.simd,
            fast_math=args.fast_math,
            keep_symbols=args.keep_symbols,
            blas_library=args.blas_library,
            lapack_library=args.lapack_library,
        )
    except CompileError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Generated C: {c_path}")
    print(f"Executable: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
