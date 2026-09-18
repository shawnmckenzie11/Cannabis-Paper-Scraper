#!/usr/bin/env python3
"""Logical Postgres backup/restore helpers (pg_dump / pg_restore / psql).

Aligns with this repo's existing Fly proxy convention
(``cannabis-papers-db`` via ``scripts/run_with_fly_proxy_venv.sh``).

Restore never targets the live Fly cluster unless both ``--allow-prod``
and ``RESTORE_CONFIRM_PROD=I_UNDERSTAND_THIS_OVERWRITES_PRODUCTION`` are
set. The default drill is ``--dry-run`` or restore into a localhost
scratch database.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_DUMP_DIR = Path("scratch/postgres_backups")
FLY_APP = os.getenv("FLY_APP", "cannabis-paper-scraper")
FLY_POSTGRES_APP = os.getenv("FLY_POSTGRES_APP", "cannabis-papers-db")
PROD_CONFIRM = "I_UNDERSTAND_THIS_OVERWRITES_PRODUCTION"

LIVE_HOST_MARKERS = (
    "cannabis-papers-db",
    "flycast",
    ".fly.dev",
    ".internal",
)


class BackupError(RuntimeError):
    """Raised when a backup or restore command cannot run safely."""


def parse_database_url(url: str) -> Dict[str, str]:
    """Parse a Postgres URL into libpq connection fields.

    Args:
        url: ``postgres://`` or ``postgresql://`` URL.

    Returns:
        Dict with host, port, user, password, database, and redacted label.

    Raises:
        BackupError: The URL is missing or not Postgres.
    """
    raw = (url or "").strip()
    if not raw:
        raise BackupError(
            "DATABASE_URL is unset. Export it, or run via "
            "./scripts/run_with_fly_proxy_venv.sh ./scripts/backup_postgres.sh"
        )
    parsed = urlparse(raw)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise BackupError(
            f"DATABASE_URL must be a Postgres URL, got scheme={parsed.scheme!r}. "
            "SQLite files are not backed up by this tool."
        )
    database = (parsed.path or "").lstrip("/")
    if not database:
        raise BackupError("DATABASE_URL has no database name.")
    host = parsed.hostname or "127.0.0.1"
    port = str(parsed.port or 5432)
    user = unquote(parsed.username or "postgres")
    password = unquote(parsed.password or "")
    label = f"{parsed.scheme}://{host}:{port}/{database}"
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
        "label": label,
        "url": raw,
    }


def looks_like_live_prod(url: str) -> bool:
    """Return True when the URL hostname looks like the live Fly cluster."""
    host = (urlparse(url).hostname or "").lower()
    return any(marker in host for marker in LIVE_HOST_MARKERS)


def is_localhost(url: str) -> bool:
    """Return True when the URL points at a local scratch Postgres."""
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def require_pg_tool(name: str) -> str:
    """Return the path to a client binary or raise BackupError."""
    path = shutil.which(name)
    if not path:
        raise BackupError(
            f"{name} is not on PATH. Install the PostgreSQL client tools "
            "(e.g. apt install postgresql-client) and retry."
        )
    return path


def default_dump_path(fmt: str, stamp: Optional[str] = None) -> Path:
    """Return the default dump path under scratch/postgres_backups/."""
    when = stamp or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = {"custom": ".dump", "plain": ".sql", "directory": ""}.get(fmt, ".dump")
    name = f"cannabis_papers_{when}{suffix}"
    return DEFAULT_DUMP_DIR / name


def sidecar_path(dump_path: Path) -> Path:
    """Return the JSON sidecar path for a dump file or directory."""
    if dump_path.suffix:
        return dump_path.with_suffix(dump_path.suffix + ".meta.json")
    return dump_path.parent / f"{dump_path.name}.meta.json"


def _pg_env(conn: Dict[str, str]) -> Dict[str, str]:
    """Build an env dict with PGPASSWORD for subprocesses."""
    env = os.environ.copy()
    if conn["password"]:
        env["PGPASSWORD"] = conn["password"]
    return env


def _run(cmd: List[str], *, env: Dict[str, str], dry_run: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command, streaming output, and raise BackupError on failure."""
    printable = " ".join(cmd)
    print(f"+ {printable}")
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    proc = subprocess.run(cmd, env=env, text=True)
    if proc.returncode != 0:
        raise BackupError(f"Command failed ({proc.returncode}): {printable}")
    return proc


