# Postgres backup, restore drill, and corpus guard

Postgres (`DATABASE_URL`, Fly app **cannabis-papers-db**) is the sole production source of truth. Local SQLite (`cannabis_papers.db`, `--sqlite-path`) and the Fly volume file (`/data/cannabis_papers.db`) are caches. Do not treat `/app/cannabis_papers.db` or an uninitialized local file as the corpus.

This page covers:

1. Logical backup (`pg_dump`)
2. Restore drill (dry-run / localhost scratch — never prod by default)
3. Verify
4. Golden / RL health check that fails closed on an empty or wrong SQLite

No Fly deploy or volume change is required to use these tools.

## Backup

The in-repo path is a standard logical dump. It uses the same `DATABASE_URL` + `fly proxy` convention as pull/push (`scripts/run_with_fly_proxy_venv.sh` → `cannabis-papers-db:5432`).

```bash
# DATABASE_URL already set (proxy up, or a scratch URL)
./scripts/backup_postgres.sh

# Start the existing Fly proxy, then dump
./scripts/backup_postgres.sh --via-fly-proxy

# Plain SQL instead of custom format
./scripts/backup_postgres.sh --format plain

# Print the pg_dump command only
./scripts/backup_postgres.sh --dry-run
```

Default output:

| Format | Path | Restore with |
| --- | --- | --- |
| `custom` (default, `-Fc`) | `scratch/postgres_backups/cannabis_papers_<utc>.dump` | `pg_restore` |
| `plain` (`-Fp`) | `scratch/postgres_backups/cannabis_papers_<utc>.sql` | `psql` |
| `directory` (`-Fd`) | `scratch/postgres_backups/cannabis_papers_<utc>` | `pg_restore` |

A sidecar `*.meta.json` records source host (redacted), format, bytes, and `paper_count` when `SELECT COUNT(*) FROM papers` succeeds. Dumps are gitignored under `scratch/postgres_backups/`.

Equivalent Python CLI:

```bash
python3 scripts/postgres_backup.py dump --format custom
python3 scripts/postgres_backup.py dump --url "$DATABASE_URL" --output /tmp/papers.dump
```

### Fly-managed snapshots (complementary)

If you use Fly's own Postgres backups, list them against the **existing** Postgres app name (do not invent a second dump tool):

```bash
./scripts/backup_postgres.sh --list-fly-snapshots
# same as:
fly postgres backup list -a cannabis-papers-db
```

Managed snapshots are not a substitute for an off-platform `pg_dump` you can restore onto localhost.

## Restore drill (safe)

Restore **never** defaults to production. `--target-url` is required for a write. Without it, only `--dry-run` (list TOC / SQL header) is allowed.

### 1. Dry-run — no writes

```bash
./scripts/restore_postgres.sh --dry-run scratch/postgres_backups/cannabis_papers_YYYYMMDD_HHMMSS.dump
```

Custom/directory dumps run `pg_restore --list`. Plain SQL dumps must look like a `pg_dump` file; the first lines are printed.

### 2. Restore into localhost scratch

```bash
docker run --rm --name cps-restore-scratch \
  -e POSTGRES_PASSWORD=scratch -p 5433:5432 postgres:16

./scripts/restore_postgres.sh \
  --target-url postgresql://postgres:scratch@127.0.0.1:5433/postgres \
  --verify \
  scratch/postgres_backups/cannabis_papers_YYYYMMDD_HHMMSS.dump
```

`--verify` runs `SELECT COUNT(*) FROM papers` on the target and compares it to the dump sidecar when present.

### 3. Live Fly restore (explicit, dual confirm)

This overwrites the production cluster. Do not do this unless you intend to.

```bash
RESTORE_CONFIRM_PROD=I_UNDERSTAND_THIS_OVERWRITES_PRODUCTION \
  ./scripts/restore_postgres.sh \
    --allow-prod \
    --target-url "$DATABASE_URL" \
    scratch/postgres_backups/cannabis_papers_YYYYMMDD_HHMMSS.dump
```

`--allow-prod` alone is rejected. Hostnames matching `cannabis-papers-db`, `flycast`, `.fly.dev`, or `.internal` are treated as live.

## Verify

```bash
# Dump TOC / SQL header
python3 scripts/postgres_backup.py list scratch/postgres_backups/foo.dump

# Paper count on a scratch URL
python3 scripts/postgres_backup.py verify \
  --target-url postgresql://postgres:scratch@127.0.0.1:5433/postgres
```

On the app VM, `python3 fly_db_check.py` still prints classifier-version counts. It now **exits non-zero** when the corpus guard fails (empty/wrong DB).

## Golden / RL corpus guard (fails closed)

Shared module: [`corpus_guard.py`](../../corpus_guard.py). Used by golden and RL entrypoints — not by the public web health probe.

| Profile | When | What must be true |
| --- | --- | --- |
| `golden` | Golden cycle / row automation | SQLite has `papers` and ≥ `GOLDEN_MIN_PAPERS` (default **1**), unless a pull is about to fill an empty cache. Pull/push also require `DATABASE_URL`. |
| `rl` | Calibration, Loop A, Fly preflight | If `DATABASE_URL` is set: Postgres `papers` count ≥ `RL_MIN_PAPERS` (default **100**). Else: populated SQLite. |

Refuses:

- Missing SQLite file, empty file, or missing `papers` table
- Zero (or below-minimum) paper rows
- `/app/cannabis_papers.db` when it is empty (image default, not the corpus)
- SQLite-only when the mode requires Postgres (`--require-postgres` / pull / Fly RL)

```bash
# Local golden cache after pull
GOLDEN_RUN=1 python3 corpus_guard.py --sqlite-path cannabis_papers.db

# About to pull (Postgres required; empty local SQLite is OK)
python3 corpus_guard.py --profile golden --require-postgres --allow-empty-sqlite

# Fly / Loop A
python3 corpus_guard.py --profile rl

# Emergency override only
CORPUS_GUARD=0 python3 corpus_guard.py --profile rl
```

How a failed check looks:

```
ERROR: Corpus guard failed (golden): SQLite at /app/cannabis_papers.db is the Fly
image default SQLite, not the corpus (papers=0, min=1).
Empty `/app/cannabis_papers.db` or an uninitialized local file is not the corpus.
Fix: pull from Postgres into a populated SQLite cache...
```

Wired into:

- `scripts/golden_endpoint_cycle.py` / `scripts/run_golden_endpoint_cycle.sh`
- `scripts/golden_endpoint_automate_row.py`
- `calibration_agent.py`
- `calibration_rl_orchestrator.py` preflight (now fails closed)
- `fly_db_check.py` (Fly SSH preflight)
- `scripts/run_loop_a_finish.sh`, `scripts/run_perpetual_loop_a_cycle.sh`
- `scripts/run_local_reingest_cycle.sh`

Override floors with `CORPUS_MIN_PAPERS`, `GOLDEN_MIN_PAPERS`, or `RL_MIN_PAPERS`.
