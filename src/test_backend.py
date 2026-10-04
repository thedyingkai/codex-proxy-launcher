"""Regression tests run only against isolated files and local test servers."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("backend", Path(__file__).with_name("backend.py"))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class LocalWorkTests(unittest.TestCase):
    def test_per_conversation_tool_config_is_detected_without_version_pin(self):
        app = Path('C:/Program Files/WindowsApps/OpenAI.Codex_any-version/app/ChatGPT.exe')
        config = {'mcp_servers':{'cua_repl':{'enabled':False,'command':str(app)}}}
        with patch.object(b, 'app_entry_contains', return_value=True):
            self.assertTrue(b.app_manages_tools(config, app))
            config['mcp_servers']['node_repl'] = {'command':'legacy'}
            self.assertTrue(b.app_manages_tools(config, app))
        config['mcp_servers'].pop('node_repl')
        with patch.object(b, 'app_entry_contains', return_value=False):
            self.assertFalse(b.app_manages_tools(config, app))

    def test_native_mode_preserves_dynamic_proxy_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = '[mcp_servers.cua_repl]\nenabled = false\ncommand = "app.exe"\n'
            (root/'config.toml').write_text(original,encoding='utf-8')
            with patch.object(b, 'codex_home', return_value=root), patch.object(b, 'STATE', root/'state'), patch.object(b, 'log'):
                self.assertTrue(b.update_config())
                self.assertFalse(b.update_config(app_managed=True))
                current = tomllib.loads((root/'config.toml').read_text())
                self.assertEqual(current['mcp_servers']['cua_repl'],tomllib.loads(original)['mcp_servers']['cua_repl'])
                self.assertEqual(current['mcp_servers']['node_repl_proxy'],b.desired_alias(current))
                self.assertFalse(b.update_config(app_managed=True))
                other=b.replace_alias(original,{'command':'other.exe','args':['other.py']})
                (root/'config.toml').write_text(other,encoding='utf-8')
                self.assertFalse(b.update_config(app_managed=True))
                self.assertEqual((root/'config.toml').read_text(),other)

    def test_dynamic_proxy_alias_accepts_verified_native_bridge(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            bridge=root/'CodexNativeProxy.exe'
            bridge.touch()
            native=root/'node_repl.exe'
            native.write_bytes(b'official fixture')
            (root/'native-target.json').write_text(json.dumps({'program':str(native),'sha256':b.file_hash(native)}))
            managed={'command':str(bridge),'env':{'NODE_REPL_TRUSTED_SERVICES':'unchanged'}}
            config={'mcp_servers':{'node_repl':managed}}
            with patch.object(b,'NATIVE_BRIDGE',bridge),patch.object(b,'STATE',root):
                program,actual=b.official_runtime(config=config)
                self.assertEqual(program,bridge.resolve())
                self.assertEqual(actual,managed)
                native.write_bytes(b'changed')
                with self.assertRaises(b.LauncherError):
                    b.official_runtime(config=config)

    def make_app(self, root, version="version-1", supported=True):
        app = root / version / "app/ChatGPT.exe"
        app.parent.mkdir(parents=True)
        app.touch()
        resources = app.parent / "resources"
        plugin = resources / "plugins/openai-bundled/plugins/codex-app-tools"
        (plugin / ".codex-plugin").mkdir(parents=True)
        (plugin / ".codex-plugin/plugin.json").write_text('{"name":"codex-app-tools"}')
        (plugin / "server.mjs").write_bytes(b"official fixture\x00\xff")
        (plugin / ".mcp.json").write_text('{"mcpServers":{}}')
        (resources / "codex.exe").write_bytes(b"official-core")
        native = resources / "cua_node/bin"
        native.mkdir(parents=True)
        (native / "node.exe").write_bytes(b"official-node")
        (native / "node_repl.exe").write_bytes(b"official-node-repl")
        script = b.BUNDLED_RESOURCES_ENV.encode() if supported else b"no override in this app"
        header = json.dumps({"files":{".vite":{"files":{"build":{"files":{
            "main-test.js":{"offset":"0","size":len(script)}}}}}}}).encode()
        (resources / "app.asar").write_bytes(struct.pack("<4I", 4, len(header)+8, len(header)+4, len(header))+header+script)
        return app

    def test_encrypted_plugins_are_copied_identically_and_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = self.make_app(root)
            original = b.tree_hashes(b.bundled_plugin_source(app))
            with patch.object(b, "STATE", root/"state"), patch.object(b, "encrypted_file", return_value=True), patch.object(b, "log"):
                first = b.prepare_bundled_resources(app)
                second = b.prepare_bundled_resources(app)
            self.assertEqual(first["resources_path"], second["resources_path"])
            self.assertEqual(second["status"], "verified")
            self.assertEqual(b.tree_hashes(Path(first["resources_path"])/"plugins"), original)
            self.assertEqual(b.tree_hashes(b.bundled_plugin_source(app)), original)
            self.assertFalse(any(b.encrypted_file(p) for p in b.regular_tree_files(Path(first["resources_path"])/"plugins")))
            # Regression: a plugins-only root silently removes browser/core runtime discovery.
            self.assertEqual(b.tree_hashes(Path(first["resources_path"])), b.tree_hashes(app.parent/"resources"))
            self.assertTrue((Path(first["resources_path"])/"cua_node/bin/node_repl.exe").is_file())
            self.assertTrue((Path(first["resources_path"])/"codex.exe").is_file())

    def test_runtime_changes_invalidate_cache_even_when_plugins_are_identical(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = self.make_app(root)
            with patch.object(b, "STATE", root/"state"), patch.object(b, "encrypted_file", return_value=True), patch.object(b, "log"):
                old = b.prepare_bundled_resources(app)
                (app.parent/"resources/cua_node/bin/node_repl.exe").write_bytes(b"updated-official-runtime")
                new = b.prepare_bundled_resources(app)
            self.assertNotEqual(old["resources_path"], new["resources_path"])
            self.assertEqual((Path(new["resources_path"])/"cua_node/bin/node_repl.exe").read_bytes(), b"updated-official-runtime")

    def test_native_bridge_preserves_official_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = self.make_app(root)
            bridge = root/"bridge.exe"
            bridge.touch()
            with patch.object(b, "STATE", root/"state"), patch.object(b, "NATIVE_BRIDGE", bridge):
                self.assertEqual(b.prepare_native_bridge(app, {}), str(bridge))
                original = {"NODE_REPL_TRUSTED_SERVICES":"exact-official-value", "SKY_CUA_SERVICE_NATIVE_PIPE_PATH":"official-pipe"}
                with patch.dict(os.environ, original), patch.object(b, "load_settings", return_value={}), patch.object(b, "choose_proxy", return_value=("http://127.0.0.1:7890", "fixture")), patch.object(b, "port_open", return_value=True), patch.object(b.subprocess, "Popen") as start:
                    start.return_value.wait.return_value = 0
                    self.assertEqual(b.run_native_mcp(), 0)
                    actual = start.call_args.kwargs["env"]
                    for key, value in original.items():
                        self.assertEqual(actual[key], value)
                    self.assertEqual(actual["HTTPS_PROXY"], "http://127.0.0.1:7890")
                    self.assertEqual(start.call_args.args[0], [str(app.parent/"resources/cua_node/bin/node_repl.exe")])
                (app.parent/"resources/cua_node/bin/node_repl.exe").write_bytes(b"changed")
                with patch.object(b.subprocess, "Popen") as start:
                    self.assertEqual(b.run_native_mcp(), 1)
                    start.assert_not_called()

    def test_app_update_automatically_selects_new_plugin_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_app = self.make_app(root, "old-version")
            new_app = self.make_app(root, "new-version")
            new_file = b.bundled_plugin_source(new_app)/"openai-bundled/plugins/codex-app-tools/server.mjs"
            new_file.write_text("new official version")
            with patch.object(b, "STATE", root/"state"), patch.object(b, "encrypted_file", return_value=True), patch.object(b, "log"):
                old = b.prepare_bundled_resources(old_app)
                new = b.prepare_bundled_resources(new_app)
            self.assertNotEqual(old["resources_path"], new["resources_path"])
            self.assertEqual(b.tree_hashes(Path(new["resources_path"])/"plugins"), b.tree_hashes(b.bundled_plugin_source(new_app)))
            self.assertTrue(Path(old["resources_path"]).is_dir())

    def test_changed_cached_files_are_not_reused_or_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = self.make_app(root)
            with patch.object(b, "STATE", root/"state"), patch.object(b, "encrypted_file", return_value=True), patch.object(b, "log"):
                old = b.prepare_bundled_resources(app)
                changed = Path(old["resources_path"])/"plugins/openai-bundled/plugins/codex-app-tools/server.mjs"
                changed.write_text("changed after preparation")
                new = b.prepare_bundled_resources(app)
            self.assertNotEqual(old["resources_path"], new["resources_path"])
            self.assertEqual(changed.read_text(), "changed after preparation")
            self.assertEqual(b.tree_hashes(Path(new["resources_path"])/"plugins"), b.tree_hashes(b.bundled_plugin_source(app)))

    def test_unsupported_app_does_not_apply_an_unverified_override(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self.make_app(Path(folder), supported=False)
            with patch.object(b, "encrypted_file", return_value=True), self.assertRaises(b.LauncherError):
                b.prepare_bundled_resources(app)

    def test_unencrypted_app_does_not_need_override(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self.make_app(Path(folder), supported=False)
            with patch.object(b, "encrypted_file", return_value=False):
                self.assertIsNone(b.prepare_bundled_resources(app)["resources_path"])

    def test_junction_plugin_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(Path, "is_junction", return_value=True), self.assertRaises(b.LauncherError):
                b.regular_tree_files(Path(folder))

    def test_old_launch_stamp_requires_restart_after_fix(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            process = {"ProcessId":123,"Created":"timestamp","ExecutablePath":"app.exe",
                       "CommandLine":"app.exe --proxy-server=http://127.0.0.1:7890"}
            saved = {"pid":123,"created":"timestamp","exe":"app.exe","proxy":"http://127.0.0.1:7890"}
            with patch.object(b, "STATE", state):
                b.write_json(state/"last-launch.json", saved)
                self.assertIsNone(b.prior_launch_matches([process], saved["proxy"], "new-copy"))
                saved.update(launcher_version=b.VERSION, bundled_plugins_resources="new-copy")
                b.write_json(state/"last-launch.json", saved)
                self.assertEqual(b.prior_launch_matches([process], saved["proxy"], "new-copy"), 123)
                self.assertIsNone(b.prior_launch_matches([process], saved["proxy"], "updated-copy"))
                self.assertIsNone(b.prior_launch_matches([process], saved["proxy"], "new-copy", "bridge.exe"))
                saved['native_bridge'] = 'bridge.exe'
                b.write_json(state/"last-launch.json", saved)
                self.assertEqual(b.prior_launch_matches([process], saved["proxy"], "new-copy", "bridge.exe"), 123)

    def test_withdrawn_placeholder_is_removed_without_removing_real_server(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = '[model] # unrelated setting\nname = "unchanged"\n'
            withdrawn = {'command':str(b.PYTHON),'args':['-I','-X','utf8',str(b.SCRIPT),'native-placeholder']}
            path=root/'config.toml'
            path.write_text(b.replace_alias(original,withdrawn,'node_repl'),encoding='utf8')
            with patch.object(b,'STATE',root/'state'),patch.object(b,'codex_home',return_value=root):
                self.assertTrue(b.retire_native_placeholder())
                self.assertEqual(tomllib.loads(path.read_text(encoding='utf8')),tomllib.loads(original))
                self.assertTrue((root/'state/native-guardian.stop').exists())
                real=b.replace_alias(original,{'command':'official-node_repl.exe','env':{'official':'preserved'}},'node_repl')
                path.write_text(real,encoding='utf8')
                self.assertFalse(b.retire_native_placeholder())
                self.assertEqual(path.read_text(encoding='utf8'),real)

    def test_diagnostics_distinguish_active_executor_from_old_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            day = root/"2026/10/04"
            day.mkdir(parents=True)
            (day/"desktop-session-999-t0-i1.log").write_text("2026-10-04T08:00:00.000Z info [tpp-local-executor] Local Work executor connected to rendezvous\n")
            log = day/"desktop-session-123-t0-i1.log"
            log.write_text("2026-10-04T08:01:00.000Z warning [tpp-local-executor] Local Work executor startup failed copyfile codex-app-tools\n")
            failed = b.local_work_status([{"ProcessId":123}], root)
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["reason"], "plugin_copy")
            with log.open("a") as f:
                f.write("2026-10-04T08:02:00.000Z info [tpp-local-executor] Local Work executor connected to rendezvous\n")
            self.assertEqual(b.local_work_status([{"ProcessId":123}], root)["status"], "connected")
            with log.open("a") as f:
                f.write("unrelated chat log line\n" * 30000)
            self.assertEqual(b.local_work_status([{"ProcessId":123}], root)["status"], "connected")


class ConfigurationTests(unittest.TestCase):
    def test_proxy_validation_and_credentials_rejected(self):
        self.assertEqual(b.validate_proxy("http://[::1]:7890/"), "http://[::1]:7890")
        for value in ["socks5://127.0.0.1:7890", "http://user:password@localhost:7890", "http://localhost:0",
                      "http://localhost:7890/path", "http://localhost:7890?x=1", "http://localhost:99999",
                      "http://localhost", "http://localhost:7890\n"]:
            with self.subTest(value=value), self.assertRaises(b.LauncherError):
                b.validate_proxy(value)

    def test_auto_proxy_follows_port_change(self):
        settings = {"proxy_mode": "auto", "proxy_url": "http://127.0.0.1:7890"}
        self.assertEqual(b.choose_proxy(settings, "http://127.0.0.1:8899")[0], "http://127.0.0.1:8899")
        self.assertEqual(b.choose_proxy(settings, "")[0], settings["proxy_url"])
        settings["proxy_mode"] = "manual"
        self.assertEqual(b.choose_proxy(settings, "http://127.0.0.1:8899")[0], settings["proxy_url"])

    def test_child_env_drops_stale_lowercase_and_wildcard_bypass(self):
        old = {"HTTP_PROXY": "old", "https_proxy": "old", "all_proxy": "old", "no_proxy": "*", "KEEP": "unchanged"}
        env = b.proxy_env("http://127.0.0.1:7890", old)
        self.assertNotIn("https_proxy", env)
        self.assertEqual(env["NO_PROXY"], "localhost,127.0.0.1,::1")
        self.assertEqual(env["NODE_USE_ENV_PROXY"], "1")
        self.assertEqual(env["KEEP"], "unchanged")
        self.assertEqual(old["no_proxy"], "*")

    def test_toml_preserves_unrelated_tables_and_multiline_text(self):
        src = '''model = "same"\nnotes = """hello\n[mcp_servers.node_repl_proxy]\nnot a table\n"""\n[mcp_servers.node_repl]\ncommand = 'C:\\official\\node_repl.exe'\n[mcp_servers."node_repl_proxy"]\ncommand = "old"\n[mcp_servers.node_repl_proxy.env]\nOLD = "value"\n[mcp_servers.node_repl_proxy_other]\ncommand = "must remain"\n[desktop]\nkeepRemoteControlAwakeWhilePluggedIn = true\n'''
        alias = {"command": "new", "args": ["你好", 'a"b'], "env_vars": [{"name": "X", "source": "local"}]}
        result = b.replace_alias(src, alias)
        parsed = tomllib.loads(result)
        self.assertEqual(parsed["notes"], tomllib.loads(src)["notes"])
        self.assertEqual(parsed["mcp_servers"]["node_repl_proxy"], alias)
        self.assertEqual(parsed["mcp_servers"]["node_repl_proxy_other"]["command"], "must remain")
        self.assertTrue(parsed["desktop"]["keepRemoteControlAwakeWhilePluggedIn"])

    def test_inline_toml_fails_without_silent_rewrite(self):
        with self.assertRaises(b.LauncherError):
            b.replace_alias('mcp_servers = { node_repl_proxy = { command = "old" } }\n', {"command": "new"})

    def test_atomic_config_is_idempotent_and_rollback_keeps_later_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before = 'model = "original"\n[mcp_servers.node_repl]\ncommand = "official"\nenv_vars = ["CURRENT"]\n'
            (root / "config.toml").write_text(before, encoding="utf8")
            with patch.object(b, "codex_home", return_value=root), patch.object(b, "STATE", root/"state"), patch.object(b, "log"):
                self.assertTrue(b.update_config())
                once = (root/"config.toml").read_bytes()
                self.assertFalse(b.update_config())
                self.assertEqual((root/"config.toml").read_bytes(), once)
                text = once.decode().replace('model = "original"', 'model = "later-user-edit"')
                (root/"config.toml").write_bytes(text.encode("utf8"))
                self.assertTrue(b.update_config(restore=True))
                result = tomllib.loads((root/"config.toml").read_text())
                self.assertEqual(result["model"], "later-user-edit")
                self.assertNotIn(b.ALIAS, result["mcp_servers"])
                self.assertFalse(b.update_config(restore=True))

    def test_browser_fallback_selects_newest_equal_official_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for version, equal in [("26.9.9", True), ("26.10.2", True), ("26.11.0", False)]:
                for plugin in ["browser", "chrome"]:
                    file = root/"plugins/cache/openai-bundled"/plugin/version/"scripts/browser-service.mjs"
                    file.parent.mkdir(parents=True)
                    file.write_bytes(b"same" if equal or plugin == "browser" else b"different")
            env = {"NODE_REPL_TRUSTED_SERVICES": json.dumps({"browser": str(root/"missing.mjs"), "sky":"@oai/sky/service"})}
            path = b.browser_fallback(env, root)
            self.assertIn("26.10.2", path)
            self.assertEqual(json.loads(env["NODE_REPL_TRUSTED_SERVICES"])["sky"], "@oai/sky/service")

    def test_browser_missing_pair_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env = {"NODE_REPL_TRUSTED_SERVICES": json.dumps({"browser": str(root/"missing")})}
            with self.assertRaises(b.LauncherError):
                b.browser_fallback(env, root)

    @unittest.skipUnless(os.environ.get("CODEX_PROXY_LIVE_TESTS") == "1", "requires an installed, running Codex desktop")
    def test_powershell_process_detection_observes_actual_desktop(self):
        results = b.app_processes()
        self.assertTrue(isinstance(results, list))
        # This test machine is running Codex. No process is terminated.
        self.assertTrue(any("OpenAI.Codex_" in p["ExecutablePath"] for p in results))

    def test_app_update_is_discovered_without_saved_version_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for version in ["OpenAI.Codex_1", "OpenAI.Codex_2"]:
                app = root/version/"app/ChatGPT.exe"
                app.parent.mkdir(parents=True)
                app.touch()
                with patch.object(b, "run_ps", return_value=json.dumps([str(app.parent.parent)])):
                    self.assertEqual(b.discover_app({}), app)

    def test_actual_child_receives_proxy_settings(self):
        env = b.proxy_env("http://127.0.0.1:7890")
        result = subprocess.run([sys.executable, "-I", "-c",
            "import os,json; print(json.dumps({k:os.environ[k] for k in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','NODE_USE_ENV_PROXY']}))"],
            capture_output=True, text=True, env=env, creationflags=b.HIDDEN, timeout=5, check=True)
        data = json.loads(result.stdout)
        self.assertEqual(data["HTTPS_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(data["NODE_USE_ENV_PROXY"], "1")

    def test_proxy_probe_uses_connect_and_does_not_fall_back(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1",0))
        listener.listen(1)
        seen = []
        def serve():
            connection, _ = listener.accept()
            with connection:
                seen.append(connection.recv(4096))
                connection.sendall(b"HTTP/1.1 502 Intentional-test-failure\r\nContent-Length: 0\r\n\r\n")
            listener.close()
        thread = threading.Thread(target=serve)
        thread.start()
        result = b.probe_https("http://127.0.0.1:"+str(listener.getsockname()[1]), "destination.invalid", timeout=2)
        thread.join(timeout=3)
        self.assertFalse(result["reachable"])
        self.assertIn(b"CONNECT destination.invalid:443", seen[0])

    @unittest.skipUnless(os.environ.get("CODEX_PROXY_LIVE_TESTS") == "1", "requires the installed Codex Node runtime")
    def test_installed_node_fetch_and_http_use_env_proxy(self):
        # Modern per-conversation tools expose the registered runtime via the app.
        registered = os.environ.get("CODEX_MCP_NODE_PATH")
        node = Path(registered) if registered else b.official_runtime()[0].parent / "node.exe"
        self.assertTrue(node.resolve().is_relative_to((Path(os.environ['LOCALAPPDATA'])/'OpenAI/Codex/runtimes/cua_node').resolve()))
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        listener.settimeout(6)
        address = "http://127.0.0.1:" + str(listener.getsockname()[1])
        seen = []
        def serve():
            try:
                for _ in range(2):
                    connection, _ = listener.accept()
                    with connection:
                        seen.append(connection.recv(4096))
                        connection.sendall(b"HTTP/1.1 502 Intentional-test-failure\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            finally:
                listener.close()
        thread = threading.Thread(target=serve)
        thread.start()
        script = "try { await fetch('https://destination.invalid', {signal: AbortSignal.timeout(4000)}); } catch {} "
        script += "await new Promise(r=>{ const q=require('node:http').get('http://destination.invalid/test',x=>{x.resume();r()}); q.on('error',r); q.setTimeout(4000,()=>{q.destroy();r()}) });"
        result = subprocess.run([str(node), "-e", "(async()=>{" + script + "})()"],
                                env=b.proxy_env(address), capture_output=True,
                                creationflags=b.HIDDEN, timeout=10)
        thread.join(timeout=7)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertTrue(any(b"CONNECT destination.invalid:443" in x for x in seen))
        self.assertTrue(any(b"GET http://destination.invalid/test" in x for x in seen))

    def test_configuration_does_not_change_official_security_fields(self):
        fixture = {"mcp_servers": {"node_repl": {"env_vars": ["AUTH_FROM_HOST"], "env": {"NODE_REPL_TRUSTED_SERVICES":"original"}}},
                   "approval_policy":"on-request", "sandbox_mode":"workspace-write"}
        snapshot = json.loads(json.dumps(fixture))
        alias = b.desired_alias(fixture)
        self.assertEqual(fixture, snapshot)
        self.assertIn("AUTH_FROM_HOST", alias["env_vars"])
        self.assertNotIn("env", alias)


if __name__ == "__main__":
    unittest.main(verbosity=2)