def count_papers(conn: Dict[str, str]) -> Optional[int]:
    """Return papers COUNT(*) when the table exists, else None."""
    psql = require_pg_tool("psql")
    cmd = [
        psql,
        "-h", conn["host"],
        "-p", conn["port"],
        "-U", conn["user"],
        "-d", conn["database"],
        "-At",
        "-c",
        "SELECT COUNT(*) FROM papers",
    ]
    proc = subprocess.run(cmd, env=_pg_env(conn), text=True, capture_output=True)
    if proc.returncode != 0:
        return None
    try:
        return int((proc.stdout or "0").strip().splitlines()[-1])
    except (TypeError, ValueError, IndexError):
        return None


def dump_database(
    *,
    url: Optional[str] = None,
    output: Optional[Path] = None,
    fmt: str = "custom",
    dry_run: bool = False,
) -> Path:
    """Run pg_dump against DATABASE_URL and write a sidecar metadata file.

    Args:
        url: Postgres URL. Defaults to DATABASE_URL.
        output: Destination file (custom/plain) or directory (directory).
        fmt: ``custom`` (pg_restore), ``plain`` (psql), or ``directory``.
        dry_run: Print the pg_dump command without writing a dump.

    Returns:
        Path to the dump file or directory.
    """
    if fmt not in {"custom", "plain", "directory"}:
        raise BackupError(f"Unsupported dump format: {fmt}")
    conn = parse_database_url(url or os.getenv("DATABASE_URL") or "")
    dest = Path(output) if output else default_dump_path(fmt)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pg_dump = require_pg_tool("pg_dump")
    fmt_flag = {"custom": "-Fc", "plain": "-Fp", "directory": "-Fd"}[fmt]
    cmd = [
        pg_dump,
        "-h", conn["host"],
        "-p", conn["port"],
        "-U", conn["user"],
        "-d", conn["database"],
        "--no-owner",
        "--no-acl",
        fmt_flag,
        "-f", str(dest),
    ]
    print(f"Backing up {conn['label']} → {dest} (format={fmt})")
    _run(cmd, env=_pg_env(conn), dry_run=dry_run)
    if dry_run:
        return dest
    papers = count_papers(conn)
    meta: Dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "format": fmt,
        "source": conn["label"],
        "fly_postgres_app": FLY_POSTGRES_APP,
        "fly_app": FLY_APP,
        "output": str(dest),
        "bytes": dest.stat().st_size if dest.is_file() else None,
        "paper_count": papers,
    }
    sidecar = sidecar_path(dest)
    sidecar.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {dest}")
    print(f"Wrote {sidecar}")
    if papers is not None:
        print(f"source paper_count={papers}")
    return dest


