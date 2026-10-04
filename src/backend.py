"""Codex Proxy Launcher. Standard library only; never modifies vendor binaries.

GUI actions emit JSON lines. `mcp` exclusively forwards the official MCP stdio.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import ctypes
import datetime as dt
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
from urllib.parse import urlsplit
import winreg

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "settings.json"
STATE = ROOT / "state"
LOGS = ROOT / "logs"
PYTHON = ROOT / "runtime" / "python.exe"
SCRIPT = ROOT / "src" / "backend.py"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
ALIAS = "node_repl_proxy"
NO_PROXY = "localhost,127.0.0.1,::1"
HIDDEN = subprocess.CREATE_NO_WINDOW
VERSION = "1.0.3"
BUNDLED_RESOURCES_ENV = "CODEX_ELECTRON_BUNDLED_PLUGINS_RESOURCES_PATH"
NATIVE_SERVER = "node_repl"
NATIVE_PROXY_REVISION = 1


class LauncherError(Exception):
    pass


def stamp():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".launcher-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def emit(kind, message, **extra):
    print(json.dumps({"type": kind, "message": message, **extra}, ensure_ascii=False), flush=True)


def log(message):
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / (dt.date.today().isoformat() + ".log")).open("a", encoding="utf-8") as f:
        f.write(stamp() + " " + message + "\n")


def progress(message):
    emit("status", message)
    log(message)


def load_settings():
    data = json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
    if data.get("schema_version") != 1:
        raise LauncherError("设置文件版本不受支持。请保留日志并检查 settings.json。")
    return data


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).resolve()


def run_ps(code, extra_env=None, timeout=25):
    # No user-controlled values are inserted into PowerShell source.
    env = os.environ.copy()
    env.update(extra_env or {})
    import base64
    prefix = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
    p = subprocess.run([str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
                        "-EncodedCommand", base64.b64encode((prefix + code).encode("utf-16le")).decode()],
                       capture_output=True, encoding="utf-8", errors="replace", env=env,
                       creationflags=HIDDEN, timeout=timeout)
    if p.returncode:
        raise LauncherError("Windows 查询未完成：" + p.stderr.strip()[:600])
    return p.stdout.strip()


def system_proxy():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            value = winreg.QueryValueEx(key, "ProxyServer")[0]
    except (FileNotFoundError, OSError):
        return None
    if not enabled or not value:
        return None
    if "=" in value:
        pairs = dict(x.strip().split("=", 1) for x in value.split(";") if "=" in x)
        value = pairs.get("https") or pairs.get("http")
    if not value:
        return None
    return value if "://" in value else "http://" + value


def validate_proxy(value):
    if not isinstance(value, str) or any(c in value for c in "\r\n\t "):
        raise LauncherError("代理地址不能包含空白；示例：http://127.0.0.1:7890")
    try:
        u = urlsplit(value)
        if (u.scheme != "http" or not u.hostname or u.username is not None or u.password is not None
                or u.path not in ("", "/") or u.query or u.fragment or not u.port):
            raise ValueError()
        if not 1 <= u.port <= 65535:
            raise ValueError()
    except ValueError:
        raise LauncherError("请填写无账号密码的 HTTP / mixed 代理地址，如 http://127.0.0.1:7890。")
    host = "[" + u.hostname + "]" if ":" in u.hostname else u.hostname
    return "http://" + host + ":" + str(u.port)


def choose_proxy(settings, detected=None):
    mode = settings.get("proxy_mode", "auto")
    if mode not in ("auto", "manual"):
        raise LauncherError("代理模式必须为 auto 或 manual。")
    if mode == "auto":
        detected = system_proxy() if detected is None else detected
        if detected:
            return validate_proxy(detected), "Windows 当前系统代理"
    return validate_proxy(settings.get("proxy_url", "")), "已保存的代理地址"


def proxy_env(proxy, base=None):
    env = dict(os.environ if base is None else base)
    names = {"http_proxy", "https_proxy", "all_proxy", "ws_proxy", "wss_proxy", "no_proxy", "node_use_env_proxy"}
    for key in list(env):
        if key.lower() in names:
            del env[key]
    env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, ALL_PROXY=proxy, WS_PROXY=proxy, WSS_PROXY=proxy,
               NO_PROXY=NO_PROXY, NODE_USE_ENV_PROXY="1")
    return env


def port_open(proxy, timeout=1):
    u = urlsplit(proxy)
    try:
        with socket.create_connection((u.hostname, u.port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_proxy(settings, proxy, allow_start):
    if port_open(proxy):
        return
    app = settings.get("proxy_app_path", "")
    if allow_start and settings.get("auto_start_proxy", False) and app:
        path = Path(app).resolve()
        if not path.is_file() or path.suffix.lower() != ".exe":
            raise LauncherError("代理软件路径不存在，请在“代理设置”中重新选择。")
        existing = run_ps("@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $env:CP_PROXY_APP }).Count",
                          {"CP_PROXY_APP": str(path)})
        if existing.strip() == "0":
            progress("正在启动代理软件，等待本地代理端口就绪……")
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            subprocess.Popen([str(path)], cwd=path.parent, creationflags=HIDDEN,
                             startupinfo=startup, close_fds=True)
        else:
            progress("代理软件已运行，正在等待代理端口……")
        deadline = time.monotonic() + min(max(int(settings.get("proxy_wait_seconds", 25)), 1), 50)
        while time.monotonic() < deadline:
            if port_open(proxy, .6):
                return
            time.sleep(.4)
    raise LauncherError("代理端口未开启。请确认代理软件已连接，或在“代理设置”中改成当前 HTTP / mixed 端口。")


def probe_https(proxy, hostname, timeout=10):
    """Explicit HTTP CONNECT then verified TLS. There is no direct fallback."""
    u = urlsplit(proxy)
    connection = http.client.HTTPSConnection(u.hostname, u.port, timeout=timeout,
                                             context=ssl.create_default_context())
    connection.set_tunnel(hostname, 443)
    started = time.monotonic()
    try:
        connection.request("HEAD", "/", headers={"User-Agent": "CodexProxyLauncher/" + VERSION})
        response = connection.getresponse()
        return {"host": hostname, "reachable": True, "http_status": response.status,
                "seconds": round(time.monotonic() - started, 2),
                "meaning": "TLS 与 HTTP 可达；未验证登录或 Remote 会话"}
    except Exception as error:
        return {"host": hostname, "reachable": False, "error": str(error)[:240],
                "seconds": round(time.monotonic() - started, 2)}
    finally:
        connection.close()


def network_checks(proxy):
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(lambda host: probe_https(proxy, host),
                             ["chatgpt.com", "api.openai.com", "codex-cloud-environments.chatgpt.com"]))


def discover_app(settings):
    override = settings.get("codex_exe", "")
    if override:
        p = Path(override).resolve()
        if not p.is_file() or p.name.lower() not in ("chatgpt.exe", "codex.exe"):
            raise LauncherError("设置中的 Codex 桌面程序路径无效。")
        return p
    raw = run_ps("@((Get-AppxPackage -Name 'OpenAI.Codex' | Sort-Object Version -Descending) | Select-Object -ExpandProperty InstallLocation) | ConvertTo-Json -Compress")
    locations = json.loads(raw) if raw else []
    if isinstance(locations, str):
        locations = [locations]
    for location in locations:
        for name in ("ChatGPT.exe", "Codex.exe"):
            p = Path(location) / "app" / name
            if p.is_file():
                return p
    raise LauncherError("没有找到当前用户安装的 Codex 桌面应用。请安装后重试，或设置 codex_exe。")


def app_processes():
    raw = run_ps("@(Get-CimInstance Win32_Process -Filter \"Name = 'ChatGPT.exe' OR Name = 'Codex.exe'\" | Where-Object { $_.ExecutablePath -match '\\\\WindowsApps\\\\OpenAI\\.Codex_[^\\\\]+\\\\app\\\\' -and $_.CommandLine -notmatch '--type=' } | Select-Object ProcessId,ExecutablePath,CommandLine,@{n='Created';e={$_.CreationDate.ToUniversalTime().ToString('o')}}) | ConvertTo-Json -Compress")
    parsed = json.loads(raw) if raw else []
    return [parsed] if isinstance(parsed, dict) else parsed


def regular_tree_files(root):
    """Reject junctions/symlinks before reading or copying a plugin tree."""
    root = Path(root)
    files = []
    if root.is_symlink() or root.is_junction():
        raise LauncherError("插件目录包含链接，未复制或修改该目录。")
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            path = Path(directory) / name
            if path.lstat().st_file_attributes & 0x400:
                raise LauncherError("插件目录包含重解析点，未复制或修改该目录。")
            if name in names:
                if not path.is_file():
                    raise LauncherError("插件目录包含非常规文件。")
                files.append(path)
    return sorted(files)


def encrypted_file(path):
    return bool(path.stat().st_file_attributes & 0x4000)


def bundled_plugin_source(app):
    resources = app.parent / "resources"
    for base in (resources, resources / "app.asar.unpacked"):
        plugins = base / "plugins"
        marker = plugins / "openai-bundled/plugins/codex-app-tools/.codex-plugin/plugin.json"
        if marker.is_file():
            return plugins
    return None


def app_entry_contains(resources, markers):
    """Inspect the installed app's own entry scripts, without altering them."""
    try:
        with (resources / "app.asar").open("rb") as stream:
            header = struct.unpack("<4I", stream.read(16))
            if not 0 < header[3] <= 16 * 1024 * 1024 or header[1] < header[3] + 4:
                return False
            archive = json.loads(stream.read(header[3]))
            entries = archive["files"][".vite"]["files"]["build"]["files"]
            for name, entry in entries.items():
                if not name.endswith(".js") or "offset" not in entry:
                    continue
                size = int(entry["size"])
                if not 0 < size <= 16 * 1024 * 1024:
                    continue
                stream.seek(8 + header[1] + int(entry["offset"]))
                content = stream.read(size)
                if all(marker.encode() in content for marker in markers):
                    return True
    except (OSError, ValueError, KeyError, struct.error):
        pass
    return False


