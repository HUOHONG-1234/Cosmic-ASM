# 报错排查手册

> 全部按真实解析器输出整理。格式统一为 `line N: 消息`。
> 完整版见 [D:\ASM\Cosmic ASM 解决方案.md](file:///D:/ASM/Cosmic%20ASM%20解决方案.md)（36 条）。

---

## 一、立刻能修的五个坑

这几条是新手上手十分钟内一定会踩到的。

### 1. `{4}` 不合法，要 `{"4"}`

```
line 3: braces must contain a variable name, {"quoted text"}, or (in = up)
```

```asm
# 错
push ({a} = {4})

# 对
push ({a} = {"4"})
```

`{name}` 是变量引用，`{"text"}` 才是字面量。**数字也要加引号。**

---

### 2. `/` 在 bigint 模式下不能用，要 `//`

```
line 5: invalid math expression: bigint mode requires integer-only expressions
```

```asm
#%math = bigint
# 错
math_run 10 ** 40 / 5 = ?
# 对
math_run 10 ** 40 // 5 = ?
```

---

### 3. `#math = int64` 会被当编码声明

```
line 1: unknown encoding: int64
```

```asm
# 错
#math = int64

# 对（注释形式）
#%math = int64
```

`#` 开头且形如 `名字 = 值` 的会被解析器当编码声明。数值模式必须写在注释里。

---

### 4. `goto (to = stop)` 不能单独成行

```
line 12: unrecognized statement: goto (to = stop)
```

```asm
# 错
goto (to = stop)

# 对
if up = (ok) goto (to = stop)
```

`goto` 只能跟在 `if` 后面。

---

### 5. 数组不能直接 `print`

没有报错，但**也没有输出**，`up` 状态是 `error`。

```asm
# 错——静默失败
print (<{arr}>)

# 对
math_run array_at(arr, 0) = ? -int64
if up = (ok) goto (push ({v} = {(in = up)}))
print (<{v}>)
```

---

## 二、声明与导入

### component 'math' has not been imported

```asm
# 错
call = (math)

# 对
#math
call = (math)
```

`execute` 和 `math` 虽然是内置的，`call` 之前仍然要有 `#` 导入行。

---

### component 'pe' is not supported

v1 的组件表里有 `#pe`，但**从未实现**。v5 及以前的表现是：

```asm
# 解释器：不报错，只擦一行 warning
warning: line 3: pe is recognized but not implemented
```

编译版更安静：生成一个 `STATUS_OK` 占位，什么也不做。

**这种状态最坑：它既不工作，又不像坏了。**

v6 起改为明确的编译错误。想解析 PE 文件的正确做法是写普通程序读字节：

```asm
#bytes
bytes head = [0x4D, 0x5A]
bytes_len(head) = ? -int64
```

---

### gc_pin / gc_unpin：声音放一半就断了

如果用了 `#dll` 把缓冲区交给异步外部库（典型：`PlaySoundA` 的 `SND_MEMORY`），**必须成对调用 pin/unpin**：

```asm
  = gc_pin (buf)
cdll = come (winmm) (playsounda) (0) (buf) (0)
  = gc_unpin (buf)
```

原因：`semi` 档用引用计数，函数一返回本地计数归零，缓冲区被释放，但声音还在放。

详见 [v6 设计](./v6-gc-asm.md)。

---

### `#asm = x86_64` 会被当编码声明

```
line 4: unknown encoding: x86_64
```

```asm
# 错
#asm = x86_64

# 对（声明写注释，标签单独一行）
#%asm = x86_64 (clobber: rax, memory)
#asm
{
    ...
}
```

跟 `#math = int64` 是同一个坑：`#` 开头且形如 `名字 = 值` 的一律进编码层，汇编块也不例外。

---

### asm block requires <arch>, current target is <arch>

v6 起汇编块强制声明架构，不匹配就编译期失败：

```asm
#%asm = arm64 (clobber: x0, memory)
#asm
{
    mov x0, #0
}
```

在 x86_64 上编这份文件报 `asm block at line N requires arm64, current target is x86_64`。

**这是故意的**，不做静默降级——不支持就跳过会让程序在另一个平台上悄悄干错事。要么改架构字段，要么删掉这一块。

---

### asm block missing clobber list

```asm
# 错：没写 clobber
#%asm = x86_64
#asm
{
    nop
}
```

clobber 必须显式写，`memory` 也不能省。空体写 `(clobber: memory)`。

---

### The_world 之后程序不再回收内存

`The_world (stop)` 记停表深度，必须配对的 `(start)` 才恢复：

```asm
  = The_world (stop)
  # ... 批量构造 ...
  = The_world (start)
```

两层 `stop` 需两次 `start`。块退出时深度不为零会被自动清零并给 warning——所以跨块泄露不会拖死整棵树，但**同一块里忘了 `start`，后面的代码就一直不回收**。

`#gc = mark` 下可用 `The_world (force)` 在停表期间强制执行一次回收，这是排查误用的手段。

---

### father must refer to up

```asm
# 错
father = parent

# 对
father = (up)
```

括号可省（`father = up` 也合法），但右边必须是 `up`。

---

### invalid start declaration

常见原因：
- 块名以数字开头（`start(normal) = {1}`）
- 第一个参数不是 `loop` / `normal`
- `start` 后面没紧跟 `{`

```asm
start(normal) = {worker}
{
}
```

---

### variable 'x' is already declared

`var` 只能声明一次，之后用 `push`。

```asm
var counter = {"0"}
push ({counter} = {"1"})    # 用 push，不要再 var
```

---

## 三、流程控制

### Loop_Deadlock: dog(x) expected 'stop', last block status was 'ok'

块自然结束是 `ok`，不是 `stop`。

```asm
# 方案一：让块显式停
start(normal) = {worker}
{
    print (<{hi}>)
    if up = (ok) goto (to = stop)
}
dog(worker) = {stop}

# 方案二：白名单，两种都放行
dog(worker) = (pass : ok) (stop : stop)
```

---

### jump is only valid inside an execution block

`jump(行号)` 用**物理行号**（含空行和注释），且不能跳进 `if` / `while` / 其他块内部。

**加一行注释，整段就错位。** 跳到自己那一行会死循环。

---

### loop iteration limit exceeded

命令行加了 `--max-loop-iterations`，检查终止条件或放宽：

```powershell
python -m cosmic_interpreter.cli 程序.cos --max-loop-iterations 10000000
```

---

## 四、数学

### invalid math expression: unsupported arithmetic operator

用了不支持的运算符。

```asm
# 错
math_run 16 ^ 2 = ?          # ^ 不是幂运算符

# 对
math_run 16 ** 2 = ?
```

**支持的运算符**：`+ - * / // % **`、`& | ^ ~ << >>`（需整数模式）、`== != < <= > >=`
**不支持的**：`^` 表示幂——它现在是按位异或

---

### int32 mode requires an integer result / int64 arithmetic overflow

```asm
# 需要小数
math_run {a} / {b} = ? -float64

# 需要更大整数
math_run 2 ** 200 = ? -bigint

# 需要 32 位回绕（SHA/AES）
#%math = uint32
math_run {a} + {b} = ? -uint32
```

---

### complex() requires complex math mode

```asm
#%math = complex
#math
call = (math)
math_run complex(3, 4) = ? -complex
```

或者单条覆盖：`math_run complex(3,4) * 2 = ? -complex`

---

### array values cannot be used as scalar math_run results

矩阵/数组函数返回的是**数组**，不能当标量。

```asm
# 错
math_run matrix_mul(A, B) = ? -float64

# 对
array C = matrix_mul(A, B)
math_run matrix_at(C, 0, 0) = ? -float64
```

---

### trapz / simpson 参数顺序错

```
trapz step must be a finite number
simpson step must be a finite number
```

签名是 **`(样本数组, 步长)`**，不是 `(xs, ys)`。

```asm
array ys = [0, 0.0625, 0.25, 0.5625, 1.0]
math_run trapz(ys, 0.25) = ?       # 0.34375
math_run simpson(ys, 0.25) = ?     # 0.333333333333333
```

> `simpson` 要求奇数个样本且至少 3 个。

---

### tape 相关

```
tape 'x' is not declared
tape symbol must be in range 0..255
tape cells currently accept integer symbols
```

```asm
tape_create (t)
tape_write (t) = {5}          # 只能是 0..255 的字面量整数
tape_move (t) = (right)
tape_read (t) = ?
```

`tape_write` **不接受变量**，只能写字面量。

---

## 五、函数

### invalid function definition / function name is reserved

```asm
function gcd(a, b) = choose(b == 0, a, gcd(b, a % b))
```

限制：
- 函数体是**单个表达式**，不能写块
- 参数名唯一
- 不能重名，不能占用内置名（`abs` `sqrt` `strlen` `dot` 等）
- 支持递归，但阶乘到 21 就超 int64

---

## 六、并发（v4）

### component 'atom' has not been imported

```asm
#atom
call = (atom)
```

---

### invalid lock; expected lock {block} [-timeout N]

```asm
lock {worker}
{
}

lock {worker} -timeout 0.5
{
}
```

`lock` 锁的是**父块上下文里的变量**，不是块本身。

---

### lock timeout

锁被别的块占用，超过指定秒数。超时会计入 `err_count`。

检查：有没有块持锁未释放（没写 `dog` 收尾、或块异常退出）。

---

### 主程序跑完了但块没执行

**`nuclear_reactor` 是异步的。** 主程序会立刻往下跑并退出，队列里的块还没轮到。

```asm
# 错——dog 之前程序就结束了
nuclear_reactor {main} = {chain}

# 对——用 dog 或 lock 等
nuclear_reactor {main} = {chain}
dog(second) = {ok}
```

---

### n-bomb 后 dog 不匹配

```
Loop_Deadlock: block 'worker' expected 'ok', last block status was 'stopped'
```

被强杀的块状态是 `stopped`，不是 `error`。

```asm
n-bomb {worker}
dog(worker) = {stopped}
```

---

## 七、DLL

### component 'dll' has not been imported

```asm
#dll
call = (DLL)
```

---

### unknown DLL type / unknown DLL return type

```asm
#extern messagebox (PTR, STR, STR, UINT) -> INT
#link user32
```

支持类型：`VOID INT UINT LONG DWORD WORD BYTE CHAR PTR STR FLOAT DOUBLE`

---

### cdll 调用

```asm
calldll = (user32)
cdll = come (user32) (messagebox) (0) ({text}) ({title}) (0)
cdll = status (user32)      # 返回 loaded / unloaded / missing
cdll = out (user32)         # 卸载，不接受参数
```

---

## 八、输出与编码

### print 未定义变量

**不报错，但无输出，`up` 状态是 `error`。**

```asm
print (<{maybe}>)
math_run err_count() = ? -int64
if up = (ok) goto (push ({errors} = {(in = up)}))
```

---

### cannot print using GBK

变量值里有目标编码表示不了的字符。

```asm
print (<{text}>)            # 用变量声明的编码
print (<{text}>) -UTF8      # 显式覆盖
```

> 变量声明是 `#text = GBK` 又在 UTF-8 终端跑，会乱码。两者只能选一。

---

## 九、编译相关

### GCC failed with exit code 1

看 `.c` 文件。编译器会把生成的 C 保留下来（默认和 exe 同目录）。

```powershell
python -m cosmic_interpreter.compiler 程序.cos -o out.exe --c-output out.c
```

常见原因：
- 用了 `-bigint` 但表达式里有浮点
- DLL 签名和实际函数不匹配

---

### 32 位链接失败

```
skipping incompatible ... libmingw32.a
cannot find -lmingw32
```

本机的 mingw 是 `x86_64-w64-mingw32`，**只有 64 位库**。要编 32 位得另装 i686 工具链。

---

## 十、v4 / v5 已修复的旧问题

如果还遇到以下问题，说明版本不对：

| 旧问题 | 修复版本 |
|---|---|
| `push ({a} = {b})` 不复制值，b 变成字面量 | v4 |
| `array a = array_set(a,1,99)` 解释器拒绝 | v4 |
| `exit()` 未实现 | v4 |
| 失败无法判断 | v4（新增 `err_count()`） |
| `print` 未定义变量两版不一致 | v4（统一为静默 + error） |
| `print_value` 缺 fflush | v4 |
| bigint 只有解释器支持 | v5（编译版已支持） |
| 无位运算 | v5 |
| 无字节数组 | v5 |
| 矩阵只有 2x2 | v5（任意 NxM） |

---

## 十一、快速对照

| 想做什么 | 正确写法 |
|---|---|
| 声明字符串 | `var a = {"text"}` |
| 赋值 | `push ({a} = {"text"})` |
| 取上一行结果 | `{(in = up)}` |
| 拼接 | `{a} (and) {b}` |
| 条件 | `if ({a} > {b}) { } else { }` |
| 状态判断 | `if up = (ok) goto (...)` |
| 循环 | `while ({i} < 10) { }` |
| 结束块 | `if up = (ok) goto (to = stop)` |
| 函数 | `function f(x) = x * 2` |
| 数组 | `array a = [1,2,3]` |
| 字节 | `bytes b = [0x00, 0xFF]` |
| 矩阵 | `array M = [[1,2],[3,4]]` |
| 加锁 | `lock {block} { }` |
| 强杀 | `n-bomb {block}` |
| 执行链 | `proton {c} = {a} -> {b}` |
| 幂运算 | `**`（不是 `^`） |
| bigint 除法 | `//`（不能 `/`） |
| 32 位回绕 | `#%math = uint32` |

---

*本文所有报错信息从 `parser.py` 的 61 条 `ParseError` 和 `runtime.py` 的 30 多条 `RuntimeError` 里提取，不是凭记忆写的。*
