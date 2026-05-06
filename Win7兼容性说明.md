# ChinanTool Windows 7 兼容性说明

## 概述

ChinanTool 已针对 Windows 7 SP1 进行兼容性适配，同时保持对 Windows 10/11 的完全兼容。本文档记录所有 Win7 兼容性问题及解决方案。

---

## 环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 7 SP1 / Windows 10 / Windows 11 |
| Python 版本（开发） | **Python 3.8.10**（Win7 支持的最后一个版本） |
| PyInstaller | 6.x |
| Inno Setup | 6.x |

---

## 已解决的兼容性问题

### 1. Python 版本限制

**问题**：Python 3.9+ 不再支持 Windows 7。

**解决**：开发环境固定使用 Python 3.8.10。

---

### 2. `logging.basicConfig(encoding='utf-8')` 语法

**问题**：`encoding` 参数是 Python 3.9+ 新增的，Python 3.8 不支持。

**解决**：使用 `codecs.open()` + `StreamHandler` 代替：

```python
# 不兼容 Python 3.8
logging.basicConfig(encoding='utf-8', ...)

# 兼容方案
import codecs
log_stream = codecs.open(log_file, mode='a', encoding='utf-8')
handler = logging.StreamHandler(log_stream)
handler.setFormatter(logging.Formatter(log_format))
```

