# AutoCopySel

后台常驻，鼠标划词即自动复制。Windows / macOS 通用，Python 实现，无窗口、只有托盘图标。

---

## 一、GitHub 同类项目调研（2026-10）

结论：**「划词即复制」这个需求在开源世界里是碎片化的**——macOS 上有人写过单点方案，Windows 上基本只有 PopClip 仿制品，**没有一个是 Windows + macOS 同一个代码库、纯自动复制、零 UI 干扰的**。所以值得自己写。

| 项目 | 平台 | 语言 | 星 | 状态 | 说明 |
|---|---|---|---|---|---|
| [0xfullex/selection-hook](https://github.com/0xfullex/selection-hook) | Win/Mac/Linux | C++ / Node | 115 | 活跃（2026-10 仍在更新） | 最相关。开源界第一个跨平台「取选区文本」原生库，走无障碍 API，MIT。**但只提供库，没有成品应用**，且绑定 Node/Electron |
| [Mazide/marker](https://github.com/Mazide/marker) | 仅 macOS | Swift | 4 | 新项目 | 思路几乎就是你要的：选区自动捕获 + 历史 + ⌥V 粘贴。**仅 macOS、仅 Swift、无 Windows** |
| [millionart/WinPopclip](https://github.com/millionart/WinPopclip.ahk) | 仅 Windows | AutoHotkey | 118 | 2025-06 后无更新 | Windows 上的 PopClip 仿制品，弹工具栏而非自动复制，依赖装 AHK |
| [sakeven/Selected](https://github.com/sakeven/Selected) | 仅 macOS | Swift | 115 | 活跃 | 划词工具栏 + 剪贴板历史，功能重，非「静默复制」定位 |
| [One-Clip/OneClip](https://github.com/One-Clip/OneClip) | 仅 macOS | Swift | 517 | 活跃 | 剪贴板管理器顺带做划词翻译，不是自动复制 |
| [pilotmoon/PopClip-Extensions](https://github.com/pilotmoon/PopClip-Extensions) | 仅 macOS | JS | 2015 | 活跃 | PopClip 本体闭源，这里只有插件目录 |
| [KubaDee/SelectToClipboard](https://github.com/KubaDee/SelectToClipboard) | Notepad++ | C | 21 | 2022 停更 | 只在编辑器里生效，不是全局工具 |
| [changkun/midgard](https://github.com/changkun/midgard) | Mac/Linux/Win | Go | 228 | 活跃 | 剪贴板同步，不做「划词自动复制」 |

技术要点（从上面的项目里验证过的）：

1. **macOS 正确姿势是 Accessibility API**（`AXSelectedText`），不碰剪贴板、不惊动源应用。缺点是个别应用（Telegram、kitty 终端、部分 Electron）不暴露选区，需要退回模拟 `Cmd+C`。
2. **模拟按键必须做剪贴板差量判定**：先记 `changeCount` / `GetClipboardSequenceNumber`，发键，轮询是否变化；没变化说明没选到东西，要把旧内容写回去，否则用户剪贴板就被清了。
3. **终端不能发 `Ctrl+C`**（Windows 上是 SIGINT，会打断进程）。所以内置了终端黑名单。
4. Windows 没有全局「读选区」接口，只能走 `Ctrl+C` + 剪贴板判定。这就是为什么开源方案在 Windows 上都用 AHK / 按键模拟。

本项目把上面 4 点全实现了，同一个代码库同时跑两个平台。

---

## 二、它是怎么工作的

```
鼠标左键按下 ──► 记录坐标/时间
        │
        ▼
   松开：位移 > 6px 且按住 > 80ms  → 判定为「拖选」
         或 0.55s 内同位置连点 2 次 → 判定为「双击选词」
        │
        ▼
   等待 130ms（让应用把选区定下来）
        │
        ├─► ① 无障碍接口直读 AXSelectedText（不碰剪贴板）
        │        读到了 → 写入剪贴板 ✅
        │        没读到 ↓
        └─► ② 前台应用在黑名单？→ 放弃
                 否则：记剪贴板版本号 → 发 Cmd+C / Ctrl+C
                       → 版本号变了 = 成功（剪贴板已经是目标文本）
                       → 没变 = 失败，还原旧剪贴板
        │
        ▼
   托盘图标闪一下绿点 / 可选提示音 → 结束
```

为什么要「直读优先」：直接读选区时**不写剪贴板中间态**，不会打断你在别处正在进行的复制粘贴，也不会把富文本降级成纯文本两遍。只有直读失败才退化成按键方案。

---

## 三、安装与运行

### macOS

```bash
cd AutoCopySel
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python run.py --check     # 先自检，会告诉你缺什么权限
python run.py             # 启动，菜单栏出现图标
```

首次运行后必须授权：

**系统设置 → 隐私与安全性 → 辅助功能** → 勾选「终端」（源码运行）或「AutoCopySel」（打包后）。
没授权的话，既读不到选区也发不出 `Cmd+C`。

> **授权后请重启一次程序**。macOS 不会把新授予的辅助功能权限热更新给已经启动的进程——监听器还是建不起来的。

打包成 .app：

```bash
./build_macos.sh
cp -R dist/AutoCopySel.app /Applications/    # 先搬到固定路径，再授权
open /Applications/AutoCopySel.app
```

打包后的 app 是 `LSUIElement`（后台程序），不占 Dock，只在菜单栏。授权对象变成 `AutoCopySel.app` 本身。

> **先挪位置再授权**。macOS 的辅助功能授权是按路径绑定的，ad-hoc 签名的 app 尤其如此。在 `dist/` 里授权完又挪到 `/Applications`，会被要求重新授权一次。重新打包同理——换了个二进制就得再勾一次。

### Windows

```bat
cd AutoCopySel
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

py run.py --check
py run.py
```

无需额外授权。打包成单个 exe：

```bat
build_windows.bat
```

产物在 `dist\AutoCopySel.exe`，双击即用。

首次双击可能被 SmartScreen 拦（未签名），点「更多信息 → 仍要运行」。另外：**目标应用如果是以管理员身份运行的，普通权限的 AutoCopySel 发不出 `Ctrl+C`**。要覆盖这种场景，把 AutoCopySel 也设成以管理员身份启动。

### 命令行自检

```bash
python run.py --check      # 平台 / 权限 / 前台应用 / 剪贴板状态
python run.py --capture    # 3 秒倒计时后取一次词，打印结果（用于排查）
python run.py --no-tray    # 不要托盘，纯命令行跑
python run.py -v           # 调试日志
```

---

## 四、托盘菜单

| 菜单项 | 作用 |
|---|---|
| ● 运行中 / ○ 已暂停 | 单击切换（也是左键点击图标的默认动作） |
| 立即复制当前选中 | 手动触发一次 |
| 最近复制 | 最近 10 条，点一下写回剪贴板 |
| 取词方式 | 自动 / 仅无障碍接口 / 仅模拟按键 |
| 提示音 | 复制成功放一声 |
| 开机自启 | macOS 写 LaunchAgent，Windows 写注册表 Run 项 |
| 打开辅助功能设置 | 直接跳到 macOS 权限页 |
| 修复权限（重置授权记录） | `tccutil reset` + 打开设置页，用于「勾了却没反应」 |
| 运行诊断 | 弹窗显示完整自检报告 |
| 打开配置目录 | `~/.autocopysel` |
| 退出 | 关闭 |

状态行本身会随实际情况变化：`● 运行中` / `○ 已暂停` / `⚠ 缺少辅助功能权限 — 点此去授权` / `⚠ 事件监听未运行 — 点此看诊断`。

---

## 五、配置

配置文件：`~/.autocopysel/config.json`（改完重启生效）。

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 总开关 |
| `drag_threshold_px` | `6` | 拖动多少像素算划词，手抖容易误触就调大 |
| `press_min_ms` | `80` | 按住时间下限（毫秒） |
| `delay_ms` | `130` | 松手后等多久取词，慢应用可调到 200–300 |
| `capture_mode` | `"auto"` | `auto` / `ax` / `key` |
| `detect_multi_click` | `true` | 双击/三击选词也触发 |
| `ignore_with_modifiers` | `true` | 按住 Ctrl/Cmd/Win 拖动不触发 |
| `min_length` / `max_length` | `1` / `100000` | 长度过滤，超长截断 |
| `trim` | `true` | 去掉首尾空白和换行 |
| `sound` | `false` | 提示音 |
| `flash_icon` | `true` | 托盘图标闪烁反馈 |
| `history_size` | `20` | 内部历史条数 |
| `debounce_ms` | `220` | 两次取词最小间隔 |
| `timeout_ms` | `450` | 等剪贴板变化的最长时间 |
| `app_blacklist` | 见下 | 黑名单，命中则不启动按键模拟 |
| `autostart` | `false` | 开机自启 |

默认黑名单（终端类，防止 `Ctrl+C` 变成 SIGINT）：

- macOS：`com.apple.Terminal`、`com.googlecode.iterm2`、`net.kovidgoyal.kitty`、`io.alacritty`、`com.mitchellh.ghostty`、`org.wezfurlong.wezterm`、`dev.warp.Warp-Stable` …
- Windows：`windowsterminal.exe`、`cmd.exe`、`powershell.exe`、`pwsh.exe`、`mintty.exe` …

注意：黑名单**只拦按键模拟**。终端里如果无障碍接口能直接读到选区，一样会正常复制（macOS 的 Terminal 就支持）。

日志：`~/.autocopysel/app.log`，超过 2MB 自动轮转。

---

## 六、已知限制

1. **纯文本**。直读接口理论上能拿 RTF/HTML，当前只写纯文本。需要保留格式的话得改成 `NSPasteboard` 多类型写入。
2. **Windows 无可读接口**，只能 `Ctrl+C`。副作用：源应用会收到一次真实的复制事件；剪贴板会短暂出现目标文本（这正是需求本身）。
3. **macOS 部分应用读不到选区**（Telegram、kitty、少数 Electron 应用），自动走按键兜底；两者都失败的会被跳过。
4. **远程桌面 / 虚拟机内**的选区捕获不到，因为事件发生在宿主系统。
5. 打包后首次运行，macOS 可能提示「无法验证开发者」，去 **系统设置 → 隐私与安全性 → 仍要打开**。
6. 权限是按「可执行文件」授予的：源码运行授权给终端，打包运行授权给 .app，**两者权限不互通**。换方式运行要重新授权。

---

## 七、授权了却没反应？——这是必读的一节

### 症状

辅助功能已经勾上，托盘图标也在，但在任何软件里选文本都毫无反应。

### 真实原因（已定位，有日志和 TCC 库为证）

```
14:54:09  service started          ← 程序启动
14:54:09  permission: False        ← 此刻还没授权
14:54:25  （TCC 库里写入授权记录）   ← 16 秒后用户才勾选
```

`pynput` 在 macOS 上创建事件监听时，如果没有辅助功能权限，`CGEventTapCreate()`
返回 `None`，监听线程会**直接 return 退出——不抛异常、不打日志**（pynput 源码行为）。
于是：

- 监听线程在授权之前就已经死了；
- 之后授权成功，**没有任何机制去重建这个监听器**；
- 结果：程序活着、图标正常、但永远收不到鼠标事件 → 永久失效。

这不是「授权没生效」，是「授权太晚，监听器已经死了且不会复活」。

### 1.1.0 的修复

| 改动 | 作用 |
|---|---|
| 新增 `health.Guardian` 守护线程，每 1.2 秒检查权限 + 监听器存活 | 权限一到位就自动重建监听器，**不用重启程序**，2 秒内生效 |
| 监听器启动返回值显式检查 | 不再静默失败，失败原因写进日志 |
| 缺权限时图标变红色感叹号 + 标题写明原因 + 启动弹窗（带「去授权」按钮） | 一眼看出「程序在，但不工作」 |
| 菜单新增「运行诊断」「修复权限（重置授权记录）」 | 自己就能定位问题 |
| 新增 `--doctor` / `--reset-permission` 命令 | 命令行排查 |

### 自己排查的三条命令

```bash
# 完整诊断（含决定性的事件 tap 自检）
/Applications/AutoCopySel.app/Contents/MacOS/AutoCopySel --doctor

# 清掉旧授权记录并打开设置页（重建过 app 就用这个）
/Applications/AutoCopySel.app/Contents/MacOS/AutoCopySel --reset-permission

# 看日志
tail -20 ~/.autocopysel/app.log
```

`--doctor` 里最关键的是一行 **「事件监听自检」**：

- `✓ 事件 tap 可建立` → 权限真的生效了；
- `✗ 无法建立事件 tap → 辅助功能授权对当前程序未生效` → 授权没落到这份程序上，走下面的重置流程。

### 为什么「勾上了还是不行」

本项目是 ad-hoc 签名（没有开发者证书），macOS 记录的授权凭据是 **cdhash**。
只要你重新打包过，cdhash 就变了，**旧的勾选状态在界面上还亮着，但已经对不上新二进制**——
这就是最坑的一种状态：看着已授权，实际被拒。

解决办法只有一个：清掉旧记录再授权一次。

```bash
tccutil reset Accessibility com.liqin.autocopysel
```

然后重新勾选。菜单里的「修复权限」做的就是这个动作。

---

## 八、自测与修复记录

### 自带测试

```bash
# 逻辑单测：不抢焦点、不写真实剪贴板（18 项）
python tests/test_capture.py

# 端到端：真实鼠标事件 + TextEdit 靶子（16 项，会抢焦点约 25 秒）
python tests/test_e2e.py
```

**跑 `test_e2e.py` 时不要动电脑**：它靠合成鼠标事件驱动，前台必须一直是 TextEdit。
中途切窗口的话，相关项会自动标 `SKIP`（不算失败），但结果就不完整了。
脚本会在开始时保存你的剪贴板、结束时原样还原；`test_capture.py` 则完全不碰真实剪贴板
（用内存假实现），并在最后断言「全程未触碰真实剪贴板」。

端到端覆盖：拖选、双击选词、三击选行、快速甩选、2px 抖动（不应触发）、Cmd+拖动（不应触发）、
空白区拖动（剪贴板必须保住）、监听器重建后不重复触发、非文本选区还原剪贴板。

### 1.1.1 修掉的 bug

| 问题 | 后果 | 修法 |
|---|---|---|
| 快速甩选被丢弃 | 鼠标划一下就松（<80ms）时**完全不复制**，位移再大也没用 | 位移 ≥ 24px 时忽略时长下限 |
| 单实例锁失效 | 连续启动会跑出**两个进程**，每次划词发两遍 `Cmd+C` | 锁文件句柄改为模块级引用（原先条件判断后就被 GC，`flock` 立刻释放） |
| 非文本选区污染剪贴板 | 双击文件夹后，剪贴板被换成文件引用 | 这是自己发 `Cmd+C` 造成的，必须还原原内容 |
| 诊断毁掉非文本剪贴板 | `--doctor` 的写测试会把剪贴板里的图片冲掉 | 检测到非文本内容时跳过写测试 |
| 监听器重建误判失败 | 慢启动时 `restart()` 返回 False，日志刷错误 | 改用「线程持续存活 300ms」判定，不再依赖 `IS_TRUSTED`（它是类默认值 `False`，和「没跑起来」无法区分） |

---

## 九、代码结构

```
AutoCopySel/
├── run.py                     # 入口 / 命令行
├── requirements.txt
├── tests/
│   ├── test_capture.py        # 取词逻辑单测（假后端，不抢焦点）
│   └── test_e2e.py            # 端到端（真实鼠标事件 + TextEdit）
├── build_macos.sh             # 打包 .app
├── build_windows.bat          # 打包 .exe
└── autocopysel/
    ├── app.py                 # Service（取词线程）+ Application（托盘组装）+ 单实例锁
    ├── hook.py                # 全局鼠标监听 → 判定拖选 / 多击选词
    ├── capture.py             # 取词主流程：AX 直读 → 按键兜底 → 剪贴板差量与回滚
    ├── health.py              # 权限守护：权限后补/监听器掉线时自动重建
    ├── diagnostics.py         # 自检：事件 tap 探测、签名、剪贴板、修复建议
    ├── clipboard.py           # 跨平台剪贴板（AppKit / Win32 ctypes / xclip）
    ├── config.py              # 配置 + 开机自启（LaunchAgent / 注册表）
    ├── icons.py               # 托盘图标（Pillow 现画）
    ├── tray.py                # 菜单
    └── backends/
        ├── base.py            # 统一接口 + CaptureResult
        ├── darwin.py          # AXSelectedText（含子节点 BFS 搜索）+ Cmd+C
        ├── win32.py           # 前台进程 + Ctrl+C
        └── linux.py           # X11 PRIMARY 选区（附带）
```

## 十、许可

MIT。按需改。
