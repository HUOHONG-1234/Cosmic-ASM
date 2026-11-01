# v5：位运算、字节、矩阵与数值计算


v5 的全部特性都在解释器和编译版中同时实现，并由测试逐项对照 Python 独立计算的期望值。

### 位运算与 `-uint32`

- 新增 `&`、`|`、`^`、`<<`、`>>`、`~`。位运算只能在整数模式下使用（`int32`/`int64`/`uint32`/`bigint`），在 `float64`/`complex` 下会明确报错。
- 新增 `-uint32` 模式：结果按 2³² 回绕而不是报溢出，`>>` 恒为逻辑右移。用户函数在 `uint32` 模式下返回 `uint32_t`，函数体内也使用无符号语义。
- `bigint` 模式下位运算以补码表示实现，`>>` 与 Python 一样是算术右移。
- 新增 `hex(value[, width])`（返回字符串）和 `unhex(text)`（建议配 `bigint` 模式，因为结果可能超出 64 位）。

### 字节数组

- `bytes name = [...]` 声明字节数组，元素必须是 `0..255` 的整数，越界会报错。
- `bytes_len(name)`、`bytes_get(name, i)`、`bytes_set(name, i, value)` 读写；与 `array` 一样按名原地修改，可在循环内使用。

### NxM 矩阵与线性方程组

- 矩阵写作嵌套列表 `[[a,b],[c,d]]`，**形状随值一起保存**：`matrix_transpose([[1,2,3],[4,5,6]])` 得到 3×2，而不是丢失维度变成 6 个元素的扁平表。
- `matrix_rows`、`matrix_cols`、`matrix_at(M, row, col)`、`matrix_mul(A, B)`、`matrix_transpose(M)`。
- `matrix2_inverse(M)`、`matrix3_inverse(M)`：仅支持实数方阵；奇异矩阵会报错。
- `solve_linear(A, b)`：高斯消元（列主元）解 `Ax = b`，返回普通数组，用 `array_at` 读取。`A` 必须是方阵且 `b` 长度等于行数。
- 矩阵值不能 `print`，也不能作为 `math_run` 标量结果，必须交给矩阵函数消费（与数组规则一致）。

### 数值积分、统计与特殊函数

- 积分：`trapz(array, h)` 梯形法、`simpson(array, h)` 辛普森法（要求奇数个样本且至少 3 个，对二次多项式精确）。
- 统计：`median`、`variance`（总体方差，与 `std` 一致）、`correlation`（皮尔逊相关系数）、`percentile(array, p)`（`p` 取 0–100，相邻样本线性插值）。
- 特殊函数：`lgamma`、`erfc`、`beta(a,b)`、`zeta(s)`（仅实数 `s > 1`，由直接求和加 Euler–Maclaurin 尾部修正实现，精度约 1e-15）。

示例：[bitops.cos](./examples/bitops.cos)、[linear_algebra.cos](./examples/linear_algebra.cos)、[numerical.cos](./examples/numerical.cos)、[sha256.cos](./examples/sha256.cos)、[aes128.cos](./examples/aes128.cos)。

> 密码学示例仅用于验证语言能力，是正确性演示，不是常量时间实现。

