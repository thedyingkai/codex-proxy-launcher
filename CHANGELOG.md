# Changelog

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