def supports_bundle_override(resources):
    return app_entry_contains(resources, [BUNDLED_RESOURCES_ENV])


def app_manages_tools(config, app):
    """New desktop builds generate trusted MCP configuration per conversation."""
    servers = config.get("mcp_servers", {})
    if isinstance(servers.get("node_repl"), dict) and not owned_native_overlay(servers["node_repl"]):
        return False
    disabled = servers.get("cua_repl", {})
    if disabled.get("enabled") is not False:
        return False
    if not re.search(r"(?i)[\\/]WindowsApps[\\/]OpenAI\.Codex_[^\\/]+[\\/]app[\\/](ChatGPT|Codex)\.exe$",
                     str(disabled.get("command", ""))):
        return False
    return app_entry_contains(app.parent / "resources",
                              ["getExpectedThreadConfig", "getTrustedServiceEnv", "mcp_servers.node_repl"])


def tree_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in regular_tree_files(root)}


def prepare_bundled_resources(app):
    """Materialize identical official files, avoiding CopyFile's EFS propagation.

    Never decrypt or change the Store installation. Never patch plugin contents.
    A cache is reused only after comparing every file with this installed version.
    """
    source = bundled_plugin_source(app)
    if source is None:
        return {"status": "not_applicable", "resources_path": None}
    source_files = regular_tree_files(source)
    if not any(encrypted_file(p) for p in source_files):
        return {"status": "native", "resources_path": None}
    if not supports_bundle_override(app.parent / "resources"):
        raise LauncherError("检测到加密的内置插件，但当前应用未提供已验证的资源路径设置；请查看 Local Work 兼容性日志。")
    expected = tree_hashes(source)
    cache_parent = STATE / "bundled-resources"
    cache_parent.mkdir(parents=True, exist_ok=True)
    if cache_parent.is_symlink() or cache_parent.is_junction():
        raise LauncherError("启动器插件副本目录不能是链接。")
    # Include the source inventory, so updates never reuse a prior version's files.
    identity = hashlib.sha256((str(app) + json.dumps(expected, sort_keys=True)).encode()).hexdigest()[:16]
    key = "resources-" + identity
    for candidate in sorted(cache_parent.glob(key + "*")):
        try:
            if candidate.is_symlink() or candidate.is_junction():
                continue
            if (candidate / "manifest.json").is_file() and tree_hashes(candidate / "plugins") == expected:
                return {"status": "verified", "resources_path": str(candidate), "files": len(expected)}
        except (OSError, LauncherError):
            continue
    staging = Path(tempfile.mkdtemp(prefix="building-", dir=cache_parent))
    for src in source_files:
        relative = src.relative_to(source)
        data = src.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected[relative.as_posix()]:
            raise LauncherError("复制期间 Codex 插件发生变化，请等待应用更新完成后重试。")
        # Writing contents preserves normal inherited ACLs and does not copy EFS.
        atomic_write(staging / "plugins" / relative, data)
    if tree_hashes(staging / "plugins") != expected:
        raise LauncherError("插件副本校验失败，未使用该副本启动应用。")
    write_json(staging / "manifest.json", {"app": str(app), "source": str(source),
               "created": stamp(), "sha256": expected})
    destination = cache_parent / key
    if destination.exists():
        destination = cache_parent / (key + "-" + staging.name.removeprefix("building-"))
    staging.rename(destination)
    log("Verified official plugin copy: " + str(destination) + "; files=" + str(len(expected)))
    return {"status": "prepared", "resources_path": str(destination), "files": len(expected)}


