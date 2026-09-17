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

Review catalog quality, including same-name conflicts, merged source provenance, potential wrappers/shims, and records changed or removed since scanning:

	D:/Apps/Python/Python313/python.exe main.py --cache catalog.json quality

Verify current catalog paths, or compute an opt-in SHA-256 checksum for a specific record:

	D:/Apps/Python/Python313/python.exe main.py verify
	D:/Apps/Python/Python313/python.exe main.py verify --record-id RECORD_ID --hash

Create and inspect a user configuration file, then report sources or export the existing catalog:

	D:/Apps/Python/Python313/python.exe main.py config init
	D:/Apps/Python/Python313/python.exe main.py config show
	D:/Apps/Python/Python313/python.exe main.py sources
	D:/Apps/Python/Python313/python.exe main.py export --format csv --output executables.csv

Use `--json` before the command for structured output. `scan` recognizes `.exe`, `.com`, `.bat`, `.cmd`, and `.ps1` by default; override this with `--extensions .exe,.cmd`.

## Behavior

The default catalog is written to `%LOCALAPPDATA%\ExecutableManager\catalog.json`. Cache writes use a temporary file followed by replacement, so an interrupted write does not leave partial JSON at the catalog path.

Compatible older catalog schemas are migrated automatically when loaded and rewritten atomically. Before an upgrade, the original is copied beside the active catalog with a version-and-datetime suffix such as `catalog.v1.20260917T142030123456+0530.json`. An unsupported or malformed catalog still produces an actionable error rather than discarding data.

Directory roots are scanned concurrently with a bounded thread pool. Refreshes take a per-catalog lock, waiting up to 30 seconds by default, so concurrent `scan` and `find --fresh` operations cannot replace each other's catalog. Inaccessible paths are retained as diagnostics with a cause such as `permission-denied`, `unavailable`, `broken-link`, or `io-error` without preventing results from other roots. Scanning does not change PATH, launch any executable, or install software.

Records with the same canonical path are consolidated and retain every source/root that found them. Executables with the same normalized name but different paths are reported by `quality`; a `.cmd`, `.bat`, or `.ps1` candidate with the same name is labeled only as a possible wrapper or shim, never selected automatically.

When `--root` is supplied, only the listed custom roots are scanned. Without `--root`, the built-in Windows source locations are used.

## Configuration And Reporting

The default configuration path is `%LOCALAPPDATA%\ExecutableManager\config.json`. Run `config init` to create it. The file configures the default catalog path, whether built-in locations are scanned, enabled built-in source labels, configured custom roots, custom-root recursion, worker count, and executable extensions. Command-line `--cache`, `--workers`, and `--extensions` override configuration values.

`sources` reports the catalog's source labels, roots, discovered-record counts, and diagnostics. `export` writes the current catalog to standard output or a specified JSON/CSV file, including each record's current filesystem status and merged provenance.

`verify` checks each catalog path against live file metadata and returns a non-zero exit code when a record is missing, inaccessible, or changed. `--hash` is intentionally opt-in because hashing executable files can be expensive.
