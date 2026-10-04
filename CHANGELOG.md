# Changelog

## 1.0.3 (pre-release; browser interaction verification pending)

- Correct the assumption that desktop-managed MCP tools inherit the main app's proxy environment. Their environment allowlist removes it.
- Merge only proxy environment values into native per-conversation tool configuration, preserving the app-supplied program, arguments, authentication, services, and permissions.
- Keep the user configuration valid for non-desktop clients through an inert MCP fallback with no tools. Desktop thread overrides supply the real transport.
- Reapply this launcher's environment overlay when the desktop regenerates its configuration, using a hidden helper that exits with Codex; no scheduled task or system service.
- Require one normal restart when migrating to the new proxy environment revision. Existing processes cannot receive changed environment variables.
- Pass 29 regression tests and an isolated integration test with the installed Codex core proving configuration merging. End-to-end Edge navigation and clicking must still be verified after reloading the current tool processes.

## 1.0.2

- Recognize desktop builds that generate trusted tool configuration per conversation, without requiring a persisted node_repl entry.
- Remove only this launcher's obsolete wrapper on those builds, while retaining the app's native tool and trust settings.
- Correct diagnostics for this mode and retain already-applied launch settings across a diagnostic-only launcher update.
- Confirm Local Work connected successfully on the first post-fix startup; add two migration tests (24 tests total).

## 1.0.1

- Automatically materialize and verify bundled plugin files when Store encryption makes native copying fail during Local Work startup.
- Discover current installation and content on every launch; automatically switch the plugin copy after application updates. No application version is pinned.
- Rebuild a changed cache without modifying a cache that a running app may still use.
- Include current-process Local Work readiness in diagnostics and post-launch checks.
- Require restart when the launcher configuration or plugin source changes, even when the proxy address is unchanged.
- Add eight regression tests for update handling, copy verification, cache changes, and readiness reporting.

## 1.0.0 — 2026-10-04

- Windows GUI for checking a local proxy and launching Codex with explicit proxy settings.
- Dynamic discovery of the current Microsoft Store installation.
- Optional startup of a user-selected proxy application.
- Independent official MCP proxy launcher with current runtime discovery.
- Configuration backup, semantic validation, idempotent updates, and focused rollback.
- Diagnostics for HTTPS transport and official MCP initialization.
- Portable runtime, packaging script with runtime checksum verification, and separate offline / local-integration tests.