def local_work_status(processes, log_root=None):
    """Only the active desktop process's events count as readiness evidence."""
    root = log_root or Path(os.environ["LOCALAPPDATA"]) / "Codex/Logs"
    files = []
    for process in processes:
        pid = int(process["ProcessId"])
        files.extend(root.glob(f"*/*/*/*-{pid}-t0-*.log"))
    result = {"status": "unverified", "message": "未取得当前 Local Work 执行器就绪记录。"}
    for file in sorted(files, key=lambda p: p.stat().st_mtime):
        with file.open("rb") as stream:
            # Read only executor events, but include the whole active process log:
            # ordinary chat traffic can push a valid connection past a tail window.
            lines = [line.decode("utf-8", errors="replace").rstrip()
                     for line in stream if b"[tpp-local-executor]" in line]
        for line in lines:
            if "[tpp-local-executor]" not in line:
                continue
            if "Local Work executor connected to rendezvous" in line:
                result = {"status": "connected", "message": "Local Work 执行器已连接。", "event_time": line[:24]}
            elif any(x in line for x in ("executor startup failed", "executor process failed",
                                        "executor exited", "executor rendezvous unavailable")):
                copying = "copyfile" in line and "codex-app-tools" in line
                result = {"status": "failed", "reason": "plugin_copy" if copying else "executor_error",
                          "message": "Local Work 执行器复制内置插件失败，需要应用修复后重启。" if copying
                                     else "Local Work 执行器尚未就绪，请查看应用连接日志。",
                          "event_time": line[:24]}
            elif "Local Work executor spawned; awaiting rendezvous" in line:
                result = {"status": "connecting", "message": "Local Work 执行器已启动，正在等待连接。",
                          "event_time": line[:24]}
    return result


