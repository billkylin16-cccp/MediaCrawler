# Windows installer build

This build is for the small, date-bounded Douyin opinion-monitor workflow. It
retains the repository's NON-COMMERCIAL LEARNING LICENSE 1.1 and is not a grant
of commercial distribution rights.

## Build prerequisites

- Windows x64
- uv
- Inno Setup 6 (only required for the final Setup.exe)

End users do not need any of those tools. The produced application contains its
own Python runtime, Playwright-provided Node.js runtime, Chromium, OCR models, and
application dependencies.

## Build

```powershell
uv run python -m playwright install chromium
powershell -ExecutionPolicy Bypass -File .\packaging\windows\build_release.ps1
```

Outputs are written to `packaging/output/`. Use `-SkipInstaller` to build only
the self-contained application folder for diagnostics. Every build runs a
packaged self-test that loads the monitoring module, initializes all OCR models,
and launches the bundled Chromium in headless mode before creating the installer.

The final files are:

- `DouyinOpinionMonitor-<version>-win-x64-setup.exe`
- `DouyinOpinionMonitor-<version>-win-x64-setup.exe.sha256`

The current beta is intentionally unsigned. Windows SmartScreen may warn users;
production distribution should use an Authenticode code-signing certificate and
sign both the application executable and the installer.

## Release acceptance

Test on a clean Windows 10/11 x64 virtual machine without Python, Node.js, uv,
Chrome, or an existing MediaCrawler checkout. Verify first-run license notice,
QR login, persisted login, watchlist editing, both monitoring modes, image OCR,
Excel generation, upgrade preservation, and uninstall behavior.
