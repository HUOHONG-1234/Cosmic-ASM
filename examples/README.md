# 示例索引

25 个可运行程序，每一个都经过测试。全部用解释器跑通，其中大部分也验证过编译版输出一致。

## 怎么跑

```powershell
# 解释执行
python -m cosmic_interpreter.cli examples\demo.cos

# 编译成 exe
python -m cosmic_interpreter.compiler examples\demo.cos -o demo.exe
```

---

## 入门

| 文件 | 内容 | 关键点 |
|---|---|---|
| [`demo.cos`](./demo.cos) | 综合演示 | 块、变量、`father`、`dog` |
| [`unicode_conversion.cos`](./unicode_conversion.cos) | 编码声明 | `#name = GBK`、`chr`/`ord` |

```asm
#demo.cos 的核心结构
#execute
call = (execute)
var greeting = {"Hello"}
father = (up)

start(normal) = {main}
{
    print (<{greeting}>)
    if up = (ok) goto (to = stop)
}
dog(main) = {stop}
```

---

## 控制流与算法

| 文件 | 内容 |
|---|---|
| [`fibonacci.cos`](./fibonacci.cos) | 斐波那契数列 |
| [`bubble_sort.cos`](./bubble_sort.cos) | 固定四元素冒泡排序 |
| [`sort4.cos`](./sort4.cos) | 排序网络，5 次比较交换 |
| [`miller_rabin.cos`](./miller_rabin.cos) | 米勒-拉宾素性检验（单基 2） |
| [`scientific_tape.cos`](./scientific_tape.cos) | 纸带、条件循环 |

`sort4.cos` 用的是**排序网络**而不是循环——因为数组下标在循环里写起来麻烦，固定四变量反而更清楚。

---

## 字符串与古典密码

| 文件 | 内容 | 实测输出 |
|---|---|---|
| [`caesar.cos`](./caesar.cos) | 凯撒密码，位移 3 | `HELLO` → `KHOOR` |
| [`vigenere.cos`](./vigenere.cos) | 维吉尼亚密码 | `HELLO` + `KEY` → `RIJVS` |

两个都只处理大写字母。玩的作用是练 `chr` / `ord` / `char_at` / 字符串拼接。

> 古典密码不是密码学。真加密见 [sha256.cos](./sha256.cos) / [aes128.cos](./aes128.cos) 或者走 DLL。

---

## 数学

### 圆周率

| 文件 | 方法 | 精度 |
|---|---|---|
| [`pi_leibniz.cos`](./pi_leibniz.cos) | 莱布尼茨级数 | 20 万项 → 5 位 |
| [`basel.cos`](./basel.cos) | 巴塞尔问题 | 100 万项 → 6 位 |
| [`pi_machin.cos`](./pi_machin.cos) | 梅钦公式 | 一次 → 14 位 |
| [`machin.cos`](./machin.cos) | 同上，代码更整洁 | 14 位 |
| [`monte_carlo_pi.cos`](./monte_carlo_pi.cos) | 蒙特卡洛 | 20 万点 → 4 位 |

收敛速度差别极大，正好拿来对比：

```
莱布尼茨   20 万项   →  5 位    解释器 23.7 秒
巴塞尔    100 万项   →  6 位
梅钦公式   几个运算   → 14 位    ← float64 极限
```

> `machin.cos` 用定点法 + bigint 能到 **39 位**，见下文。

### 复数与高等函数

| 文件 | 内容 |
|---|---|
| [`euler_identity.cos`](./euler_identity.cos) | 欧拉恒等式 `e^(iπ) + 1 = 0` |
| [`advanced_math.cos`](./advanced_math.cos) | 精度控制（`#%precision = 17`） |
| [`numerical.cos`](./numerical.cos) | 积分、统计、Zeta |

`euler_identity.cos` 的输出：

```
-1+1.22464679914735e-16i
```

虚部那个 `1.2e-16` 就是 float64 的精度极限，不是 bug。

### 物理

| 文件 | 内容 |
|---|---|
| [`nbody.cos`](./nbody.cos) | 三体引力模拟（太阳 + 地球 + 木星） |

> ⚠️ 这个示例的初速度参数填得不准，只能当语言演示，**不能当物理结果**。

---

## v5：位运算、字节、矩阵

### 位运算与字节数组

[`bitops.cos`](./bitops.cos) — 2.3 KB

```asm
#%math = uint32
math_run 12 & 10 = ?        # 8
math_run 12 | 10 = ?        # 14
math_run 12 ^ 10 = ?        # 6
math_run 1 << 10 = ?        # 1024
math_run ~5 = ?             # 4294967290（uint32 回绕）

bytes BLK = [97, 98, 99, 128]
math_run bytes_get(BLK, 0) = ? -uint32
math_run hex(255) = ?       # "ff"
math_run unhex("ff") = ?    # 255
```