def official_runtime(config=None, home=None):
    home = home or codex_home()
    config = config or tomllib.loads((home / "config.toml").read_text(encoding="utf-8-sig"))
    managed = config.get("mcp_servers", {}).get("node_repl")
    if not isinstance(managed, dict):
        raise LauncherError("Codex 尚未生成官方 node_repl 配置。请在 Codex 中启用浏览器或 Computer Use 插件后重试。")
    p = Path(managed.get("command", "")).resolve()
    runtime_root = Path(os.environ["LOCALAPPDATA"]) / "OpenAI/Codex/runtimes/cua_node"
    roots = [runtime_root.resolve()]
    # Older portable releases used a resources/cua_node directory. Only the
    # currently managed installed executable is accepted, never a downloaded helper.
    if p.name.lower() != "node_repl.exe" or not p.is_relative_to(roots[0]):
        raise LauncherError("官方 Node REPL 的安装结构已变化；为避免选择错误程序，请检查日志中的兼容性说明。")
    if not p.is_file():
        raise LauncherError("Codex 更新后的工具路径尚未刷新。先打开 Codex 等待更新完成，再运行本启动器。")
    return p, copy.deepcopy(managed)


def version_key(name):
    try:
        return tuple(int(x) for x in name.split("."))
    except ValueError:
        return (-1,)


def browser_fallback(env, home):
    if "NODE_REPL_TRUSTED_SERVICES" not in env:
        return None
    services = json.loads(env["NODE_REPL_TRUSTED_SERVICES"])
    configured = services.get("browser")
    if not configured or Path(configured).is_file():
        return configured
    cache = home / "plugins/cache/openai-bundled"
    browser_root = cache / "browser"
    if not browser_root.is_dir():
        raise LauncherError("官方浏览器插件文件尚未安装完整，请在 Codex 中完成插件更新。")
    for folder in sorted(browser_root.iterdir(), key=lambda p: version_key(p.name), reverse=True):
        if version_key(folder.name) == (-1,):
            continue
        browser = folder / "scripts/browser-service.mjs"
        chrome = cache / "chrome" / folder.name / "scripts/browser-service.mjs"
        if browser.is_file() and chrome.is_file():
            if hashlib.sha256(browser.read_bytes()).digest() == hashlib.sha256(chrome.read_bytes()).digest():
                services["browser"] = browser.as_posix()
                env["NODE_REPL_TRUSTED_SERVICES"] = json.dumps(services, ensure_ascii=False)
                return str(browser)
    raise LauncherError("找不到可验证的官方浏览器服务。请完成 Browser / Chrome 插件更新；没有修改插件代码。")


def mcp_configuration(settings=None, home=None):
    settings = settings or load_settings()
    home = home or codex_home()
    program, managed = official_runtime(home=home)
    env = os.environ.copy()
    env.update({k: str(v) for k, v in managed.get("env", {}).items()})
    proxy, source = choose_proxy(settings)
    env = proxy_env(proxy, env)
    browser = browser_fallback(env, home)
    return program, managed.get("args", []), env, {"official_program": str(program),
        "browser_service": browser, "proxy": proxy, "proxy_source": source}


def managed_env_vars(config):
    values = copy.deepcopy(config.get("mcp_servers", {}).get("node_repl", {}).get("env_vars", []))
    for name in ("CODEX_HOME", "CODEX_WINDOWS_REGISTERED_CORE"):
        if not any(x == name or isinstance(x, dict) and x.get("name") == name for x in values):
            values.append(name)
    return values


def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(x) for x in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(json.dumps(k) + " = " + toml_value(v) for k, v in value.items()) + " }"
    raise LauncherError("不支持的 MCP 配置类型；原配置未改变。")


def table_path(header):
    marker = "__codex_proxy_launcher_marker__"
    try:
        obj = tomllib.loads(header + "\n" + marker + " = 1\n")
        result = []
        while marker not in obj:
            if len(obj) != 1:
                return None
            key, obj = next(iter(obj.items()))
            result.append(key)
            if isinstance(obj, list):
                obj = obj[-1]
        return tuple(result)
    except Exception:
        return None


def replace_alias(text, alias, server_name=ALIAS):
    """Edit complete table spans, preserving all unrelated TOML byte text.

    Prefix parsing prevents a header-looking line inside a multiline string or
    array from being misidentified as a table. Parse/semantic checks fail closed.
    """
    original = tomllib.loads(text)
    headers = []
    for m in re.finditer(r"(?m)^[ \t]*\[.*\][ \t]*(?:#[^\r\n]*)?(?:\r?\n|$)", text):
        path = table_path(m.group().strip())
        if path is None:
            continue
        try:
            tomllib.loads(text[:m.start()])
        except tomllib.TOMLDecodeError:
            continue
        headers.append((m.start(), path))
    targets = []
    for i, (start, path) in enumerate(headers):
        if path[:2] == ("mcp_servers", server_name):
            targets.append((start, headers[i+1][0] if i+1 < len(headers) else len(text)))
    current = original.get("mcp_servers", {}).get(server_name)
    if current is not None and not targets:
        raise LauncherError("MCP 配置使用了内联表或不支持的表结构，无法安全替换。原文件保持不变。")
    updated = text
    for start, end in reversed(targets):
        updated = updated[:start] + updated[end:]
    if alias is not None:
        block = "[mcp_servers." + server_name + "]\n"
        block += "\n".join(json.dumps(k) + " = " + toml_value(v) for k, v in alias.items()) + "\n"
        updated = updated.rstrip() + "\n\n" + block
    expected = copy.deepcopy(original)
    expected.setdefault("mcp_servers", {}).pop(server_name, None)
    if alias is not None:
        expected["mcp_servers"][server_name] = alias
    actual = tomllib.loads(updated)
    # An otherwise empty table is harmless, but no other semantic change is allowed.
    if expected.get("mcp_servers") == {}:
        expected.pop("mcp_servers", None)
    if actual.get("mcp_servers") == {}:
        actual.pop("mcp_servers", None)
    if actual != expected:
        raise LauncherError("配置完整性检查未通过；没有写入任何修改。")
    return updated


