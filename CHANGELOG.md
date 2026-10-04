# Changelog

## 1.0.4 — Edge verified; prerelease

- Preserve the complete installed resources tree, including core and browser executables. A plugins-only resources override caused `node-repl-missing` and Chrome runtime reconciliation errors.
- Use the installed app's native executable override to launch the real official MCP through a transparent proxy bridge. Preserve official trust and identity fields, add only proxy environment, and contain child processes in a Windows job.
- Verify the selected official executable by hash. A changed executable or unavailable proxy fails without a direct fallback.
- Stop and remove only this installation's withdrawn 1.0.3 placeholder. Do not create another placeholder or configuration guardian.
- Restore the separately named dynamic proxy connection used by the previously successful Edge repair. Read current official paths and environment on each connection; retain the alias when the app manages native tools.
- Validate real MCP initialization, four non-empty native tools, actual native process proxy variables, and child cleanup. Both the default and independent connections actually opened an Edge page, read its content, clicked a link, and read the IANA destination.
- Run 28 focused tests (26 passed, two opt-in live checks skipped in the last run; both live checks passed earlier). Cold-start persistence, another dots conversation, and the next real desktop update remain unverified.

## 1.0.3 — withdrawn

- The placeholder-based environment overlay failed desktop integration: the incomplete resource override prevented generation of the actual native transport, leaving an empty tool server.
- Withdrawn from public releases and reverted. Earlier isolated configuration tests did not establish desktop browser functionality.

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
