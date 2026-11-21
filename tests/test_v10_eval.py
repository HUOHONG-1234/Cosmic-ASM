"""v10 阶段 1：B1（消除 2^n）与 B2（call_depth 全路径）。

这两个 bug 一起导致「递归 15 层就要 3 秒、20 层超时」。测试用**调用计数**而不是
耗时来判断，因为耗时受机器影响，而调用次数是精确的语义事实。
"""

from __future__ import annotations

import ast
import io
import sys
import unittest

import cosmic_interpreter.runtime as runtime
from cosmic_interpreter.parser import parse


def run_source(source: str) -> str:
    output = io.StringIO()
    runtime.Interpreter(
        output=output, error_output=io.StringIO(), sleep=lambda _seconds: None
    ).run(parse(source))
    return output.getvalue().replace("\r\n", "\n")


class CallCountingTests(unittest.TestCase):
    """B1：函数体只能执行一次，不能被「先试数组、再试数值」跑两遍。

    计数点选在 ``_body_gen`` 上：v10 起所有用户函数调用（单表达式与语句体）都经过
    trampoline 的这一处入口，所以「同一个调用被执行两次」一定能被看到。
    """

    def setUp(self) -> None:
        self.dispatches: dict[str, int] = {}
        self._original = runtime._body_gen
        counter = self

        def counting(entry, arguments, values, **kwargs):
            name = entry.name or "<anonymous>"
            counter.dispatches[name] = counter.dispatches.get(name, 0) + 1
            return counter._original(entry, arguments, values, **kwargs)

        runtime._body_gen = counting
        self.addCleanup(setattr, runtime, "_body_gen", self._original)

    def calls(self, name: str) -> int:
        return self.dispatches.get(name, 0)

    def test_naive_fibonacci_calls_each_node_once(self) -> None:
        run_source(
            "function fib(n)\n"
            "{\n"
            "    if (n < 2)\n"
            "    {\n"
            "        return n\n"
            "    }\n"
            "    return fib(n - 1) + fib(n - 2)\n"
            "}\n"
            "math_run fib(10) = ?\n"
        )
        # fib(10) 的调用次数是 2 * fib(11) - 1 = 177；翻倍会得到 354 以上。
        self.assertEqual(self.calls("fib"), 177)

    def test_linear_recursion_calls_once_per_level(self) -> None:
        run_source(
            "function depth(n)\n"
            "{\n"
            "    if (n <= 0)\n"
            "    {\n"
            "        return 0\n"
            "    }\n"
            "    return depth(n - 1) + 1\n"
            "}\n"
            "math_run depth(20) = ?\n"
        )
        # depth(20) .. depth(0) 共 21 次调用；翻倍会到 42 以上。
        self.assertEqual(self.calls("depth"), 21)

    def test_call_used_as_argument_is_not_evaluated_twice(self) -> None:
        # 实参位置的调用（旧的 _eval_value_node 会跑两遍）只能执行一次。
        result = run_source(
            "function inc(n)\n"
            "{\n"
            "    return n + 1\n"
            "}\n"
            "function twice(n)\n"
            "{\n"
            "    return inc(inc(n))\n"
            "}\n"
            "math_run twice(5) = ?\n"
            "if up = (ok) goto (push ({r} = {(in = up)}))\n"
            "print (<{r}>)\n"
        )
        self.assertEqual(result, "7\n")
        self.assertEqual(self.calls("twice"), 1)
        self.assertEqual(self.calls("inc"), 2)


class RecursionGuardTests(unittest.TestCase):
    """B2：递归上限在数组实参路径上同样生效。"""

    def run_with_big_python_stack(self, source: str) -> str:
        original = sys.getrecursionlimit()
        sys.setrecursionlimit(100000)
        try:
            return run_source(source)
        finally:
            sys.setrecursionlimit(original)

    def test_limit_fires_on_scalar_path(self) -> None:
        with self.assertRaises(Exception) as caught:
            self.run_with_big_python_stack(
                "function d(n)\n"
                "{\n"
                "    if (n <= 0)\n"
                "    {\n"
                "        return 0\n"
                "    }\n"
                "    return d(n - 1) + 1\n"
                "}\n"
                "math_run d(5000) = ?\n"
            )
        self.assertIn("function recursion depth exceeded", str(caught.exception))

    def test_limit_fires_on_array_argument_path(self) -> None:
        # 旧实现里这条路径把 call_depth 丢了，计数器永远停在 0，上限形同虚设。
        with self.assertRaises(Exception) as caught:
            self.run_with_big_python_stack(
                "function d(a, n)\n"
                "{\n"
                "    if (n <= 0)\n"
                "    {\n"
                "        return array_len(a)\n"
                "    }\n"
                "    return d(a, n - 1)\n"
                "}\n"
                "math_run d([1, 2], 5000) = ?\n"
            )
        self.assertIn("function recursion depth exceeded", str(caught.exception))


class ArrayStillWorksTests(unittest.TestCase):
    """B1 改成静态判定后，数组/记录的既有路径不能退化。"""

    def test_array_argument_and_return(self) -> None:
        self.assertEqual(
            run_source(
                "function total(a) = array_sum(a)\n"
                "function make(n) = array_fill(n, 3)\n"
                "math_run total(make(4)) = ?\n"
                "if up = (ok) goto (push ({r} = {(in = up)}))\n"
                "print (<{r}>)\n"
            ),
            "12\n",
        )

    def test_record_argument_from_constructor(self) -> None:
        self.assertEqual(
            run_source(
                "record P { x, y }\n"
                "function len2(p : P) = p.x + p.y\n"
                "function make(a, b) -> P = P(a, b)\n"
                "math_run len2(make(3, 4)) = ?\n"
                "if up = (ok) goto (push ({r} = {(in = up)}))\n"
                "print (<{r}>)\n"
            ),
            "7\n",
        )

    def test_text_value_still_evaluated_once(self) -> None:
        self.assertEqual(
            run_source(
                "function shout(t) = upper(t)\n"
                "function twice(t) = shout(shout(t))\n"
                'math_run twice("ab") = ?\n'
                "print (<{(in = up)}>)\n"
            ),
            "AB\n",
        )


if __name__ == "__main__":
    unittest.main()
