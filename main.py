"""Discover Windows executables and query a local catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import sysconfig
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable, Iterable, TypedDict, cast


CATALOG_SCHEMA_VERSION = 2
DEFAULT_EXTENSIONS = (".exe", ".com", ".bat", ".cmd", ".ps1")
WRAPPER_EXTENSIONS = frozenset((".bat", ".cmd", ".ps1"))


class Provenance(TypedDict):
    source: str
    root: str


class ExecutableRecord(TypedDict):
    id: str
    name: str
    normalized_name: str
    filename: str
    path: str
    extension: str
    discovered_at: str
    size: int
    modified_at: str
    modified_ns: int
    sources: list[Provenance]


class Catalog(TypedDict):
    schema_version: int
    scanned_at: str
    records: list[ExecutableRecord]
    diagnostics: list[dict[str, str]]


@dataclass(frozen=True)
class RootSpec:
    path: Path
    source: str
    recursive: bool


@dataclass(frozen=True)
class ScanDiagnostic:
    source: str
    root: str
    message: str


def utc_now() -> str:
    return datetime.now().astimezone().isoformat()


def default_cache_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base_directory = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return base_directory / "ExecutableManager" / "catalog.json"


def canonical_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def executable_id(path: Path) -> str:
    return hashlib.sha256(canonical_path(path).encode("utf-8")).hexdigest()[:16]


def parse_extensions(value: str) -> tuple[str, ...]:
    extensions = []
    for extension in value.split(","):
        normalized = extension.strip().lower()
        if not normalized:
            continue
        extensions.append(normalized if normalized.startswith(".") else f".{normalized}")
    if not extensions:
        raise argparse.ArgumentTypeError("at least one extension is required")
    return tuple(dict.fromkeys(extensions))


def unique_root_specs(specifications: Iterable[RootSpec]) -> list[RootSpec]:
    seen: set[tuple[str, bool]] = set()
    roots: list[RootSpec] = []
    for specification in specifications:
        key = (canonical_path(specification.path), specification.recursive)
        if key not in seen:
            seen.add(key)
            roots.append(specification)
    return roots


def default_root_specs() -> list[RootSpec]:
    environment = os.environ
    roots: list[RootSpec] = []

    for directory in environment.get("PATH", "").split(os.pathsep):
        if directory.strip():
            roots.append(RootSpec(Path(directory), "PATH", False))

    python_scripts = Path(sysconfig.get_path("scripts"))
    roots.append(RootSpec(python_scripts, "Python scripts", False))
    user_base = environment.get("PYTHONUSERBASE")
    if user_base:
        roots.append(RootSpec(Path(user_base) / "Scripts", "Python user scripts", False))
    else:
        roots.append(RootSpec(Path.home() / "AppData" / "Roaming" / "Python" / "Scripts", "Python user scripts", False))

    app_data = Path(environment.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    local_app_data = Path(environment.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    program_data = Path(environment.get("ProgramData", "C:/ProgramData"))
    user_profile = Path(environment.get("USERPROFILE", Path.home()))
    scoop_root = Path(environment.get("SCOOP", user_profile / "scoop"))
    chocolatey_root = Path(environment.get("ChocolateyInstall", program_data / "chocolatey"))

    roots.extend(
        [
            RootSpec(app_data / "npm", "npm global", False),
            RootSpec(local_app_data / "uv" / "tools", "uv tools", True),
            RootSpec(app_data / "uv" / "tools", "uv tools", True),
            RootSpec(scoop_root / "shims", "Scoop", False),
            RootSpec(chocolatey_root / "bin", "Chocolatey", False),
            RootSpec(local_app_data / "Microsoft" / "WinGet" / "Links", "winget", False),
        ]
    )
    return unique_root_specs(roots)


def build_record(path: Path, root: RootSpec, discovered_at: str) -> ExecutableRecord:
    stat_result = path.stat()
    return {
        "id": executable_id(path),
        "name": path.stem,
        "normalized_name": path.stem.casefold(),
        "filename": path.name,
        "path": str(path.resolve(strict=False)),
        "extension": path.suffix.lower(),
        "discovered_at": discovered_at,
        "size": stat_result.st_size,
        "modified_at": datetime.fromtimestamp(stat_result.st_mtime).astimezone().isoformat(),
        "modified_ns": stat_result.st_mtime_ns,
        "sources": [{"source": root.source, "root": str(root.path)}],
    }


def scan_root(root: RootSpec, extensions: tuple[str, ...], discovered_at: str) -> tuple[list[ExecutableRecord], list[ScanDiagnostic]]:
    records: list[ExecutableRecord] = []
    diagnostics: list[ScanDiagnostic] = []
    pending = [root.path]
    visited: set[str] = set()

    while pending:
        directory = pending.pop()
        directory_key = canonical_path(directory)
        if directory_key in visited:
            continue
        visited.add(directory_key)
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if root.recursive:
                                pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            path = Path(entry.path)
                            if path.suffix.lower() in extensions:
                                records.append(build_record(path, root, discovered_at))
                    except OSError as error:
                        diagnostics.append(ScanDiagnostic(root.source, entry.path, str(error)))
        except OSError as error:
            diagnostics.append(ScanDiagnostic(root.source, str(directory), str(error)))

    return records, diagnostics


def consolidate_records(records: list[ExecutableRecord]) -> list[ExecutableRecord]:
    merged_records: dict[str, ExecutableRecord] = {}
    for record in records:
        existing = merged_records.get(record["id"])
        if existing is None:
            merged_records[record["id"]] = record
            continue

        known_sources = {(source["source"], source["root"]) for source in existing["sources"]}
        for source in record["sources"]:
            source_key = (source["source"], source["root"])
            if source_key not in known_sources:
                existing["sources"].append(source)
                known_sources.add(source_key)

    for record in merged_records.values():
        record["sources"].sort(key=lambda source: (source["source"].casefold(), source["root"].casefold()))
    return sorted(merged_records.values(), key=lambda record: (record["normalized_name"], record["path"].casefold()))


def scan_roots(roots: list[RootSpec], extensions: tuple[str, ...], workers: int) -> tuple[list[ExecutableRecord], list[dict[str, str]]]:
    discovered_at = utc_now()
    records: list[ExecutableRecord] = []
    diagnostics: list[ScanDiagnostic] = []
    worker_count = max(1, min(workers, len(roots) or 1))

    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="exman-scan") as executor:
        futures = [executor.submit(scan_root, root, extensions, discovered_at) for root in roots]
        for future in as_completed(futures):
            root_records, root_diagnostics = future.result()
            records.extend(root_records)
            diagnostics.extend(root_diagnostics)

    ordered_records = consolidate_records(records)
    ordered_diagnostics = sorted((asdict(diagnostic) for diagnostic in diagnostics), key=lambda diagnostic: (diagnostic["source"], diagnostic["root"]))
    return ordered_records, ordered_diagnostics


def write_catalog(
    cache_path: Path,
    records: list[ExecutableRecord],
    diagnostics: list[dict[str, str]],
    scanned_at: str | None = None,
) -> Catalog:
    catalog: Catalog = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "scanned_at": scanned_at or utc_now(),
        "records": records,
        "diagnostics": diagnostics,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=cache_path.parent, delete=False) as temporary_file:
        json.dump(catalog, temporary_file, indent=2)
        temporary_file.write("\n")
        temporary_path = Path(temporary_file.name)
    temporary_path.replace(cache_path)
    return catalog


def migrate_v1_to_v2(catalog: dict[str, object]) -> dict[str, object]:
    legacy_records = catalog.get("records")
    if not isinstance(legacy_records, list):
        raise ValueError("catalog v1 has invalid records")

    migrated_records: list[ExecutableRecord] = []
    for legacy_record in legacy_records:
        if not isinstance(legacy_record, dict):
            raise ValueError("catalog v1 contains an invalid record")
        path = str(legacy_record.get("path", ""))
        if not path:
            raise ValueError("catalog v1 contains a record without a path")

        path_object = Path(path)
        try:
            modified_ns = path_object.stat().st_mtime_ns
        except OSError:
            modified_ns = 0

        name = str(legacy_record.get("name") or path_object.stem)
        source = str(legacy_record.get("source", "Unknown"))
        source_root = str(legacy_record.get("source_root", ""))
        migrated_records.append(
            {
                "id": str(legacy_record.get("id") or executable_id(path_object)),
                "name": name,
                "normalized_name": str(legacy_record.get("normalized_name") or name.casefold()),
                "filename": str(legacy_record.get("filename") or path_object.name),
                "path": path,
                "extension": str(legacy_record.get("extension") or path_object.suffix.lower()),
                "discovered_at": str(legacy_record.get("discovered_at") or catalog.get("scanned_at") or utc_now()),
                "size": int(legacy_record.get("size", 0)),
                "modified_at": str(legacy_record.get("modified_at", "")),
                "modified_ns": modified_ns,
                "sources": [{"source": source, "root": source_root}],
            }
        )

    diagnostics = catalog.get("diagnostics", [])
    if not isinstance(diagnostics, list):
        diagnostics = []
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "scanned_at": str(catalog.get("scanned_at") or utc_now()),
        "records": consolidate_records(migrated_records),
        "diagnostics": cast(list[dict[str, str]], diagnostics),
    }


CatalogMigration = Callable[[dict[str, object]], dict[str, object]]
CATALOG_MIGRATIONS: dict[int, CatalogMigration] = {
    1: migrate_v1_to_v2,
}


def backup_catalog(cache_path: Path, schema_version: int) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    backup_path = cache_path.with_name(f"{cache_path.stem}.v{schema_version}.{timestamp}{cache_path.suffix}")
    try:
        shutil.copy2(cache_path, backup_path)
    except OSError as error:
        raise ValueError(f"could not back up catalog before migration: {error}") from error
    return backup_path


def migrate_catalog(catalog: dict[str, object]) -> Catalog:
    schema_version = catalog.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise ValueError("catalog has an invalid schema version")
    if schema_version > CATALOG_SCHEMA_VERSION:
        raise ValueError(
            f"catalog schema v{schema_version} is newer than this CLI supports (v{CATALOG_SCHEMA_VERSION})"
        )

    migrated_catalog = catalog
    while schema_version < CATALOG_SCHEMA_VERSION:
        migration = CATALOG_MIGRATIONS.get(schema_version)
        if migration is None:
            raise ValueError(f"catalog schema v{schema_version} cannot be migrated automatically")
        migrated_catalog = migration(migrated_catalog)
        next_schema_version = migrated_catalog.get("schema_version")
        if not isinstance(next_schema_version, int) or next_schema_version <= schema_version:
            raise ValueError(f"catalog migration from v{schema_version} did not advance the schema version")
        schema_version = next_schema_version
    return cast(Catalog, migrated_catalog)


def load_catalog(cache_path: Path) -> Catalog:
    try:
        with cache_path.open(encoding="utf-8") as cache_file:
            catalog = json.load(cache_file)
    except FileNotFoundError as error:
        raise ValueError(f"catalog not found: {cache_path}; run 'scan' first") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"catalog is not valid JSON: {cache_path}") from error
    schema_version = catalog.get("schema_version")
    if isinstance(schema_version, int) and not isinstance(schema_version, bool) and schema_version < CATALOG_SCHEMA_VERSION:
        backup_path = backup_catalog(cache_path, schema_version)
        migrated_catalog = migrate_catalog(catalog)
        updated_catalog = write_catalog(
            cache_path,
            migrated_catalog["records"],
            migrated_catalog["diagnostics"],
            migrated_catalog["scanned_at"],
        )
        print(
            f"Migrated catalog v{schema_version} to v{CATALOG_SCHEMA_VERSION}; backup: {backup_path}",
            file=sys.stderr,
        )
        return updated_catalog
    if schema_version != CATALOG_SCHEMA_VERSION or not isinstance(catalog.get("records"), list):
        raise ValueError(f"catalog has an unsupported schema: {cache_path}")
    return cast(Catalog, catalog)


def find_records(records: list[ExecutableRecord], query: str, source: str | None, extension: str | None) -> list[ExecutableRecord]:
    normalized_query = query.casefold()
    normalized_extension = extension.lower() if extension else None
    matches = []
    for record in records:
        if normalized_query not in record["normalized_name"]:
            continue
        if source and not any(provenance["source"].casefold() == source.casefold() for provenance in record["sources"]):
            continue
        if normalized_extension and record["extension"].lower() != normalized_extension:
            continue
        matches.append(record)
    return matches


def print_records(records: list[ExecutableRecord], as_json: bool) -> None:
    if as_json:
        print(json.dumps(records, indent=2))
        return
    if not records:
        print("No matching executables found.")
        return
    for record in records:
        source_names = ", ".join(provenance["source"] for provenance in record["sources"])
        print(f"{record['id']}  {record['filename']}  [{source_names}]\n  {record['path']}")


def record_status(record: ExecutableRecord) -> str:
    try:
        stat_result = Path(record["path"]).stat()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "inaccessible"
    if stat_result.st_size != record["size"] or stat_result.st_mtime_ns != record["modified_ns"]:
        return "changed"
    return "current"


def catalog_quality(records: list[ExecutableRecord]) -> dict[str, object]:
    grouped_by_name: dict[str, list[ExecutableRecord]] = {}
    for record in records:
        grouped_by_name.setdefault(record["normalized_name"], []).append(record)

    multi_source_paths = [
        {
            "id": record["id"],
            "path": record["path"],
            "sources": record["sources"],
        }
        for record in records
        if len(record["sources"]) > 1
    ]
    name_conflicts = []
    for name, candidates in sorted(grouped_by_name.items()):
        if len(candidates) < 2:
            continue
        wrappers = [candidate["id"] for candidate in candidates if candidate["extension"] in WRAPPER_EXTENSIONS]
        name_conflicts.append(
            {
                "name": name,
                "record_ids": [candidate["id"] for candidate in candidates],
                "paths": [candidate["path"] for candidate in candidates],
                "possible_wrapper_or_shim_ids": wrappers,
            }
        )
    stale_records = [
        {"id": record["id"], "path": record["path"], "status": status}
        for record in records
        if (status := record_status(record)) != "current"
    ]
    return {
        "summary": {
            "records": len(records),
            "multi_source_paths": len(multi_source_paths),
            "name_conflicts": len(name_conflicts),
            "stale_records": len(stale_records),
        },
        "multi_source_paths": multi_source_paths,
        "name_conflicts": name_conflicts,
        "stale_records": stale_records,
    }


def print_quality_report(report: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2))
        return
    summary = cast(dict[str, int], report["summary"])
    print(
        f"Records: {summary['records']} | Multi-source paths: {summary['multi_source_paths']} | "
        f"Name conflicts: {summary['name_conflicts']} | Stale: {summary['stale_records']}"
    )
    for conflict in cast(list[dict[str, object]], report["name_conflicts"]):
        print(f"\nName conflict: {conflict['name']}")
        for path in cast(list[str], conflict["paths"]):
            print(f"  {path}")
        wrappers = cast(list[str], conflict["possible_wrapper_or_shim_ids"])
        if wrappers:
            print(f"  Possible wrappers/shims: {', '.join(wrappers)}")
    for stale in cast(list[dict[str, str]], report["stale_records"]):
        print(f"\n{stale['status'].title()}: {stale['path']}")


def refresh_catalog(arguments: argparse.Namespace) -> tuple[Catalog, int]:
    roots = [RootSpec(Path(root), "Custom", True) for root in arguments.root] if arguments.root else default_root_specs()
    unique_roots = unique_root_specs(roots)
    records, diagnostics = scan_roots(unique_roots, arguments.extensions, arguments.workers)
    catalog = write_catalog(arguments.cache, records, diagnostics)
    return catalog, len(unique_roots)


def command_scan(arguments: argparse.Namespace) -> int:
    catalog, root_count = refresh_catalog(arguments)
    if arguments.json:
        print(json.dumps(catalog, indent=2))
    else:
        print(f"Discovered {len(catalog['records'])} executable(s) from {root_count} root(s).")
        if catalog["diagnostics"]:
            print(f"Skipped {len(catalog['diagnostics'])} inaccessible or unavailable path(s).")
        print(f"Catalog: {arguments.cache}")
    return 0


def command_find(arguments: argparse.Namespace) -> int:
    if arguments.fresh:
        refresh_catalog(arguments)
    catalog = load_catalog(arguments.cache)
    matches = find_records(catalog["records"], arguments.query, arguments.source, arguments.extension)
    print_records(matches, arguments.json)
    return 0 if matches else 1


def command_show(arguments: argparse.Namespace) -> int:
    catalog = load_catalog(arguments.cache)
    selected = next((record for record in catalog["records"] if record["id"] == arguments.record_id), None)
    if selected is None:
        print(f"No executable with ID '{arguments.record_id}' in {arguments.cache}.", file=sys.stderr)
        return 1
    details = dict(selected)
    details["current_status"] = record_status(selected)
    print(json.dumps(details, indent=2) if arguments.json else "\n".join(f"{key}: {value}" for key, value in details.items()))
    return 0


def command_quality(arguments: argparse.Namespace) -> int:
    catalog = load_catalog(arguments.cache)
    print_quality_report(catalog_quality(catalog["records"]), arguments.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover Windows executables and query a local catalog.")
    parser.add_argument("--cache", type=Path, default=default_cache_path(), help="catalog JSON path")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="scan executable locations and update the catalog")
    scan_parser.add_argument("--root", action="append", default=[], help="custom root to scan recursively; repeatable")
    scan_parser.add_argument("--workers", type=int, default=min(8, (os.cpu_count() or 1) + 2), help="maximum scan workers")
    scan_parser.add_argument("--extensions", type=parse_extensions, default=DEFAULT_EXTENSIONS, help="comma-separated executable extensions")
    scan_parser.set_defaults(handler=command_scan)

    find_parser = subparsers.add_parser("find", help="search the catalog by executable name")
    find_parser.add_argument("query", help="case-insensitive substring to search")
    find_parser.add_argument("--fresh", action="store_true", help="scan before searching")
    find_parser.add_argument("--root", action="append", default=[], help="custom root for --fresh; repeatable")
    find_parser.add_argument("--workers", type=int, default=min(8, (os.cpu_count() or 1) + 2), help="maximum scan workers for --fresh")
    find_parser.add_argument("--extensions", type=parse_extensions, default=DEFAULT_EXTENSIONS, help="extensions for --fresh")
    find_parser.add_argument("--source", help="only search one source label")
    find_parser.add_argument("--extension", help="only search one extension, such as .exe")
    find_parser.set_defaults(handler=command_find)

    show_parser = subparsers.add_parser("show", help="display details for a catalog record")
    show_parser.add_argument("record_id", help="record ID returned by find")
    show_parser.set_defaults(handler=command_show)

    quality_parser = subparsers.add_parser("quality", help="report catalog collisions, provenance, and stale records")
    quality_parser.set_defaults(handler=command_quality)
    return parser


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()
    try:
        return arguments.handler(arguments)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Scan interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