**关键**：`~5` 在 `uint32` 模式下是 `4294967290`，在 `int64` 模式下是 `-6`。回绕行为和模式绑定。

### 线性代数

[`linear_algebra.cos`](./linear_algebra.cos) — 4.1 KB

```asm
array M = [[1, 2, 3], [4, 5, 6]]
math_run matrix_rows(M) = ?   # 2
math_run matrix_cols(M) = ?   # 3
math_run matrix_at(M, 1, 2) = ?  # 6

# 转置保持形状
array T = matrix_transpose([[1, 2, 3], [4, 5, 6]])   # 3x2

# 矩阵乘法
array C = matrix_mul([[1, 2, 3], [4, 5, 6]], [[7, 8], [9, 10], [11, 12]])

# 解方程
array x = solve_linear([[2, 1], [1, 3]], [5, 6])     # [1.8, 1.4]
```

**注意**：这些函数返回**数组**，不能直接当标量用。要 `array C = matrix_mul(...)` 接，或者 `matrix_at` 取值。

### 数值方法

[`numerical.cos`](./numerical.cos) — 4.6 KB

```asm
# 梯形法：x² 从 0 到 1，步长 0.25
array ys = [0, 0.0625, 0.25, 0.5625, 1.0]
math_run trapz(ys, 0.25) = ?     # 0.34375

# 辛普森法：对二次多项式精确
math_run simpson(ys, 0.25) = ?   # 0.333333333333333（= 1/3）

# 黎曼 Zeta
math_run zeta(2) = ?             # 1.64493406684823
math_run zeta(3) = ?             # 1.20205690315959
```

**签名是 `(样本数组, 步长)`**，不是 `(xs, ys)`——这条容易搞错。

---

## v4：并发

| 文件 | 内容 |
|---|---|
| [`atom_counter.cos`](./atom_counter.cos) | `lock` 保护的共享计数器 |
| [`atom_chain.cos`](./atom_chain.cos) | `proton` 串执行链 |
| [`dll_status.cos`](./dll_status.cos) | DLL 加载 / 调用 / 查询 / 卸载 |

```asm
#atom
call = (atom)

start(normal) = {worker}
{
    lock {worker} -timeout 0.5
    {
        math_run 1 + 1 = ? -int64
    }
}

proton {chain} = {worker} -> {worker} -> {worker}
nuclear_reactor {main} = {chain}
```

**`nuclear_reactor` 是异步的**，主程序会立刻往下跑。要等结果必须 `dog` 或 `lock`。

---

## 密码学实现

| 文件 | 内容 | 大小 |
|---|---|---|
| [`sha256.cos`](./sha256.cos) | SHA-256 完整实现 | 58 KB |
| [`aes128.cos`](./aes128.cos) | AES-128 单块加密（FIPS-197） | 56 KB |

这两个文件很大，因为 K 常量表和 S-box 都是硬编码的数组字面量。

```asm
#%math = uint32
#% SHA-256 需要 uint32 回绕和位运算
array KS = [0x428a2f98, 0x71374491, ...]
bytes BLK = [97, 98, 99, 128, 0, ..., 24]

math_run (bytes_get(BLK, 0) << 24) + (bytes_get(BLK, 1) << 16) + ... = ?
```

**测试向量**（实现正确性的唯一判据）：

```
SHA-256("abc") = ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
SHA-256("")    = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855

AES-128(key=000102...0f, plaintext=00112233...ff) = 69c4e0d86a7b0430d8cdb78070b4c55a
```

> ⚠️ **这两个不是安全实现。** 查表会命中缓存、循环长度依赖数据、分支走向可测。
> 它们证明「COS 能表达这些算法」，不证明「COS 能保护你的数据」。
> 生产加密请走 [`dll_status.cos`](./dll_status.cos) 那种方式调用 OpenSSL / CNG。

---

## 写自己的示例

最小骨架：

```asm
#execute
call = (execute)
var msg = {"hello"}
father = (up)

start(normal) = {main}
{
    print (<{msg}>)
    if up = (ok) goto (to = stop)
}
dog(main) = {stop}
```

需要算术就加 `#math` + `call = (math)`，需要位运算再加 `#%math = uint32`。

**常见坑**：
- `{4}` 不合法，要写 `{"4"}`
- `/` 在 bigint 模式下报错，要用 `//`
- `#math = int64` 会被当编码声明，必须写 `#%math = int64`
- `print (<{数组}>)` 静默无输出，要用 `array_at`

---

*所有示例的运行结果都在 CI 里对拍过，解释器和编译器输出必须一致。*
