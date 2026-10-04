"""Optional Windows network bridge. Uses the user's existing NekoBox core.

No Codex files, certificates, subscription credentials, or system proxy settings
are changed. The elevated supervisor owns the core in a kill-on-close job.
"""
import ctypes
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from ctypes import wintypes as w

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("launcher_backend", ROOT / "src" / "backend.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)
STATE = ROOT / "state" / "cloud-network"
STATUS = STATE / "status.json"
STOP = STATE / "stop"
CORE_LOG = ROOT / "logs" / "cloud-network.log"


def status(value, **details):
    backend.write_json(STATUS, {"time": backend.stamp(), "status": value,
                               "supervisor_pid": os.getpid(), **details})
    messages = {"starting": "正在启动云端网络代理，请完成 Windows 管理员确认。",
                "prepared": "云端网络代理配置检查通过。", "running": "云端网络代理已启动。",
                "stopped": "云端网络代理已停止。", "failed": details.get("error", "网络代理失败。")}
    print(json.dumps({"type": "error" if value == "failed" else "status", "status": value,
                      "message": messages.get(value, value), **details}, ensure_ascii=False), flush=True)


def process_snapshot():
    class Entry(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD),
                    ("heap", ctypes.c_size_t), ("module", w.DWORD), ("threads", w.DWORD),
                    ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD),
                    ("exe", w.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    result = {}
    try:
        entry = Entry(); entry.size = ctypes.sizeof(entry)
        found = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while found:
            result[entry.pid] = (entry.parent, entry.exe.lower())
            found = kernel.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    return result


def running_status(proxy):
    try:
        data = json.loads(STATUS.read_text(encoding="utf8"))
        if data.get("status") != "running":
            return data
        processes = process_snapshot()
        supervisor = int(data["supervisor_pid"])
        core_pid = int(data["core_pid"])
        parent, name = processes[supervisor]
        if (name != "python.exe" or processes[parent][1] != "codexcloudproxy.exe"
                or processes[core_pid] != (supervisor, "nekobox_core.exe")):
            return {"status": "stale"}
        config = json.loads((STATE / "config.json").read_text(encoding="utf8"))
        outbound = next(x for x in config["outbounds"] if x["tag"] == "proxy")
        selected = urlsplit(proxy)
        expected_host = "127.0.0.1" if selected.hostname == "localhost" else selected.hostname
        if outbound["server"] != expected_host or outbound["server_port"] != selected.port:
            return {"status": "proxy_changed"}
        return data
    except (OSError, ValueError, KeyError, StopIteration):
        return {"status": "stopped"}


def ensure_running(proxy):
    current = running_status(proxy)
    if current["status"] == "running":
        return current
    if current["status"] == "proxy_changed":
        STOP.write_text("stop", encoding="utf8")
        for _ in range(30):
            time.sleep(.5)
            if running_status(proxy)["status"] != "proxy_changed":
                break
        else:
            raise RuntimeError("原网络代理尚未退出；请从托盘关闭后重试。")
    entry = ROOT / "CodexCloudProxy.exe"
    if not entry.is_file():
        raise RuntimeError("云端网络代理程序缺失，请重新完整解压启动器。")
    STATE.mkdir(parents=True, exist_ok=True)
    error_file = STATE / "elevation-error.txt"
    error_file.unlink(missing_ok=True)
    status("starting")
    subprocess.Popen([str(entry)], cwd=ROOT, creationflags=backend.HIDDEN)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        current = running_status(proxy)
        if current["status"] == "running":
            return current
        if error_file.exists():
            raise RuntimeError("Windows 管理员确认未完成；没有启用云端网络代理。")
        if current["status"] == "failed":
            raise RuntimeError(current.get("error", "云端网络代理启动失败。"))
        time.sleep(.5)
    raise RuntimeError("等待网络代理启动超时，请查看 state/cloud-network/status.json。")


def prepare():
    settings = backend.load_settings()
    proxy, _ = backend.choose_proxy(settings)
    if not backend.port_open(proxy):
        raise RuntimeError("现有代理端口不可用，请先启动代理软件。")
    address = urlsplit(proxy)
    if address.hostname not in ("127.0.0.1", "localhost"):
        raise RuntimeError("网络补充入口目前要求使用本机 HTTP / mixed 代理。")
    core = Path(settings.get("proxy_app_path", "")).parent / "nekobox_core.exe"
    if not core.is_file():
        raise RuntimeError("没有找到代理软件旁的 nekobox_core.exe。")
    result = subprocess.run([str(core), "version"], capture_output=True,
                            creationflags=backend.HIDDEN, timeout=10)
    version = re.search(rb"sing-box version (\d+)\.(\d+)", result.stdout)
    if result.returncode or not version or (int(version[1]), int(version[2])) != (1, 6):
        raise RuntimeError("此入口尚未验证当前代理内核；没有修改网络。")
    # Query the physical default route before creating our own virtual adapter.
    query = r"""
$routes = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' |
 Where-Object { $_.InterfaceAlias -ne 'CodexProxyTun' }
$best = $routes | Sort-Object { $_.RouteMetric + (Get-NetIPInterface -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4).InterfaceMetric } | Select-Object -First 1
if (-not $best) { throw 'No physical default route.' }
$dns = (Get-DnsClientServerAddress -InterfaceIndex $best.InterfaceIndex -AddressFamily IPv4).ServerAddresses | Select-Object -First 1
if (-not $dns) { throw 'No physical DNS server.' }
@{ dns=$dns; interface=$best.InterfaceAlias } | ConvertTo-Json -Compress
"""
    physical = json.loads(backend.run_ps(query))
    dns = str(ipaddress.IPv4Address(physical["dns"]))
    config = {
        "log": {"level": "warn", "timestamp": True},
        "dns": {"servers": [{"tag": "physical-dns", "address": dns,
                               "detour": "direct"}], "strategy": "ipv4_only"},
        "inbounds": [{"type": "tun", "tag": "codex-tun",
                      "interface_name": "CodexProxyTun",
                      "inet4_address": "172.31.254.1/30", "mtu": 1500,
                      "auto_route": True, "strict_route": False,
                      "stack": "gvisor", "sniff": True,
                      "sniff_override_destination": True}],
        "outbounds": [{"type": "http", "tag": "proxy", "server": "127.0.0.1",
                       "server_port": address.port},
                      {"type": "direct", "tag": "direct"},
                      {"type": "dns", "tag": "dns"}],
        "route": {"auto_detect_interface": True, "rules": [
            {"protocol": "dns", "outbound": "dns"},
            {"ip_cidr": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                         "127.0.0.0/8", "169.254.0.0/16"], "outbound": "direct"},
            {"domain": ["codex-cloud-backend.chatgpt.com"], "network": "tcp",
             "outbound": "proxy"},
            {"process_name": ["ChatGPT.exe", "Codex.exe", "codex.exe", "node_repl.exe"],
             "network": "tcp", "outbound": "proxy"}], "final": "direct"}}
    STATE.mkdir(parents=True, exist_ok=True)
    config_file = STATE / "config.json"
    backend.write_json(config_file, config)
    check = subprocess.run([str(core), "check", "-c", str(config_file)],
                           capture_output=True, creationflags=backend.HIDDEN, timeout=10)
    if check.returncode:
        raise RuntimeError("代理内核未接受配置：" + check.stderr.decode("utf8", "replace")[-700:])
    status("prepared", core=str(core), proxy=proxy, physical_dns=dns,
           physical_interface=physical["interface"])
    return core, config_file