def desired_alias(config):
    return {"command": str(PYTHON), "args": ["-I", "-X", "utf8", str(SCRIPT), "mcp"],
            "startup_timeout_sec": 120, "env_vars": managed_env_vars(config)}


def owned_native_overlay(server):
    return isinstance(server, dict) and server.get("command") == str(PYTHON) and server.get("args") == [
        "-I", "-X", "utf8", str(SCRIPT), "native-placeholder"]


def native_overlay(proxy):
    # A valid, inert transport is necessary for config/read. The desktop supplies
    # the real command, args, trust metadata and services in each thread config.
    # Codex merges the env map, so ONLY these network settings are contributed.
    return {"command": str(PYTHON),
            "args": ["-I", "-X", "utf8", str(SCRIPT), "native-placeholder"],
            "startup_timeout_sec": 120, "env": proxy_env(proxy, {})}


def native_placeholder():
    """Empty MCP server for non-desktop clients; never substitutes browser tools."""
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            method = request.get("method")
            if method == "initialize":
                result = {"protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "codex-proxy-environment", "version": VERSION}}
            elif method == "tools/list":
                result = {"tools": []}
            elif method == "ping":
                result = {}
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                  "error": {"code": -32601, "message": "Method not found"}}), flush=True)
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
        except (ValueError, TypeError):
            continue
    return 0


def update_native_proxy(proxy=None, restore=False):
    path = codex_home() / "config.toml"
    rollback = STATE / "native-proxy-before.json"
    for _ in range(4):
        original = path.read_bytes()
        text = original.decode("utf-8-sig")
        config = tomllib.loads(text)
        old = config.get("mcp_servers", {}).get(NATIVE_SERVER)
        if old is not None and not owned_native_overlay(old):
            # A new official layout or user-defined entry is never overwritten.
            raise LauncherError("官方工具入口已变化，未覆盖现有 node_repl 配置。请查看诊断。")
        if restore:
            if not rollback.exists() or not owned_native_overlay(old):
                return False
            saved = json.loads(rollback.read_text(encoding="utf-8"))
            if saved["config_path"] != str(path):
                raise LauncherError("代理补充配置的备份属于另一个 Codex 配置文件。")
            desired = saved["previous_server"]
        else:
            desired = copy.deepcopy(old) if old else native_overlay(proxy)
            desired.setdefault("env", {}).update(proxy_env(proxy, {}))
        if old == desired:
            return False
        updated = replace_alias(text, desired, NATIVE_SERVER)
        if path.read_bytes() != original:
            time.sleep(.15)
            continue
        STATE.mkdir(parents=True, exist_ok=True)
        if not restore and not rollback.exists():
            write_json(rollback, {"config_path": str(path), "previous_server": old, "created": stamp()})
        backup_dir = STATE / "backups"
        backup_dir.mkdir(exist_ok=True)
        (backup_dir / ("native-proxy-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".toml")).write_bytes(original)
        if path.read_bytes() != original:
            continue
        atomic_write(path, updated.encode("utf-8-sig" if original.startswith(b"\xef\xbb\xbf") else "utf-8"))
        log("Native MCP proxy environment " + ("restored" if restore else "configured") + "; proxy=" + str(proxy))
        return True
    raise LauncherError("Codex 正在更新工具配置，代理补充配置将在下次检查时重试。")


def native_proxy_status(config, proxy):
    server = config.get("mcp_servers", {}).get(NATIVE_SERVER)
    expected = proxy_env(proxy, {})
    good = owned_native_overlay(server) and all(server.get("env", {}).get(k) == v for k, v in expected.items())
    return {"configured": good, "browser_session": "not-tested",
            "meaning": "已补充原生工具代理环境；网页操作仍需实测。" if good else "原生工具缺少代理补充配置。"}


def ensure_native_guardian(parent_pid):
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "native-guardian.stop").unlink(missing_ok=True)
    child = subprocess.Popen([str(PYTHON), "-I", "-X", "utf8", str(SCRIPT), "watch-native",
                              "--parent-pid", str(parent_pid)], cwd=ROOT,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=HIDDEN, close_fds=True)
    return child.pid


def watch_native_proxy(parent_pid):
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "native-guardian.lock").open("a+b") as lock:
        lock.seek(0); lock.write(b"0"); lock.flush(); lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return 0
        handle = kernel.OpenProcess(0x00100000, False, parent_pid)
        if not handle:
            return 1
        try:
            write_json(STATE / "native-guardian.json", {"pid": os.getpid(), "parent_pid": parent_pid, "started": stamp()})
            previous_error = None
            previous_signature = None
            while kernel.WaitForSingleObject(handle, 250) == 0x102:
                if (STATE / "native-guardian.stop").exists():
                    break
                try:
                    proxy, _ = choose_proxy(load_settings())
                    config_path = codex_home() / "config.toml"
                    info = config_path.stat()
                    signature = (info.st_mtime_ns, info.st_size, proxy)
                    if signature == previous_signature:
                        continue
                    update_native_proxy(proxy)
                    info = config_path.stat()
                    previous_signature = (info.st_mtime_ns, info.st_size, proxy)
                    previous_error = None
                except Exception as error:
                    message = str(error)
                    if message != previous_error:
                        log("Native proxy guardian: " + message)
                        previous_error = message
            return 0
        finally:
            kernel.CloseHandle(handle)
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def owned_alias(alias):
    if not alias:
        return True
    args = [str(x).lower().replace("/", "\\") for x in alias.get("args", [])]
    return (any(x.endswith("\\node_repl_proxy_launcher.py") for x in args)
            or any(x.endswith("\\src\\backend.py") for x in args) and "mcp" in args)


