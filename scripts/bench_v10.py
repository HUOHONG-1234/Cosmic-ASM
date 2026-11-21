"""v10 压测：递归深度、文本累积、basel 基准。

用法：
    python scripts/bench_v10.py            # 解释器 + 编译版
    python scripts/bench_v10.py --quick    # 只跑解释器的小规模

每项都打印「修前 / 修后」的对照，修前数字来自 v9 的实测。
"""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cosmic_interpreter.parser import parse  # noqa: E402
from cosmic_interpreter.runtime import Interpreter  # noqa: E402


def run_interpreted(source: str) -> tuple[str, float]:
    output = io.StringIO()
    started = time.perf_counter()
    Interpreter(
        output=output, error_output=io.StringIO(), sleep=lambda _seconds: None
    ).run(parse(source))
    return output.getvalue().replace("\r\n", "\n"), time.perf_counter() - started


def compile_and_time(source: str) -> tuple[int, str, float]:
    from cosmic_interpreter.compiler import compile_source

    with tempfile.TemporaryDirectory() as temporary_directory:
        directory = Path(temporary_directory)
        source_path = directory / "bench.cos"
        source_path.write_bytes(source.encode("utf-8"))
        started = time.perf_counter()
        _c_path, executable = compile_source(
            source_path, output_path=directory / "bench.exe"
        )
        completed = subprocess.run(
            [str(executable)], check=False, capture_output=True, timeout=600
        )
        elapsed = time.perf_counter() - started
    return (
        completed.returncode,
        completed.stdout.decode(errors="replace").replace("\r\n", "\n"),
        elapsed,
    )


RECURSION_SOURCE = '''function depth(n)
{{
    if (n <= 0)
    {{
        return 0
    }}
    return depth(n - 1) + 1
}}
math_run depth({depth}) = ?
if up = (ok) goto (push ({{result}} = {{(in = up)}}))
print (<{{result}}>)
'''

FIB_SOURCE = """function fib(n)
{{
    if (n < 2)
    {{
        return n
    }}
    return fib(n - 1) + fib(n - 2)
}}
math_run fib({n}) = ?
if up = (ok) goto (push ({{result}} = {{(in = up)}}))
print (<{{result}}>)
"""

TEXT_SOURCE = """function grow(times)
{{
    let acc = ""
    let i = 0
    while (i < times)
    {{
        set acc = text_concat(acc, "0123456789abcdef")
        set i = i + 1
    }}
    return acc
}}
math_run strlen(grow({times})) = ?
if up = (ok) goto (push ({{result}} = {{(in = up)}}))
print (<{{result}}>)
"""


def bench_recursion(depths: list[int], compile_too: bool) -> None:
    print("== 递归深度 ==")
    print("修前：约 200 层就 RecursionError；20 层曾要 25 秒以上（2^n 求值）")
    for depth in depths:
        source = RECURSION_SOURCE.format(depth=depth)
        try:
            output, elapsed = run_interpreted(source)
            print(f"  解释器 depth={depth:<6} {elapsed * 1000:8.1f} ms  结果={output.strip()}")
        except RecursionError:
            print(f"  解释器 depth={depth:<6} RecursionError")
            continue
        if compile_too:
            code, output, elapsed = compile_and_time(source)
            status = "ok" if code == 0 else f"rc={code}"
            print(f"  编译版 depth={depth:<6} {elapsed * 1000:8.1f} ms  结果={output.strip()} ({status})")


def bench_fib(n: int) -> None:
    source = FIB_SOURCE.format(n=n)
    output, elapsed = run_interpreted(source)
    print(f"  fib({n}) 解释器 {elapsed * 1000:8.1f} ms  结果={output.strip()}")


def bench_text(times: int, compile_too: bool) -> None:
    source = TEXT_SOURCE.format(times=times)
    print(f"== 文本累积（{times * 16} 字节）==")
    print("修前：没有 text_concat，只能嵌套内置，无法累积")
    output, elapsed = run_interpreted(source)
    print(f"  解释器 {elapsed * 1000:8.1f} ms  长度={output.strip()}")
    if compile_too:
        code, output, elapsed = compile_and_time(source)
        print(f"  编译版 {elapsed * 1000:8.1f} ms  长度={output.strip()} (rc={code})")


def bench_basel(compile_too: bool) -> None:
    example = REPO_ROOT / "examples" / "basel.cos"
    if not example.is_file():
        print("== basel ==")
        print("  缺少 examples/basel.cos，跳过")
        return
    print("== basel（examples/basel.cos）==")
    print("修前：解释器 100 万次约 135 秒")
    source = example.read_text(encoding="utf-8")
    output, elapsed = run_interpreted(source)
    print(f"  解释器 {elapsed:8.2f} s  末行={output.strip().splitlines()[-1] if output.strip() else ''}")
    if compile_too:
        code, output, elapsed = compile_and_time(source)
        print(f"  编译版 {elapsed:8.2f} s (rc={code})")


def main() -> int:
    parser = argparse.ArgumentParser(description="v10 压测")
    parser.add_argument("--quick", action="store_true", help="只跑小规模，不动编译版")
    parser.add_argument("--no-compile", action="store_true", help="跳过编译版")
    args = parser.parse_args()

    compile_too = not (args.quick or args.no_compile)
    depths = [100, 500] if args.quick else [100, 500, 1000, 2000]
    bench_recursion(depths, compile_too and not args.quick)
    print()
    print("== 朴素递归（调用次数应约等于斐波那契调用数，不再翻倍）==")
    bench_fib(20)
    print()
    bench_text(1024 if args.quick else 65536, compile_too and not args.quick)
    print()
    if not args.quick:
        bench_basel(compile_too)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