def stop_core(child):
    """Deliver Go's normal console shutdown signal before any forced cleanup."""
    if child.poll() is not None:
        return
    kernel = ctypes.windll.kernel32
    kernel.FreeConsole()
    attached = kernel.AttachConsole(child.pid)
    if attached:
        handler_type = ctypes.WINFUNCTYPE(w.BOOL, w.DWORD)
        handler = handler_type(lambda event: True)
        # Ignoring CTRL_C with a null handler does not protect against CTRL_BREAK.
        # Keep an actual callback alive while attached to the child's console.
        kernel.SetConsoleCtrlHandler.argtypes = [handler_type, w.BOOL]
        kernel.SetConsoleCtrlHandler(handler, True)
        kernel.GenerateConsoleCtrlEvent(1, 0)  # CTRL_BREAK_EVENT to this child console only
        try:
            child.wait(timeout=6)
        except subprocess.TimeoutExpired:
            pass
        finally:
            kernel.FreeConsole()
    if child.poll() is None:
        child.terminate()
        child.wait(timeout=5)


def run():
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("Windows 创建虚拟网卡需要管理员权限。")
    # The GUI first assigns this process to its job, then releases this gate.
    if sys.stdin.readline().strip() != "start":
        raise RuntimeError("网络入口未获得有效启动信号。")
    core, config_file = prepare()
    STOP.unlink(missing_ok=True)
    CORE_LOG.parent.mkdir(exist_ok=True)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    with CORE_LOG.open("w", encoding="utf8") as output:
        child = subprocess.Popen([str(core), "run", "--disable-color", "-c", str(config_file)],
                                 cwd=core.parent, stdout=output, stderr=output,
                                 creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError("网络入口启动失败：" + CORE_LOG.read_text(encoding="utf8", errors="replace")[-1500:])
                adapters = backend.run_ps("@(Get-NetAdapter -Name 'CodexProxyTun' -ErrorAction SilentlyContinue | Where-Object Status -eq 'Up').Count")
                if adapters.strip() == "1" and child.poll() is None:
                    break
                time.sleep(.25)
            else:
                raise RuntimeError("虚拟网卡未能在 20 秒内启动。")
            status("running", core_pid=child.pid, interface="CodexProxyTun")
            while child.poll() is None and not STOP.exists():
                time.sleep(.5)
            if child.poll() is not None and child.returncode:
                raise RuntimeError("网络入口意外退出，请查看 cloud-network.log。")
        finally:
            stop_core(child)
            status("stopped")


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["prepare"]:
            prepare()
        elif sys.argv[1:] == ["run"]:
            run()
        else:
            raise RuntimeError("Unsupported action")
    except Exception as error:
        status("failed", error=str(error))
        sys.exit(1)
