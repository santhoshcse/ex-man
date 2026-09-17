# Executable Manager

`ex-man` discovers Windows executable files from custom folders or common installation locations, stores a local JSON catalog, and lets you search it later.

## Commands

Run a targeted recursive scan:

	D:/Apps/Python/Python313/python.exe main.py --cache catalog.json scan --root D:\Tools --root D:\Scripts

Run a default scan with PATH, Python, npm, uv, Scoop, Chocolatey, and winget locations:

	D:/Apps/Python/Python313/python.exe main.py scan

Search the existing catalog:

	D:/Apps/Python/Python313/python.exe main.py find claude

Refresh a custom root before searching, restrict results, and inspect one result:

	D:/Apps/Python/Python313/python.exe main.py --cache catalog.json find --fresh --root D:\Tools claude --extension .exe
	D:/Apps/Python/Python313/python.exe main.py --cache catalog.json show RECORD_ID

Use `--json` before the command for structured output. `scan` recognizes `.exe`, `.com`, `.bat`, `.cmd`, and `.ps1` by default; override this with `--extensions .exe,.cmd`.

## Behavior

The default catalog is written to `%LOCALAPPDATA%\ExecutableManager\catalog.json`. Cache writes use a temporary file followed by replacement, so an interrupted write does not leave partial JSON at the catalog path.

Directory roots are scanned concurrently with a bounded thread pool. Inaccessible paths are retained as diagnostics in the catalog without preventing results from other roots. Scanning does not change PATH, launch any executable, or install software.

When `--root` is supplied, only the listed custom roots are scanned. Without `--root`, the built-in Windows source locations are used.
