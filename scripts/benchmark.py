#!/usr/bin/env python
"""Index benchmark for TaskFlow (PostgreSQL).

What it does
  1. Creates a throw-away database (default: <your db>_bench) and builds the schema with
     the real Alembic migration.
  2. Seeds a synthetic dataset with set-based SQL (generate_series).
  3. Phase A - drops the four secondary task indexes, ANALYZEs, runs every query.
  4. Phase B - creates the indexes, ANALYZEs, runs every query again.
  5. Each query is executed with EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) after warm-up and the
     MEDIAN execution time over --runs repetitions is reported, together with the plan shape.
  6. Measures the write-side cost of the indexes (bulk INSERT, rolled back).
  7. Writes docs/benchmark_results.md. No number in that file is typed by hand.

Usage
  python scripts/benchmark.py [--tasks 400000] [--projects 200] [--users 2000]
                              [--members 20] [--runs 15]
Env
  BENCHMARK_DATABASE_URL  full SQLAlchemy URL of the benchmark DB (db name must end in _bench)
  DATABASE_URL            fallback: same server/credentials, database name -> <name>_bench
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import statistics
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent
INDEXES = {
    "ix_tasks_project_id_status": "CREATE INDEX ix_tasks_project_id_status ON tasks (project_id, status)",
    "ix_tasks_assigned_to": "CREATE INDEX ix_tasks_assigned_to ON tasks (assigned_to)",
    "ix_tasks_project_id_created_at": "CREATE INDEX ix_tasks_project_id_created_at ON tasks (project_id, created_at)",
    "ix_tasks_project_id_due_date": "CREATE INDEX ix_tasks_project_id_due_date ON tasks (project_id, due_date)",
}


def bench_url() -> str:
    explicit = os.environ.get("BENCHMARK_DATABASE_URL")
    if explicit:
        return explicit
    base = os.environ.get("DATABASE_URL")
    if not base:
        sys.exit("Set BENCHMARK_DATABASE_URL or DATABASE_URL")
    url = make_url(base)
    return url.set(database=f"{url.database}_bench").render_as_string(hide_password=False)


def queries(project_id: str, user_id: str, email: str) -> list[tuple[str, str, str]]:
    """(name, description, SQL). These mirror the statements TaskRepository generates."""
    p, u = f"'{project_id}'", f"'{user_id}'"
    return [
        ("Q1 status filter + newest first",
         "GET /projects/{id}/tasks?status=TODO (default sort, page 1)",
         f"SELECT * FROM tasks WHERE project_id = {p} AND status = 'TODO' ORDER BY created_at DESC, id LIMIT 20"),
        ("Q2 sort by due date",
         "GET /projects/{id}/tasks?sort_by=due_date&order=asc",
         f"SELECT * FROM tasks WHERE project_id = {p} ORDER BY due_date ASC NULLS LAST, id LIMIT 20"),
        ("Q3 newest first, deep page",
         "GET /projects/{id}/tasks?page=3 (default sort)",
         f"SELECT * FROM tasks WHERE project_id = {p} ORDER BY created_at DESC, id LIMIT 20 OFFSET 40"),
        ("Q4 count per status",
         "total for a filtered list / dashboard counter",
         f"SELECT count(*) FROM tasks WHERE project_id = {p} AND status = 'IN_PROGRESS'"),
        ("Q5 due-date window",
         "GET /projects/{id}/tasks?due_after=...&due_before=...",
         f"SELECT * FROM tasks WHERE project_id = {p} AND due_date BETWEEN DATE '2026-03-01' AND DATE '2026-03-31' ORDER BY due_date, id LIMIT 20"),
        ("Q6 assigned to user (all projects)",
         "'my tasks' lookup; also the FK ON DELETE SET NULL action when a member is removed",
         f"SELECT * FROM tasks WHERE assigned_to = {u} ORDER BY created_at DESC LIMIT 50"),
        ("C1 control: membership lookup",
         "authorization check on every request (unique index, present in both phases)",
         f"SELECT * FROM project_memberships WHERE project_id = {p} AND user_id = {u}"),
        ("C2 control: user by email",
         "login (unique index, present in both phases)",
         f"SELECT * FROM users WHERE email = '{email}'"),
    ]


def node_summary(plan: dict) -> str:
    """Compact 'Limit > Index Scan(ix_name)' style summary of a JSON plan."""
    parts: list[str] = []

    def walk(node: dict) -> None:
        label = node["Node Type"]
        if "Index Name" in node:
            label += f"({node['Index Name']})"
        elif "Relation Name" in node:
            label += f"({node['Relation Name']})"
        parts.append(label)
        for child in node.get("Plans", []):
            walk(child)

    walk(plan)
    return " > ".join(parts)


def explain_json(conn, sql: str) -> dict:
    row = conn.execute(text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")).scalar_one()
    return row[0]


def explain_text(conn, sql: str) -> str:
    return "\n".join(r[0] for r in conn.execute(text(f"EXPLAIN (ANALYZE, BUFFERS) {sql}")))


def run_phase(conn, qs, runs: int) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, _desc, sql in qs:
        for _ in range(3):  # warm-up: populate caches, exclude from stats
            explain_json(conn, sql)
        exec_times, plan_times = [], []
        first = None
        for _ in range(runs):
            result = explain_json(conn, sql)
            first = first or result
            exec_times.append(result["Execution Time"])
            plan_times.append(result["Planning Time"])
        out[name] = {
            "median_ms": statistics.median(exec_times),
            "min_ms": min(exec_times),
            "max_ms": max(exec_times),
            "planning_ms": statistics.median(plan_times),
            "shape": node_summary(first["Plan"]),
            "buffers": first["Plan"].get("Shared Hit Blocks", 0) + first["Plan"].get("Shared Read Blocks", 0),
            "text": explain_text(conn, sql),
        }
    return out


def seed(conn, users: int, projects: int, members: int, tasks: int) -> None:
    conn.execute(text("""
        INSERT INTO users (name, email, password_hash)
        SELECT 'User ' || g, 'user' || g || '@bench.test', 'not-a-real-hash' FROM generate_series(1, :n) g"""), {"n": users})
    conn.execute(text("CREATE TEMP TABLE bu AS SELECT id, (row_number() OVER (ORDER BY email) - 1)::int AS idx FROM users"))
    conn.execute(text("CREATE TEMP TABLE bp AS SELECT g AS pidx, gen_random_uuid() AS id FROM generate_series(0, :p - 1) g"), {"p": projects})
    conn.execute(text("""
        INSERT INTO projects (id, name, owner_id)
        SELECT bp.id, 'Project ' || bp.pidx, bu.id FROM bp JOIN bu ON bu.idx = (bp.pidx * 7) % :n"""), {"n": users})
    conn.execute(text("""
        INSERT INTO project_memberships (project_id, user_id, role)
        SELECT bp.id, bu.id,
               (CASE WHEN k = 0 THEN 'OWNER' WHEN k = 1 THEN 'ADMIN' ELSE 'MEMBER' END)::project_role
        FROM bp CROSS JOIN generate_series(0, :m - 1) k
        JOIN bu ON bu.idx = (bp.pidx * 7 + k) % :n"""), {"m": members, "n": users})
    # Tasks are interleaved across projects (like real traffic), so a project's rows are scattered
    # over the heap. Attributes come from hashtext(), i.e. pseudo-random and NOT correlated with the
    # project or with each other (an earlier version used g % 10 and accidentally gave one project
    # zero TODO tasks). ~70% assigned (always to a member of that project), ~20% without due date.
    conn.execute(text("""
        CREATE TEMP TABLE rnd AS
        SELECT g,
               abs(hashtext(g::text || 's')::bigint) % 10 AS s,
               abs(hashtext(g::text || 'p')::bigint) % 3  AS p,
               abs(hashtext(g::text || 'a')::bigint) % 10 AS a,
               abs(hashtext(g::text || 'm')::bigint) % :m AS slot,
               abs(hashtext(g::text || 'd')::bigint) % 365 AS d,
               abs(hashtext(g::text || 'n')::bigint) % 5  AS nodue
        FROM generate_series(1, :t) g"""), {"t": tasks, "m": members})
    conn.execute(text("""
        INSERT INTO tasks (project_id, assigned_to, title, status, priority, due_date, created_at, updated_at)
        SELECT bp.id,
               CASE WHEN r.a < 7 THEN bu.id END,
               'Task ' || r.g || ' ' || substr(md5(r.g::text), 1, 8),
               (CASE WHEN r.s < 5 THEN 'TODO' WHEN r.s < 8 THEN 'IN_PROGRESS' ELSE 'DONE' END)::task_status,
               (CASE r.p WHEN 0 THEN 'LOW' WHEN 1 THEN 'MEDIUM' ELSE 'HIGH' END)::task_priority,
               CASE WHEN r.nodue = 0 THEN NULL ELSE DATE '2026-01-01' + r.d::int END,
               timestamptz '2025-01-01' + r.g * interval '1 minute',
               timestamptz '2025-01-01' + r.g * interval '1 minute'
        FROM rnd r
        JOIN bp ON bp.pidx = r.g % :p
        LEFT JOIN bu ON bu.idx = (bp.pidx * 7 + r.slot) % :n
        ORDER BY r.g"""),
        {"p": projects, "m": members, "n": users})


def insert_cost(url, rows: int) -> float:
    """Time (ms) to INSERT `rows` tasks in a normal transaction, then ROLL BACK so the
    dataset is unchanged. Uses its own (non-autocommit) connection."""
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            start = c.execute(text("SELECT clock_timestamp()")).scalar_one()
            c.execute(text("""
                INSERT INTO tasks (project_id, assigned_to, title, status, priority, due_date, created_at, updated_at)
                SELECT project_id, assigned_to, 'bench copy ' || title, status, priority, due_date, now(), now()
                FROM tasks LIMIT :r"""), {"r": rows})
            end = c.execute(text("SELECT clock_timestamp()")).scalar_one()
            c.rollback()
        return (end - start).total_seconds() * 1000
    finally:
        eng.dispose()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=int, default=400_000)
    ap.add_argument("--projects", type=int, default=200)
    ap.add_argument("--users", type=int, default=2_000)
    ap.add_argument("--members", type=int, default=20)
    ap.add_argument("--runs", type=int, default=15)
    ap.add_argument("--out", default=str(ROOT / "docs" / "benchmark_results.md"))
    args = ap.parse_args()

    url = make_url(bench_url())
    if not (url.database or "").endswith("_bench"):
        sys.exit("Refusing to run: benchmark database name must end with '_bench'")

    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()

    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False),
           "SECRET_KEY": os.environ.get("SECRET_KEY", "benchmark-only-secret-key-0123456789abcdef")}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        t0 = dt.datetime.now()
        print(f"Seeding {args.tasks:,} tasks / {args.projects} projects / {args.users} users ...")
        seed(conn, args.users, args.projects, args.members, args.tasks)
        seed_s = (dt.datetime.now() - t0).total_seconds()
        print(f"  seeded in {seed_s:.1f}s")

        # A project/user/email that exist and are representative.
        project_id = conn.execute(text("SELECT id FROM bp WHERE pidx = 5")).scalar_one()
        user_id = conn.execute(text(
            "SELECT user_id FROM project_memberships WHERE project_id = :p AND role = 'MEMBER' LIMIT 1"), {"p": project_id}).scalar_one()
        email = conn.execute(text("SELECT email FROM users WHERE id = :u"), {"u": user_id}).scalar_one()
        qs = queries(str(project_id), str(user_id), email)

        counts = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
                  for t in ("users", "projects", "project_memberships", "tasks")}
        per_project = conn.execute(text("SELECT count(*) FROM tasks WHERE project_id = :p"), {"p": project_id}).scalar_one()
        match_status = conn.execute(text("SELECT count(*) FROM tasks WHERE project_id = :p AND status = 'TODO'"), {"p": project_id}).scalar_one()

        # ---------------- Phase A: no secondary task indexes
        for name in INDEXES:
            conn.execute(text(f"DROP INDEX {name}"))
        conn.execute(text("VACUUM ANALYZE"))
        print("Phase A (no task indexes) ...")
        before = run_phase(conn, qs, args.runs)
        insert_before = statistics.median(insert_cost(url, 20_000) for _ in range(3))

        # ---------------- Phase B: indexes created
        build_ms = {}
        for name, ddl in INDEXES.items():
            s = dt.datetime.now()
            conn.execute(text(ddl))
            build_ms[name] = (dt.datetime.now() - s).total_seconds() * 1000
        conn.execute(text("VACUUM ANALYZE"))
        print("Phase B (with indexes) ...")
        after = run_phase(conn, qs, args.runs)
        insert_after = statistics.median(insert_cost(url, 20_000) for _ in range(3))

        sizes = {r[0]: r[1] for r in conn.execute(text(
            "SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) FROM pg_stat_user_indexes WHERE relname = 'tasks'"))}
        table_size = conn.execute(text("SELECT pg_size_pretty(pg_table_size('tasks'))")).scalar_one()
        settings = {k: conn.execute(text(f"SHOW {k}")).scalar_one() for k in
                    ("server_version", "shared_buffers", "work_mem", "effective_cache_size", "random_page_cost",
                     "max_parallel_workers_per_gather")}

    mem_kb = None
    try:
        mem_kb = int(next(l for l in open("/proc/meminfo") if l.startswith("MemTotal")).split()[1])
    except Exception:  # noqa: BLE001
        pass
    cpu = "unknown"
    try:
        cpu = next(l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name"))
    except Exception:  # noqa: BLE001
        pass

    lines: list[str] = []
    w = lines.append
    w("# Benchmark results (generated by `scripts/benchmark.py`)\n")
    w(f"Run at: {dt.datetime.now().isoformat(timespec='seconds')}\n")
    w("## Environment\n")
    w(f"- PostgreSQL: {settings['server_version']} (running locally, same host as the script, no network)")
    w(f"- Host: {platform.platform()}; CPU: {cpu}; logical CPUs: {os.cpu_count()}; RAM: {mem_kb // 1024 if mem_kb else '?'} MiB")
    w(f"- Settings (defaults, not tuned): shared_buffers={settings['shared_buffers']}, work_mem={settings['work_mem']}, "
      f"effective_cache_size={settings['effective_cache_size']}, random_page_cost={settings['random_page_cost']}, "
      f"max_parallel_workers_per_gather={settings['max_parallel_workers_per_gather']}")
    w(f"- Python {platform.python_version()}; method: {args.runs} timed runs per query after 3 warm-ups (warm cache); "
      "reported value = **median** of PostgreSQL's own `Execution Time` from `EXPLAIN (ANALYZE)`")
    w("- `EXPLAIN ANALYZE` adds instrumentation overhead, so absolute times are slightly higher than an un-instrumented query; "
      "the before/after comparison uses identical instrumentation.\n")
    w("## Dataset (synthetic)\n")
    w("| table | rows |\n|---|---|")
    for t, n in counts.items():
        w(f"| {t} | {n:,} |")
    w(f"\n- tasks table size (heap + TOAST): {table_size}; target project holds {per_project:,} tasks, {match_status:,} with status TODO")
    w(f"- seeding took {seed_s:.1f}s. Tasks are interleaved across projects, ~70% assigned, ~20% without due date.\n")
    w("## Results: median execution time, before vs after indexing\n")
    w("| Query | Endpoint / purpose | Without indexes | With indexes | Speed-up | Plan without | Plan with |")
    w("|---|---|---:|---:|---:|---|---|")
    for name, desc, _ in qs:
        b, a = before[name], after[name]
        ratio = b["median_ms"] / a["median_ms"] if a["median_ms"] else float("inf")
        w(f"| {name} | {desc} | {b['median_ms']:.3f} ms | {a['median_ms']:.3f} ms | {ratio:.1f}x | `{b['shape']}` | `{a['shape']}` |")
    w("\nControl queries (C1, C2) are served by unique indexes in both phases and should not change; "
      "differences there are measurement noise and give a feel for the noise floor.\n")
    w("## Cost of the indexes\n")
    w("| Index | Size | Build time on 400k rows |\n|---|---|---|")
    for name in INDEXES:
        w(f"| {name} | {sizes.get(name, '?')} | {build_ms[name]:.0f} ms |")
    w(f"\nBulk `INSERT` of 20,000 task rows (median of 3, rolled back): **{insert_before:.0f} ms without** the four indexes vs "
      f"**{insert_after:.0f} ms with** them ({insert_after / insert_before:.2f}x).\n")
    w("## Execution plans (EXPLAIN (ANALYZE, BUFFERS))\n")
    for name, desc, sql in qs:
        w(f"### {name}\n\n`{sql}`\n")
        w("**Without indexes**\n\n```\n" + before[name]["text"] + "\n```\n")
        w("**With indexes**\n\n```\n" + after[name]["text"] + "\n```\n")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines))
    print(f"\nWrote {args.out}\n")
    for name, _, _ in qs:
        b, a = before[name], after[name]
        print(f"{name:40s} {b['median_ms']:9.3f} ms -> {a['median_ms']:9.3f} ms  ({b['median_ms']/a['median_ms']:.1f}x)")


if __name__ == "__main__":
    main()
