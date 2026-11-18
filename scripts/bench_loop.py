"""解释器热循环微基准：两语句 while 循环的每次迭代开销。

用法::

    python scripts/bench_loop.py --iterations 5000 --repeat 3
    python scripts/bench_loop.py --iterations 5000 --repeat 3 --json
    python scripts/bench_loop.py --record result.json
    python scripts/bench_loop.py --baseline before.json

基准程序把 1..N-1 累加进 {sum}，每轮两条 math_run 加两条 if/goto push，
用 print 输出最终结果；脚本自己算期望值 N*(N-1)/2 并校验。
"""

from __future__ import annotations

import argparse
import gc
import io
import json
import platform
import statistics
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cosmic_interpreter.parser import ParseError, parse
from cosmic_interpreter.runtime import Interpreter
from cosmic_interpreter.runtime import RuntimeError as CosmicRuntimeError

# 固定基准程序；__LIMIT__ 由 --iterations 替换。
SOURCE_TEMPLATE = """\
#math
call = (math)

math_run 0 = ?
if up = (ok) goto (push ({sum} = {(in = up)}))
math_run 1 = ?
if up = (ok) goto (push ({n} = {(in = up)}))
while ({n} < __LIMIT__)
{
    math_run {sum} + {n} = ?
    if up = (ok) goto (push ({sum} = {(in = up)}))
    math_run {n} + 1 = ?
    if up = (ok) goto (push ({n} = {(in = up)}))
}
print (<{sum}>)
"""

BENCHMARK_NAME = "while-sum-2stmt"


def _build_program(iterations: int):
    """解析一次，重复测量时复用同一 Program。"""
    return parse(SOURCE_TEMPLATE.replace("__LIMIT__", str(iterations)))


def _run_once(program) -> tuple[float, str, str]:
    """跑一次，返回 (秒, stdout, stderr)；输出重定向到 StringIO，不污染真实 stdout。"""
    out = io.StringIO()
    err = io.StringIO()
    interpreter = Interpreter(output=out, error_output=err)
    gc.collect()
    start = time.perf_counter()
    interpreter.run(program)
    elapsed = time.perf_counter() - start
    return elapsed, out.getvalue(), err.getvalue()


def _parse_sum(text: str) -> int | None:
    """取 stdout 里最后一个数值（float64 模式下可能带小数点）。"""
    for token in reversed(text.split()):
        try:
            return int(float(token))
        except ValueError:
            continue
    return None


def _payload(
    iterations: int,
    repeat: int,
    samples: list[float],
    totals: list[float],
    actual_sum: int | None,
    expected_sum: int,
) -> dict:
    loop_passes = max(iterations - 1, 1)
    return {
        "benchmark": BENCHMARK_NAME,
        "iterations": iterations,
        "loop_iterations": loop_passes,
        "repeat": repeat,
        "us_per_iteration_median": round(statistics.median(samples), 3),
        "us_per_iteration_min": round(min(samples), 3),
        "us_per_iteration_max": round(max(samples), 3),
        "samples_us_per_iteration": [round(value, 3) for value in samples],
        "total_seconds_median": round(statistics.median(totals), 6),
        "expected_sum": expected_sum,
        "actual_sum": actual_sum,
        "correct": actual_sum == expected_sum,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def _load_baseline_us(path: Path) -> float:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("us_per_iteration_median", "us_per_iteration", "median_us"):
        value = data.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    raise ValueError(f"{path}: no usable us_per_iteration field")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Micro-benchmark the Cosmic ASM interpreter hot loop.",
    )
    parser.add_argument("--iterations", type=int, default=5000, help="loop bound N (default: 5000)")
    parser.add_argument("--repeat", type=int, default=3, help="runs per measurement (default: 3)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--record", type=Path, help="write this measurement to a JSON file")
    parser.add_argument("--baseline", type=Path, help="compare against a recorded JSON file")
    args = parser.parse_args(argv)

    if args.iterations < 2:
        parser.error("--iterations must be at least 2")
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")

    baseline_us: float | None = None
    if args.baseline is not None:
        try:
            baseline_us = _load_baseline_us(args.baseline)
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2

    try:
        program = _build_program(args.iterations)
    except (ParseError, OSError, UnicodeError) as error:
        print(f"parse error: {error}", file=sys.stderr)
        return 2

    loop_passes = args.iterations - 1
    expected_sum = args.iterations * loop_passes // 2

    samples: list[float] = []
    totals: list[float] = []
    actual_sum: int | None = None
    errors: list[str] = []
    for _ in range(args.repeat):
        try:
            elapsed, stdout_text, stderr_text = _run_once(program)
        except CosmicRuntimeError as error:
            errors.append(str(error))
            continue
        totals.append(elapsed)
        samples.append(elapsed * 1_000_000 / loop_passes)
        actual_sum = _parse_sum(stdout_text)
        if stderr_text.strip():
            errors.append(stderr_text.strip())

    if not samples:
        print("error: every run failed", file=sys.stderr)
        for message in errors:
            print(message, file=sys.stderr)
        return 1

    result = _payload(args.iterations, args.repeat, samples, totals, actual_sum, expected_sum)
    if baseline_us is not None:
        current = result["us_per_iteration_median"]
        result["baseline_us_per_iteration"] = round(baseline_us, 3)
        result["speedup"] = round(baseline_us / current, 2) if current > 0 else None

    if args.record is not None:
        try:
            args.record.parent.mkdir(parents=True, exist_ok=True)
            args.record.write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        except OSError as error:
            print(f"error: cannot write {args.record}: {error}", file=sys.stderr)
            return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        _print_report(result, loop_passes, errors, baseline_us)

    return 0 if result["correct"] else 1


def _print_report(
    result: dict, loop_passes: int, errors: list[str], baseline_us: float | None
) -> None:
    print(f"benchmark  : {result['benchmark']}")
    print(f"iterations : {result['iterations']} (loop passes: {loop_passes})")
    print(f"repeat     : {result['repeat']}")
    print(f"python     : {result['python']} ({platform.system()})")
    print(
        "us/iter    : median {:.2f}  min {:.2f}  max {:.2f}".format(
            result["us_per_iteration_median"],
            result["us_per_iteration_min"],
            result["us_per_iteration_max"],
        )
    )
    print(f"total      : {result['total_seconds_median']:.4f} s (median)")
    status = "OK" if result["correct"] else "MISMATCH"
    print(f"result     : {result['actual_sum']} (expected {result['expected_sum']}) -> {status}")
    if baseline_us is not None:
        print(f"baseline   : {result['baseline_us_per_iteration']:.2f} us/iter")
        print(f"speedup    : {result['speedup']:.2f}x")
    for message in errors:
        print(f"warning    : {message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
