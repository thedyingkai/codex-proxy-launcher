# Codex Proxy Launcher / Codex 代理启动器

给 Windows 版 Codex 配置代理并启动应用的本地工具，支持自动检测系统代理、识别更新后的安装路径、官方工具代理入口、诊断和配置回退。

面向的问题：Windows 已开启代理，但 Codex 的某些进程没有继承代理，导致 Computer Use、浏览器工具或手机 / 网页 Remote 连接失败；临时修复又容易因安装路径变化或托管配置重新生成而失效。

这是独立项目，不是 OpenAI 官方插件，也不提供代理节点或代理服务。

**验证状态：1.0.5 已恢复实际 dots 云端任务连接，作为预发布提供；1.0.4 的真实 Edge 验收仍有效。** 2026-10-04，当前 Codex 主进程保持运行，云端 WebSocket 从连续超时变为已连接并完成初始化；两个原任务成功读取，用户确认原来打不开的任务已能打开。Windows 冷启动和下一次真实应用更新仍待验证。

## 下载与使用

从 [1.0.5 预发布](https://github.com/thedyingkai/codex-proxy-launcher/releases/tag/v1.0.5) 下载 ZIP，完整解压后双击 `CodexProxyLauncher.exe`，不需要全局安装 Python。1.0.2 有已知资源与工具代理问题；1.0.3 已撤回。

1. 确认已安装 Microsoft Store 版 Codex，以及需要使用的官方浏览器 / Computer Use 插件。
2. 首次使用先连接自己的代理软件。默认跟随 Windows 系统代理；系统代理关闭时使用保存的地址，初始值为 `http://127.0.0.1:7890`。
3. 需要自动启动代理软件时，在“代理设置”中选择其 EXE，并勾选自动启动。发布包不预设任何人的代理软件路径。
4. 双击启动器完成检查并启动 Codex。若 Codex 已运行，复用现有窗口，不再次调用主程序。需要刷新应用启动环境时，在任务结束后手动退出 Codex，再双击启动器。
5. 若 Edge 正常而 dots 新任务报 `Codex app-server is not available`，且使用 NekoRay / NekoBox 1.6 内核，可在“代理设置”勾选“启用云端任务网络代理”。每次该网络组件尚未运行时，需要 Windows 管理员确认；组件仍运行时直接复用。发布包默认不开启此选项。

HTTP / mixed 端口可用；当前不支持 SOCKS-only 端口或带用户名密码的代理地址。代理客户端本身仍需连接到有效节点。

请保留完整软件目录，不能只移动 EXE。创建桌面快捷方式可在解压目录运行：

```powershell
.\runtime\python.exe -I -X utf8 .\src\backend.py configure
```

创建后，日常使用桌面的“Codex 代理启动”入口。启动器成功启动 Codex 后退出；每个官方工具进程使用一个随连接结束而退出的透明代理入口。可选的云端网络组件留在托盘，供正在运行的 Codex 使用；不创建计划任务，也不定时改写 Codex 配置。

## 云端任务网络代理

某些桌面版本的公网云端 WebSocket 没有使用代理参数或代理环境变量。此时浏览器能操作、HTTPS 探测有响应，也不能证明 dots 的任务连接正常。

可选组件 `CodexCloudProxy.exe` 使用用户已有的 `nekobox_core.exe`，建立临时虚拟网卡 `CodexProxyTun`。系统 IPv4 流量进入该网卡后分流：Codex 的 TCP 连接和云端任务域名经过原有本机 HTTP 代理，其他流量直接转发；本地网络保持直连。TLS 仍由客户端与目标服务器验证和建立，不安装证书、不解密流量、不修改 WindowsApps。

此组件当前仅验证了 NekoBox / sing-box-extra 1.6 内核和 IPv4。代理设置需选择 NekoRay 主程序，其目录旁需有 `nekobox_core.exe`；其他内核会明确拒绝启动，不自动升级代理软件。该限制针对外部代理内核，不与 Codex 的版本目录绑定。IPv6、网络切换、冷启动和后续真实 Codex 更新尚待验证。

关闭方式：从托盘选择“关闭网络代理并恢复路由”，或执行 `CodexCloudProxy.exe --stop`。取消“代理设置”中的云端网络选项也会请求停止。程序先发送正常关闭信号，监督进程还通过 Windows Job Object 管理自己的子进程。正常关闭信号已在独立本机核心进程上验证；没有为了测试回退而中断用户刚恢复的云端连接。

## 功能

| 功能 | 行为 |
| --- | --- |
| 配置并启动 | 检测代理、维护工具代理配置、启动当前安装版本的 Codex |
| 仅检查 | 分别检查代理 TLS/HTTP、MCP 配置、Local Work 和云端任务 WebSocket 的当前日志，不重启应用 |
| 代理设置 | 选择自动 / 固定代理、代理软件路径及可选云端网络组件 |
| 打开日志 | 查看软件目录中的诊断结果 |
| 撤销工具配置 | 恢复安装前的独立工具条目，保留用户后来修改的其他 Codex 设置 |

如果应用存在保存提示或保持后台运行，正常关闭请求可能无法完成。此时需要手动退出 Codex，启动器不会强制终止它或 Edge。

## 工作方式

- 每次查询 Windows 当前注册的 Codex 安装目录，避免把某次更新的版本目录写死。
- 给 Electron 设置 `--proxy-server` 和本机地址绕过规则；后台继承 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`WS_PROXY`、`WSS_PROXY` 及 `NODE_USE_ENV_PROXY=1`。
- 检查当前安装代码是否支持 `CODEX_NODE_REPL_PATH`。支持时，让应用原生的工具配置使用 `CodexNativeProxy.exe`，保留官方生成的参数、可信服务、身份和权限环境，只为真正的官方工具进程补充代理。工具退出后由 Windows Job Object 清理子进程。
- 同时维护独立的 `node_repl_proxy` 连接，复用已经实际接通 Edge 的方式。每次启动都读取当前官方程序和完整环境，再添加代理，不保存某个版本的官方环境快照。应用刷新自己的配置时，这个独立入口仍被保留。
- 官方浏览器服务路径不存在时，只在已安装的 Browser / Chrome 同版本文件 SHA-256 一致时使用该回退文件。
- 自动处理 Store 资源带有 Windows 加密属性、Local Work 复制文件失败的问题：复制并逐文件校验完整的官方资源目录，包括插件、`codex.exe`、浏览器运行环境及依赖。资源路径设置会影响多个组件，不能只复制插件子目录。更新后自动重新发现和生成副本，不固定版本号。当前版本资源约 1.7 GB；首次或更新后需要复制，后续校验复用。
- 仅在安装包代码包含相应资源路径及工具路径入口时使用；不改写 WindowsApps 或应用源码。这些环境变量是当前应用实现提供的入口，并非公开承诺长期不变的 API；后续安装布局变化仍需重新验证。
- 启动后检查当前进程的 Local Work 连接日志；发现失败时显示失败提示，不把代理或 MCP 握手通过当成本地执行器就绪。
- 对配置做解析及语义一致性校验，修改前备份。没有修改插件二进制、认证逻辑、审批策略或证书校验。

依据：[Electron 代理参数](https://www.electronjs.org/docs/latest/api/command-line-switches#--proxy-serveraddressport)、[Node 环境代理支持](https://nodejs.org/download/release/latest-v24.x/docs/api/http.html#built-in-proxy-support)、[Codex MCP 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。

## 配置与数据

便携包中的 `settings.json` 来源于仓库里的 `settings.example.json`。源码仓库不会跟踪本机 `settings.json`、日志、运行状态或配置备份。

```json
{
  "schema_version": 1,
  "proxy_mode": "auto",
  "proxy_url": "http://127.0.0.1:7890",
  "proxy_app_path": "",
  "auto_start_proxy": false,
  "proxy_wait_seconds": 25,
  "codex_exe": "",
  "cloud_network_enabled": false
}
```

配置文件维护本启动器的 `node_repl_proxy` 条目；迁移时清除本安装目录创建的 1.0.3 占位入口并停止其维护进程，不删除真正的官方工具配置。运行 `configure` 时创建桌面快捷方式。完整资源副本保存在 `state/bundled-resources/`，代理入口所需的当前官方路径及校验值保存在 `state/native-target.json`，路径只传给启动的应用，不设置全局环境变量。旧副本保留以免影响运行中的进程，不打入公开包。日志和配置备份可能包含个人信息，不应上传。

## 适用范围与验证

- Windows 10 / 11 x64，.NET Framework 4.x；当前实现针对 Microsoft Store 版 Codex 及其官方 `cua_node` 运行时布局。
- 初始验证环境为 Codex 26.924.6891.0、Node 24.21.0；代码动态发现路径，不将这些版本号作为运行条件。
- 回归检查覆盖配置保留与回退、安装目录变化、完整资源复制、运行文件改变后的缓存失效、代理环境和 Local Work 状态判断。
- 真实官方工具通过新代理入口启动后，返回 `js` 等 4 个工具。只读检查实际官方进程确认代理变量已存在；强制结束测试入口后其测试子进程全部退出。此检查没有操作浏览器。
- 历史版本曾通过 Edge 点击、Computer Use 操作和手机 / 网页 Remote 连入测试；这些结果不能代替 1.0.4 的验收。
- 1.0.4 的默认 `node_repl` 和独立 `node_repl_proxy` 均完成真实 Edge 打开、读取与点击测试，最终到达 IANA 的 Example Domains 页面。测试时间为 2026-10-04 19:00–19:01（北京时间）。
- 当前 Local Work 日志显示已连接。1.0.5 在北京时间 20:26:05 恢复云端任务 WebSocket，两个原任务读取成功，用户确认界面可打开。后续检查仍保持连接。Windows 冷启动和下一次真实应用更新尚未验证；自动发现路径的回归测试不等于所有未来版本的兼容承诺。
- 1.0.5 新增云端连接状态、失效进程记录、代理端口变化及已运行 Codex 不重复启动的回归检查。独立透明转发测试在保留服务器证书验证的情况下到达了任务服务；独立代理核心通过正常关闭信号退出，返回码为 0。
- 本次正常退出请求未使 Codex 主进程退出；期间插件连接已重新加载，随后实际 Edge 验收通过。未强制终止应用，未将该过程记为完整重启成功。
- HTTP 403 / 421 只证明取得 HTTP 响应，不代表登录、API 权限、浏览器控制或 Remote 会话成功。

Remote 仍需在 Codex 中启用，电脑保持在线，设备的登录和配对有效。本工具处理代理配置，不改变 Remote 权限。

## 从源码构建

在 Windows 中运行：

```powershell
.\scripts\package.ps1
```

脚本使用系统 .NET Framework C# 编译器构建界面，下载 `runtime-source.json` 指定的官方 Python 嵌入式包并验证 SHA-256，再生成 `dist/` 下的便携 ZIP 与校验文件。运行环境不需要全局安装，版本固定为 3.14.6。

也可以使用已经下载的运行环境压缩包：

```powershell
.\scripts\package.ps1 -RuntimeArchive 'C:\Downloads\python-3.14.6-embed-amd64.zip'
```

本地测试需要 Windows 和 Python 3.12+：

```powershell
python -X utf8 .\src\test_backend.py
```

默认运行独立测试，跳过依赖真实 Codex 的两项检查。在已安装并运行 Codex 的本机，可启用完整检查：

```powershell
$env:CODEX_PROXY_LIVE_TESTS = '1'
python -X utf8 .\src\test_backend.py
Remove-Item Env:\CODEX_PROXY_LIVE_TESTS
```

第三方运行环境的来源和许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