def list_dump(dump_path: Path, fmt: Optional[str] = None) -> str:
    """Return a TOC (custom/directory) or a plain-SQL header preview."""
    path = Path(dump_path)
    if not path.exists():
        raise BackupError(f"Dump not found: {path}")
    resolved_fmt = fmt or infer_format(path)
    if resolved_fmt == "plain":
        text = path.read_text(encoding="utf-8", errors="replace")
        preview = "\n".join(text.splitlines()[:40])
        if "PostgreSQL database dump" not in text[:4000] and "pg_dump" not in text[:4000]:
            raise BackupError(
                f"{path} does not look like a pg_dump plain SQL file."
            )
        return preview
    pg_restore = require_pg_tool("pg_restore")
    proc = subprocess.run(
        [pg_restore, "--list", str(path)],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise BackupError(
            f"pg_restore --list failed for {path}: {proc.stderr.strip()}"
        )
    return proc.stdout


def infer_format(dump_path: Path) -> str:
    """Guess dump format from path suffix or directory."""
    if dump_path.is_dir():
        return "directory"
    suffix = dump_path.suffix.lower()
    if suffix in {".sql", ".pgsql"}:
        return "plain"
    return "custom"


def _confirm_restore_target(url: str, *, allow_prod: bool) -> Dict[str, str]:
    """Refuse live Fly restore unless the operator confirmed twice."""
    conn = parse_database_url(url)
    if looks_like_live_prod(url):
        confirmed = os.getenv("RESTORE_CONFIRM_PROD") == PROD_CONFIRM
        if not allow_prod or not confirmed:
            raise BackupError(
                f"Refusing restore to live Fly Postgres ({conn['label']}).\n"
                "This tool never wipes production by default.\n"
                "Restore drill: start a local scratch Postgres and pass --target-url.\n"
                "  docker run --rm -e POSTGRES_PASSWORD=scratch -p 5433:5432 postgres:16\n"
                "  ./scripts/restore_postgres.sh --target-url "
                "postgresql://postgres:scratch@127.0.0.1:5433/postgres dump.dump\n"
                "To overwrite the live cluster you must pass --allow-prod AND "
                f"RESTORE_CONFIRM_PROD={PROD_CONFIRM}"
            )
    return conn


def restore_database(
    dump_path: Path,
    *,
    target_url: str,
    fmt: Optional[str] = None,
    allow_prod: bool = False,
    dry_run: bool = False,
    clean: bool = True,
) -> None:
    """Restore a dump into target_url (localhost scratch unless --allow-prod).

    Args:
        dump_path: pg_dump custom/plain/directory output.
        target_url: Destination Postgres URL.
        fmt: Override format inference.
        allow_prod: Required together with RESTORE_CONFIRM_PROD to hit Fly.
        dry_run: Print the restore command without applying it.
        clean: Drop existing objects first (pg_restore --clean --if-exists).
    """
    path = Path(dump_path)
    if not path.exists():
        raise BackupError(f"Dump not found: {path}")
    conn = _confirm_restore_target(target_url, allow_prod=allow_prod)
    resolved_fmt = fmt or infer_format(path)
    env = _pg_env(conn)
    print(f"Restoring {path} → {conn['label']} (format={resolved_fmt})")
    if resolved_fmt == "plain":
        psql = require_pg_tool("psql")
        cmd = [
            psql,
            "-h", conn["host"],
            "-p", conn["port"],
            "-U", conn["user"],
            "-d", conn["database"],
            "-v", "ON_ERROR_STOP=1",
            "-f", str(path),
        ]
    else:
        pg_restore = require_pg_tool("pg_restore")
        cmd = [
            pg_restore,
            "-h", conn["host"],
            "-p", conn["port"],
            "-U", conn["user"],
            "-d", conn["database"],
            "--no-owner",
            "--no-acl",
        ]
        if clean:
            cmd.extend(["--clean", "--if-exists"])
        cmd.append(str(path))
    _run(cmd, env=env, dry_run=dry_run)


def verify_restore(target_url: str, *, expected_papers: Optional[int] = None) -> int:
    """Count papers in the restored database and optionally compare."""
    conn = parse_database_url(target_url)
    count = count_papers(conn)
    if count is None:
        raise BackupError(
            f"Verify failed: could not COUNT papers on {conn['label']}."
        )
    print(f"verify paper_count={count} on {conn['label']}")
    if expected_papers is not None and count != expected_papers:
        raise BackupError(
            f"Verify mismatch: restored {count} papers, dump metadata had {expected_papers}."
        )
    return count


def expected_papers_from_sidecar(dump_path: Path) -> Optional[int]:
    """Read paper_count from a dump sidecar when present."""
    sidecar = sidecar_path(Path(dump_path))
    if not sidecar.is_file():
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    value = payload.get("paper_count")
    return int(value) if value is not None else None


def fly_snapshot_list_command() -> List[str]:
    """Return the Fly CLI command that lists managed Postgres snapshots."""
    return ["fly", "postgres", "backup", "list", "-a", FLY_POSTGRES_APP]


def build_parser() -> argparse.ArgumentParser:
    """Build the postgres backup/restore CLI."""
    parser = argparse.ArgumentParser(
        description="pg_dump / pg_restore helpers for cannabis-papers-db.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    dump = sub.add_parser("dump", help="Write a logical dump (custom/plain/directory).")
    dump.add_argument("--url", default=None, help="Postgres URL (default: DATABASE_URL).")
    dump.add_argument("--output", default=None, help="Dump path.")
    dump.add_argument(
        "--format",
        dest="fmt",
        choices=("custom", "plain", "directory"),
        default="custom",
        help="pg_dump format (default: custom, restore with pg_restore).",
    )
    dump.add_argument("--dry-run", action="store_true")

    restore = sub.add_parser("restore", help="Restore a dump into --target-url.")
    restore.add_argument("dump", help="Dump file or directory.")
    restore.add_argument(
        "--target-url",
        required=True,
        help="Destination Postgres URL (localhost scratch unless --allow-prod).",
    )
    restore.add_argument(
        "--format",
        dest="fmt",
        choices=("custom", "plain", "directory"),
        default=None,
    )
    restore.add_argument("--allow-prod", action="store_true")
    restore.add_argument("--dry-run", action="store_true")
    restore.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not pass --clean --if-exists to pg_restore.",
    )
    restore.add_argument(
        "--verify",
        action="store_true",
        help="After restore, COUNT papers and compare to dump sidecar.",
    )

    listing = sub.add_parser("list", help="Dry-run: print TOC / SQL header (does not restore).")
    listing.add_argument("dump", help="Dump file or directory.")
    listing.add_argument(
        "--format",
        dest="fmt",
        choices=("custom", "plain", "directory"),
        default=None,
    )

    verify = sub.add_parser("verify", help="COUNT papers on --target-url.")
    verify.add_argument("--target-url", required=True)
    verify.add_argument("--expected-papers", type=int, default=None)

    snapshots = sub.add_parser(
        "list-fly-snapshots",
        help="Print the fly postgres backup list command (managed snapshots).",
    )
    snapshots.add_argument(
        "--run",
        action="store_true",
        help="Execute `fly postgres backup list` (requires flyctl + auth).",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    """CLI entrypoint for dump, restore, list, and verify."""
    args = build_parser().parse_args(argv)
    try:
        if args.command == "dump":
            dump_database(
                url=args.url,
                output=Path(args.output) if args.output else None,
                fmt=args.fmt,
                dry_run=args.dry_run,
            )
        elif args.command == "list":
            print(list_dump(Path(args.dump), fmt=args.fmt))
        elif args.command == "restore":
            restore_database(
                Path(args.dump),
                target_url=args.target_url,
                fmt=args.fmt,
                allow_prod=args.allow_prod,
                dry_run=args.dry_run,
                clean=not args.no_clean,
            )
            if args.verify and not args.dry_run:
                verify_restore(
                    args.target_url,
                    expected_papers=expected_papers_from_sidecar(Path(args.dump)),
                )
        elif args.command == "verify":
            verify_restore(args.target_url, expected_papers=args.expected_papers)
        elif args.command == "list-fly-snapshots":
            cmd = fly_snapshot_list_command()
            print(" ".join(cmd))
            print(
                "# Managed Fly snapshots are complementary to pg_dump. "
                "They do not replace an in-repo logical dump."
            )
            if args.run:
                _run(cmd, env=os.environ.copy())
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
