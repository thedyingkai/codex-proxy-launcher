# Codex Proxy Launcher / Codex 代理启动器

给 Windows 版 Codex 配置代理并启动应用的本地工具，支持自动检测系统代理、识别更新后的安装路径、官方工具代理入口、诊断和配置回退。

面向的问题：Windows 已开启代理，但 Codex 的某些进程没有继承代理，导致 Computer Use、浏览器工具或手机 / 网页 Remote 连接失败；临时修复又容易因安装路径变化或托管配置重新生成而失效。

这是独立项目，不是 OpenAI 官方插件，也不提供代理节点或代理服务。

## 下载与使用

从 [Releases](https://github.com/thedyingkai/codex-proxy-launcher/releases) 下载 `codex-proxy-launcher-1.0.2-windows-x64.zip`，完整解压后双击 `CodexProxyLauncher.exe`。便携包包含运行环境，不需要安装 Python。

1. 确认已安装 Microsoft Store 版 Codex，以及需要使用的官方浏览器 / Computer Use 插件。
2. 首次使用先连接自己的代理软件。默认跟随 Windows 系统代理；系统代理关闭时使用保存的地址，初始值为 `http://127.0.0.1:7890`。
3. 需要自动启动代理软件时，在“代理设置”中选择其 EXE，并勾选自动启动。发布包不预设任何人的代理软件路径。
4. 双击启动器完成检查并启动 Codex。若 Codex 已运行，会提示先结束任务，再正常关闭并重启；已有进程不能接收后来添加的环境变量。

HTTP / mixed 端口可用；当前不支持 SOCKS-only 端口或带用户名密码的代理地址。代理客户端本身仍需连接到有效节点。

请保留完整软件目录，不能只移动 EXE。创建桌面快捷方式可在解压目录运行：

```powershell
.\runtime\python.exe -I -X utf8 .\src\backend.py configure
```

创建后，日常使用桌面的“Codex 代理启动”入口。启动器成功启动 Codex 后退出，不常驻，也不创建计划任务。

## 功能

| 功能 | 行为 |
| --- | --- |
| 配置并启动 | 检测代理、维护工具代理配置、启动当前安装版本的 Codex |
| 仅检查 | 检查代理 TLS/HTTP、官方 MCP 初始化和当前 Local Work 执行器日志，不重启应用 |
| 代理设置 | 选择自动 / 固定代理，以及代理软件路径 |
| 打开日志 | 查看软件目录中的诊断结果 |
| 撤销工具配置 | 恢复安装前的独立工具条目，保留用户后来修改的其他 Codex 设置 |

如果应用存在保存提示或保持后台运行，正常关闭请求可能无法完成。此时需要手动退出 Codex，启动器不会强制终止它或 Edge。

## 工作方式

- 每次查询 Windows 当前注册的 Codex 安装目录，避免把某次更新的版本目录写死。
- 给 Electron 设置 `--proxy-server` 和本机地址绕过规则；后台继承 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`WS_PROXY`、`WSS_PROXY` 及 `NODE_USE_ENV_PROXY=1`。
- 自动识别官方工具管理方式：旧版使用独立 `mcp_servers.node_repl_proxy` 条目，读取官方当前路径、参数和环境后加入代理；新版由应用按会话生成工具配置时，使用应用原生入口及主进程代理继承，并移除本启动器已不适用的旧条目。不会重建会话认证或信任设置。
- 官方浏览器服务路径不存在时，只在已安装的 Browser / Chrome 同版本文件 SHA-256 一致时使用该回退文件。
- 自动处理 Store 内置插件带有 Windows 加密属性、Local Work 复制文件失败的问题：从当前注册安装目录读取完整官方插件，逐文件校验后生成普通文件副本。每次启动重新检查当前版本及文件内容，更新后自动生成对应副本，不固定版本号，不需要手工改路径。
- 仅在安装包代码包含内置资源路径配置 `CODEX_ELECTRON_BUNDLED_PLUGINS_RESOURCES_PATH` 时使用该配置；不改写 WindowsApps 或应用源码。此配置是当前应用实现提供的入口，并非公开的稳定 API。
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
  "codex_exe": ""
}
```

Codex 用户配置只改动 `node_repl_proxy` 条目；运行 `configure` 时创建桌面快捷方式。内置插件副本保存在 `state/bundled-resources/`，其路径只传给启动的应用，不设置全局环境变量。副本与安装文件内容一致，旧版本副本保留以免影响仍在运行的进程，不打入公开发布包。运行日志在 `logs/`，回退记录和完整配置备份在 `state/`。完整配置备份可能包含个人信息，分享软件或提交 issue 时请勿上传这些文件。

## 适用范围与验证

- Windows 10 / 11 x64，.NET Framework 4.x；当前实现针对 Microsoft Store 版 Codex 及其官方 `cua_node` 运行时布局。
- 初始验证环境为 Codex 26.924.6891.0、Node 24.21.0；代码动态发现路径，不将这些版本号作为运行条件。
- 已通过 24 项本机测试，包括配置保留与回退、更新目录模拟、子进程环境继承、HTTP CONNECT、实际捕获 Node fetch / HTTP 的代理请求、更新后插件副本自动切换、损坏副本重建、Local Work 状态判断及新旧工具管理方式切换。
- 已验证官方 MCP 初始化和代理传输可达。HTTP 403 / 421 只证明取得 HTTP 响应，不代表登录、API 权限、浏览器控制或 Remote 会话成功。
- 已实测 Codex 从 26.924.6891.0 更新至 26.930.3930.0 后的路径发现、Edge 页面点击、Computer Use 窗口操作；用户确认手机 / 网页 Remote 连入成功。以上与 dots / Work 的 Local Work 执行器分别验证。
- 2026-10-04 应用重启后，Local Work 第一次启动即连接成功，日志记录 `Local Work executor connected to rendezvous`；执行器进程的一条已建立连接指向本地代理。新版官方工具入口也已实际调用成功。未实测 Windows 冷启动，未代替用户操作 dots 的本机授权界面。安装布局或内部接口发生变化时可能需要适配。

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

默认运行独立测试，跳过依赖真实 Codex 的两项检查。在已安装并运行 Codex 的本机，可启用全部 24 项：

```powershell
$env:CODEX_PROXY_LIVE_TESTS = '1'
python -X utf8 .\src\test_backend.py
Remove-Item Env:\CODEX_PROXY_LIVE_TESTS
```

第三方运行环境的来源和许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