def update_config(restore=False, app_managed=False):
    config_path = codex_home() / "config.toml"
    STATE.mkdir(parents=True, exist_ok=True)
    rollback = STATE / "mcp-before.json"
    for attempt in range(4):
        original = config_path.read_bytes()
        text = original.decode("utf-8-sig")
        config = tomllib.loads(text)
        old = config.get("mcp_servers", {}).get(ALIAS)
        if app_managed and old and (old.get("command") != str(PYTHON) or str(SCRIPT) not in old.get("args", [])):
            return False  # Never remove another installation's or user's entry.
        if not owned_alias(old):
            raise LauncherError("同名 node_repl_proxy 已被其他工具占用，未覆盖。请检查 config.toml。")
        if restore:
            if not rollback.exists():
                return False
            prior = json.loads(rollback.read_text(encoding="utf-8"))
            if prior["config_path"] != str(config_path):
                raise LauncherError("回退文件对应另一份 Codex 配置，未覆盖当前文件。")
            alias = prior["previous_alias"]
        elif app_managed:
            alias = None
        else:
            alias = desired_alias(config)
        if old == alias:
            return False
        updated = replace_alias(text, alias)
        encoded = updated.encode("utf-8-sig" if original.startswith(b"\xef\xbb\xbf") else "utf-8")
        if config_path.read_bytes() != original:
            time.sleep(.15)
            continue
        if not restore and not rollback.exists():
            write_json(rollback, {"config_path": str(config_path), "previous_alias": old, "created": stamp()})
        backup_dir = STATE / "backups"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / ("config-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".toml")
        backup.write_bytes(original)
        # Last comparison protects against ordinary app regeneration while editing.
        if config_path.read_bytes() != original:
            continue
        atomic_write(config_path, encoded)
        after = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
        if after.get("mcp_servers", {}).get(ALIAS) != alias:
            raise LauncherError("Codex 同时刷新了配置，请再点一次“配置并启动”。")
        log("MCP alias " + ("restored" if restore else "configured") + "; backup=" + str(backup))
        return True
    raise LauncherError("Codex 正在连续更新配置，请等待更新结束后重试。")


def install_shortcut():
    exe = ROOT / "CodexProxyLauncher.exe"
    result = run_ps("$d=[Environment]::GetFolderPath('Desktop'); $p=Join-Path $d 'Codex 代理启动.lnk'; $w=New-Object -ComObject WScript.Shell; if(Test-Path -LiteralPath $p) { $o=$w.CreateShortcut($p); if($o.TargetPath -ne $env:CP_TARGET) { throw 'A shortcut with this name already exists.' } }; $s=$w.CreateShortcut($p); $s.TargetPath=$env:CP_TARGET; $s.WorkingDirectory=$env:CP_ROOT; $s.Description='检查代理并启动当前版本 Codex'; $s.IconLocation=$env:CP_TARGET+',0'; $s.Save(); $p",
                    {"CP_TARGET": str(exe), "CP_ROOT": str(ROOT)})
    return result


def run_mcp():
    try:
        program, args, env, info = mcp_configuration()
        if not port_open(info["proxy"]):
            raise LauncherError("代理端口未开启，请通过 Codex 代理启动器启动。")
        log("MCP official runtime=" + str(program) + "; browser=" + str(info["browser_service"]))
        child = subprocess.Popen([str(program), *args], env=env,
                                 stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
                                 creationflags=HIDDEN)
        return child.wait()
    except Exception as error:
        print("Codex Proxy Launcher: " + str(error), file=sys.stderr, flush=True)
        return 1


def check_mcp():
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "codex-proxy-launcher-check", "version": VERSION}}}
    p = subprocess.Popen([str(PYTHON), "-I", "-X", "utf8", str(SCRIPT), "mcp"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         creationflags=HIDDEN)
    import queue
    import threading
    replies = queue.Queue()
    def read():
        for line in p.stdout:
            try:
                x = json.loads(line)
                if x.get("id") == 1:
                    replies.put(x)
                    return
            except ValueError:
                pass
        replies.put(None)
    threading.Thread(target=read, daemon=True).start()
    try:
        p.stdin.write((json.dumps(request) + "\n").encode())
        p.stdin.flush()
        result = replies.get(timeout=20)
        if not result or "result" not in result:
            raise LauncherError("官方 MCP 初始化握手未完成。")
        return {"initialized": True, "server": result["result"].get("serverInfo"),
                "meaning": "仅验证官方工具启动和协议握手，未执行浏览器或电脑操作"}
    finally:
        p.stdin.close()
        try:
            p.wait(timeout=4)
        except subprocess.TimeoutExpired:
            # This is our isolated diagnostic wrapper, never the desktop's process.
            p.terminate()


def close_app(processes):
    targets = {int(p["ProcessId"]) for p in processes}
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    @callback_type
    def visitor(hwnd, unused):
        pid = ctypes.c_ulong()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in targets and user.IsWindowVisible(ctypes.c_void_p(hwnd)):
            user.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE, permits app save/cancel.
        return True
    user.EnumWindows(visitor, 0)
    for _ in range(10):
        time.sleep(1)
        if not targets.intersection(int(p["ProcessId"]) for p in app_processes()):
            return
    raise LauncherError("Codex 尚未正常退出，可能有保存提示。请处理提示或手动退出后重试；没有强制结束进程。")


def prior_launch_matches(processes, proxy, bundled_resources=None, native_tools=False):
    p = STATE / "last-launch.json"
    if not p.exists():
        return None
    saved = json.loads(p.read_text(encoding="utf-8"))
    if (saved.get("proxy") != proxy or not saved.get("launcher_version")
            or native_tools and saved.get("native_proxy_revision", 0) < NATIVE_PROXY_REVISION
            or saved.get("bundled_plugins_resources") != bundled_resources):
        return None
    for process in processes:
        if (process["ProcessId"] == saved.get("pid") and process["Created"] == saved.get("created")
                and process["ExecutablePath"] == saved.get("exe")
                and ("--proxy-server=" + proxy) in process.get("CommandLine", "")):
            return process["ProcessId"]
    return None


def run_action(action):
    settings = load_settings()
    if action == "restore":
        atomic_write(STATE / "native-guardian.stop", b"stop\n")
        time.sleep(1.2)
        native_changed = update_native_proxy(restore=True)
        changed = update_config(restore=True)
        changed = changed or native_changed
        emit("result", "已恢复启动器安装前的工具配置；其他 Codex 设置保持当前值。" if changed else "工具配置无需恢复。", status="restored")
        return 0
    proxy, source = choose_proxy(settings)
    progress("使用" + source + "：" + proxy)
    ensure_proxy(settings, proxy, allow_start=action in ("launch", "restart", "configure"))
    progress("正在检查经代理建立的 HTTPS 连接……")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        app_future = pool.submit(discover_app, settings)
        checks = network_checks(proxy)
        app = app_future.result()
    report = {"time": stamp(), "launcher_version": VERSION, "proxy": proxy, "proxy_source": source,
              "codex_exe": str(app), "network": checks, "mcp": None,
              "remote_session": "未验证，需要在 Codex 内确认远程设备在线"}
    processes = app_processes()
    report["local_work"] = local_work_status(processes)
    config = tomllib.loads((codex_home() / "config.toml").read_text(encoding="utf-8-sig"))
    native_tools = app_manages_tools(config, app)
    for item in checks:
        progress(item["host"] + ("：代理 TLS/HTTP 可达（HTTP " + str(item["http_status"]) + "，不代表已登录）"
                                if item["reachable"] else "：连接失败，" + item.get("error", "")))
    try:
        if native_tools:
            report["mcp"] = {"mode": "app-managed", **native_proxy_status(config, proxy)}
        else:
            _, _, _, info = mcp_configuration(settings)
            report["mcp"] = {"mode": "legacy-wrapper", "configuration": info}
    except Exception as error:
        report["mcp"] = {"error": str(error)}
    LOGS.mkdir(parents=True, exist_ok=True)
    write_json(LOGS / "latest-diagnostics.json", report)
    if not any(x["reachable"] for x in checks):
        raise LauncherError("代理端口可用，但 OpenAI 地址的 TLS/HTTP 检查都失败。请先切换可用代理节点，再重试。")
    if action == "diagnose":
        if report["mcp"].get("error"):
            raise LauncherError(report["mcp"]["error"])
        if native_tools:
            progress(report["mcp"]["meaning"])
        else:
            progress("正在验证独立官方工具进程的 MCP 初始化握手……")
            report["mcp"]["handshake"] = check_mcp()
        write_json(LOGS / "latest-diagnostics.json", report)
        partial = (any(not item["reachable"] for item in checks) or report["local_work"]["status"] == "failed"
                   or native_tools and not report["mcp"]["configured"])
        message = ("部分地址连接失败，请查看日志；官方 MCP 初始化通过。" if partial else "代理连接与官方 MCP 初始化检查通过。")
        if native_tools:
            message = "代理与应用托管工具配置检查已完成。" + report["local_work"]["message"]
        if report["local_work"]["status"] == "failed":
            message = "代理与 MCP 检查已完成；" + report["local_work"]["message"]
        emit("result", message + "浏览器、Computer Use 和 Remote 的实际会话需在 Codex 中确认。", status="partial" if partial else "checked", report=report)
        return 0
    progress("正在自动检查当前安装版本的内置插件，并校验 Local Work 所需文件……")
    bundle = prepare_bundled_resources(app)
    report["bundled_plugins"] = bundle
    write_json(LOGS / "latest-diagnostics.json", report)
    if bundle["resources_path"]:
        progress("内置插件副本已校验。以后更新时会自动重新识别并准备，无需手动修改版本或路径。")
    progress("正在检查工具代理配置；配置变化前会自动备份……")
    changed = update_config(app_managed=native_tools)
    if native_tools:
        native_changed = update_native_proxy(proxy)
        changed = changed or native_changed
        progress("已补充原生浏览器工具的代理环境，官方按会话生成的权限与工具入口保持有效。")
        if processes:
            ensure_native_guardian(processes[0]["ProcessId"])
        report["mcp"] = {"mode": "app-managed", **native_proxy_status(
            tomllib.loads((codex_home() / "config.toml").read_text(encoding="utf-8-sig")), proxy)}
        write_json(LOGS / "latest-diagnostics.json", report)
    if action == "configure":
        shortcut = install_shortcut()
        emit("result", "已保存代理工具配置并创建桌面“Codex 代理启动”快捷方式。当前 Codex 会话没有重启。", status="configured", shortcut=shortcut, changed=changed)
        return 0
    if processes:
        matched = prior_launch_matches(processes, proxy, bundle["resources_path"], native_tools=native_tools)
        if matched and action != "restart":
            emit("result", "Codex 已通过本启动器运行，代理配置相同。", status="already", pid=matched)
            return 0
        if action != "restart":
            emit("result", "Codex 当前已在运行，已有进程无法继承新代理。请先结束正在执行的任务，再点击“正常关闭并重启”，或手动退出 Codex 后重试。", status="running")
            return 0
        progress("正在请求 Codex 正常退出……")
        close_app(processes)
        app = discover_app(settings)  # Closing can complete an application update.
        bundle = prepare_bundled_resources(app)
    progress("正在通过代理启动当前安装的 Codex……")
    env = proxy_env(proxy)
    # Never retain a previous version's resource override from the parent shell.
    for key in list(env):
        if key.upper() == BUNDLED_RESOURCES_ENV:
            del env[key]
    if bundle["resources_path"]:
        env[BUNDLED_RESOURCES_ENV] = bundle["resources_path"]
    args = [str(app), "--proxy-server=" + proxy,
            "--proxy-bypass-list=localhost;127.0.0.1;[::1]"]
    child = subprocess.Popen(args, cwd=ROOT, env=env, close_fds=True)
    observed = []
    for _ in range(8):
        time.sleep(1)
        observed = app_processes()
        if observed:
            break
        if child.poll() not in (None, 0):
            break
    candidates = [p for p in observed if ("--proxy-server=" + proxy) in p.get("CommandLine", "")]
    if not candidates:
        raise LauncherError("未观察到带代理参数的 Codex 主进程。应用可能正在更新；请等更新结束后重试。")
    main = next((p for p in candidates if p["ProcessId"] == child.pid), candidates[0])
    write_json(STATE / "last-launch.json", {"time": stamp(), "pid": main["ProcessId"],
              "created": main["Created"], "exe": main["ExecutablePath"], "proxy": proxy,
              "launcher_version": VERSION, "bundled_plugins_resources": bundle["resources_path"],
              "native_proxy_revision": NATIVE_PROXY_REVISION if native_tools else 0})
    if native_tools:
        ensure_native_guardian(main["ProcessId"])
    progress("Codex 已启动，正在读取本次 Local Work 执行器的连接状态……")
    deadline = time.monotonic() + 12
    while True:
        local_work = local_work_status([main])
        if local_work["status"] in ("connected", "failed") or time.monotonic() >= deadline:
            break
        time.sleep(.5)
    report.update(codex_exe=str(app), bundled_plugins=bundle, local_work=local_work)
    write_json(LOGS / "latest-diagnostics.json", report)
    if local_work["status"] == "failed":
        emit("result", "Codex 已带代理启动；" + local_work["message"], status="partial", pid=main["ProcessId"], report=report)
        return 0
    progress(local_work["message"])
    emit("result", "Codex 已带代理启动。新建工具连接会使用已保存的官方代理启动配置。", status="launched", pid=main["ProcessId"])
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["launch", "restart", "configure", "diagnose", "restore", "mcp", "mcp-config", "native-placeholder", "watch-native"])
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    if args.action == "native-placeholder":
        return native_placeholder()
    if args.action == "watch-native":
        return watch_native_proxy(args.parent_pid) if args.parent_pid else 2
    if args.action == "mcp":
        return run_mcp()
    if args.action == "mcp-config":
        print(json.dumps(mcp_configuration()[3], ensure_ascii=False))
        return 0
    # Cross-process lock for GUI and CLI. The MCP wrapper is deliberately separate.
    import msvcrt
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "action.lock").open("a+b") as lock:
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            emit("error", "另一个启动器正在操作，请稍等。")
            return 1
        try:
            return run_action(args.action)
        except Exception as error:
            log(type(error).__name__ + ": " + str(error))
            emit("error", str(error))
            return 1
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    sys.exit(main())
