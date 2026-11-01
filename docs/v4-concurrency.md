# v4：并发与 C DLL


### 并发组件 `#atom`

```cos
#atom
call = (atom)

start(normal) = {worker}
{
    lock {worker}
    {
        math_run {counter} + 1 = ? -int64
        if up = (ok) goto (push ({counter} = {(in = up)}))
    }
}

proton {chain} = {worker} -> {worker} -> {worker}
nuclear_reactor {main} = {chain}
dog(worker) = (pass : ok) (error : error)
```

- `lock {块} [-timeout 秒] { ... }` 锁住该块的父上下文变量，退出时释放；同块可重入；`-timeout` 超时返回 `error`。

  > 编译版把 `-timeout 秒` 换算成约 `秒×1000` 次 1 毫秒轮询。由于 Win32 `CRITICAL_SECTION` 与递归 pthread 互斥量对同线程可重入，超时只在**另一个线程正持锁**时才会触发；同线程内重复加锁会立即成功。
- `n-bomb {all}` / `n-bomb {块}` 强杀：状态置为 `stopped`，已写入的变量保留，不回滚。
- `neutron {父} = {子}` 运行时挂父块；`father = up` 仍是编译期复制上下文，两者互补。
- `proton {链} = {块1} -> {块2}` 定义执行链；`nuclear_reactor {父} = {链}` 点火，立即返回。
- 需要结果时用 `dog(块)` 等待其结束，或 `lock` 同步。

`dog` 对并发块可见的状态：`idle`（排队/未启动）、`running`、`ok`、`stop`、`error`、`stopped`（被强杀）。

解释器用真实线程实现；GCC 后端 Windows 用 Win32 线程、其他系统用 pthread（编译时自动加 `-pthread`）。

### C DLL 组件 `#dll`

```cos
#dll
call = (DLL)
#extern gettickcount () -> DWORD as GetTickCount
#link kernel32

calldll = (kernel32)
cdll = status (kernel32)
cdll = come (kernel32) (gettickcount)
cdll = out (kernel32)
```

- `#extern 名称 (类型, ...) -> 类型 [as 真实导出名] [stdcall]` 声明签名；默认 `cdecl`，Win32 API 通常需要 `stdcall` 或 `as` 指定真实导出名。
- 类型：`VOID INT UINT LONG DWORD WORD BYTE CHAR PTR STR FLOAT DOUBLE`（另接受 `LPCSTR HWND`）。
- `calldll = (库)` 加载（Windows `LoadLibrary`，Linux `dlopen`）；`cdll = out` 卸载；`cdll = status` 返回 `loaded` / `unloaded` / `missing`。
- 参数用括号传递：`cdll = come (库) (函数) (参数1) (参数2)`，短横线只留给编码与模式标志。
- `VOID` 返回固定为 `ok`；库/函数/参数错误返回 `error` 且 `err_count()` 递增。

**只加载你信任的 DLL**，风险与 `bash =` 命令相同。

### 行为对齐修复

- `array a = array_set(a, 1, 99)` 允许重绑定具名数组，解释器与编译版一致。
- `print` 未定义变量：两版都静默、状态为 `error`，并计入 `err_count()`。
- 新增 `exit(状态码)`：解释器由 CLI 作为退出码返回；编译版直接退出进程。
- `err_count()` 返回累计错误数；编译版输出已加 `fflush`，重定向到文件不会丢内容。