**涉及文件**：[main.py](file:///e:/程序/chinantool/backup_20260421_2243/main.py#L63)

---

### 3. 类型注解语法（`X | None`、`dict[str, Any]`）

**问题**：`X | None` 联合类型和 `dict[str, Any]` 内置泛型是 Python 3.10+/3.9+ 语法。

**解决**：在文件头部添加 `from __future__ import annotations`，使类型注解延迟为字符串，在运行时不求值：

```python
from __future__ import annotations

# 以下语法在 Python 3.8 中可正常使用
def func(x: dict[str, Any]) -> list[str] | None:
    ...
```

**涉及文件**：[websocket_server.py](file:///e:/程序/chinantool/backup_20260421_2243/websocket_server.py#L8)

其他文件使用 `typing` 模块的传统写法（`Dict[str, Any]`、`Optional[X]`、`List[str]`）。

---

### 4. PyInstaller 未收集 `_overlapped` 模块

**问题**：PyInstaller 默认不会收集 Windows asyncio 的 C 扩展 `_overlapped.pyd`，导致 Win7 上 `asyncio` 无法正常工作。

**解决**：后端打包时添加 `--collect-all asyncio` 参数：

```powershell
pyinstaller ... --collect-all asyncio ... main.py
```

---

### 5. `--noconsole` 模式下 `sys.stdout`/`sys.stderr` 为 None

**问题**：使用 `--noconsole` 打包时，PyInstaller 的 `runw.exe` 会将 `sys.stdout` 和 `sys.stderr` 设为 `None`，uvicorn 调用 `.isatty()` 时会崩溃。

**解决**：在 `main()` 函数开头使用 `NullWriter` 替代：

```python
if getattr(sys, 'frozen', False):
    if sys.stdout is None or sys.stderr is None:
        class NullWriter:
            def write(self, *args): pass
            def flush(self): pass
            def isatty(self): return False
        if sys.stdout is None:
            sys.stdout = NullWriter()
        if sys.stderr is None:
            sys.stderr = NullWriter()
```

**涉及文件**：[main.py](file:///e:/程序/chinantool/backup_20260421_2243/main.py#L91-L100)

---

### 6. 日志文件写入权限

**问题**：程序安装到 `C:\Program Files (x86)\` 后，普通用户无写入权限。

**解决**：日志文件写入用户可写的 `%LOCALAPPDATA%\ChinanTool\` 目录：

```python
local_app = os.environ.get('LOCALAPPDATA', '')
if local_app:
    log_dir = os.path.join(local_app, 'ChinanTool')
else:
    log_dir = get_base_dir()
os.makedirs(log_dir, exist_ok=True)
```

**涉及文件**：[main.py](file:///e:/程序/chinantool/backup_20260421_2243/main.py#L55-L60)、[launcher.py](file:///e:/程序/chinantool/backup_20260421_2243/launcher.py#L32-L38)

---

### 7. PyInstaller 临时目录路径获取

**问题**：打包后 `__file__` 指向 PyInstaller 的临时解压目录，而非 exe 所在目录。

**解决**：使用 `get_base_dir()` 函数统一获取路径：

```python
def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))
```

**涉及文件**：[main.py](file:///e:/程序/chinantool/backup_20260421_2243/main.py#L27-L30)、[launcher.py](file:///e:/程序/chinantool/backup_20260421_2243/launcher.py#L25-L28)、[api_server.py](file:///e:/程序/chinantool/backup_20260421_2243/api_server.py#L38-L41)、[websocket_server.py](file:///e:/程序/chinantool/backup_20260421_2243/websocket_server.py#L49-L52)

---


### 8. websockets 库版本兼容

**问题**：不同版本的 websockets 库 API 不同（`state` 属性 vs `closed` 属性）。

**解决**：使用兼容函数统一处理：

```python
def is_ws_closed(websocket):
    if hasattr(websocket, 'state'):
        return websocket.state == WsState.CLOSED
    return websocket.closed
```

**涉及文件**：[websocket_server.py](file:///e:/程序/chinantool/backup_20260421_2243/websocket_server.py#L35-L42)

---

### 9. librouteros 模块可选导入

**问题**：librouteros 可能未安装或打包不完整。

**解决**：使用 try/except 进行可选导入：

```python
try:
    from librouteros import connect as librouteros_connect
except ImportError:
    librouteros_connect = None
```

**涉及文件**：[websocket_server.py](file:///e:/程序/chinantool/backup_20260421_2243/websocket_server.py#L23-L27)

---

## 已知但无害的问题

### 1. 退出时 "Failed to remove temporary directory" 弹窗

**现象**：Win7 上关闭程序时可能弹出警告窗口：
```
Failed to remove temporary directory:
C:\Users\...\AppData\Local\Temp\_MEI31202
```

**原因**：PyInstaller 的 bootloader（`runw.exe`）在程序退出时尝试清理临时目录，但 Win7 的文件锁定机制与 Win10/11 不同，导致临时目录中的某些文件（如 DLL）在程序退出时仍被占用，无法删除。

**影响**：仅弹窗提示，不影响程序功能。Win10/11 上无此问题。

**处理方式**：点击"确定"关闭即可，无害。

---

## Python 3.8 兼容性语法对照表

| Python 3.9+ 语法 | Python 3.8 替代方案 |
|-----------------|-------------------|
| `logging.basicConfig(encoding='utf-8')` | `codecs.open()` + `StreamHandler` |
| `X \| None`（联合类型） | `Optional[X]`（需 `from typing import Optional`） |
| `dict \| dict`（字典合并） | `{**d1, **d2}` 或 `d1.copy(); d1.update(d2)` |
| `list[int]`（内置泛型） | `List[int]`（需 `from typing import List`） |
| `dict[str, Any]`（内置泛型） | `Dict[str, Any]`（需 `from typing import Dict`） |

> **注意**：如果文件头部有 `from __future__ import annotations`，则类型注解中的 `dict[str, Any]`、`X | None` 等语法在 Python 3.8 中也可以正常使用（注解会被延迟为字符串，运行时不求值）。

---

## 打包命令参考

### Win7 兼容的后端打包

```powershell
pyinstaller --noconfirm --onefile --noconsole `
    --name "chinantool_backend" `
    --icon "logo.ico" `
    --collect-all asyncio `
    --hidden-import "uvicorn.logging" `
    --hidden-import "uvicorn.loops" `
    --hidden-import "uvicorn.loops.auto" `
    --hidden-import "uvicorn.protocols" `
    --hidden-import "uvicorn.protocols.http" `
    --hidden-import "uvicorn.protocols.http.auto" `
    --hidden-import "uvicorn.protocols.websockets" `
    --hidden-import "uvicorn.protocols.websockets.auto" `
    --hidden-import "uvicorn.lifespan" `
    --hidden-import "uvicorn.lifespan.on" `
    --hidden-import "yaml" `
    --hidden-import "routeros_api" `
    --hidden-import "routeros_api.api_structure" `
    --hidden-import "routeros_api.resource" `
    --hidden-import "websockets" `
    --hidden-import "websockets.legacy" `
    --hidden-import "websockets.legacy.server" `
    --hidden-import "psutil" `
    --hidden-import "PIL" `
    --hidden-import "librouteros" `
    --hidden-import "librouteros.api" `
    --hidden-import "librouteros.query" `
    --hidden-import "librouteros.helpers" `
    main.py
```

### 启动器打包

```powershell
pyinstaller --noconfirm --onefile --noconsole `
    --name "ChinanTool" `
    --icon "logo.ico" `
    --hidden-import "yaml" `
    launcher.py
```

---

## 日志文件位置

| 日志类型 | 路径 |
|---------|------|
| 后端日志 | `%LOCALAPPDATA%\ChinanTool\chinantool.log` |
| 启动器日志 | `%LOCALAPPDATA%\ChinanTool\launcher.log` |

---

## 更新记录

| 日期 | 内容 |
|------|------|
| 2026-05-05 | 初始版本，记录所有 Win7 兼容性问题及解决方案 |
