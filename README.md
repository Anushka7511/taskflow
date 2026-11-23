# TaskFlow – Multi-User Task Management API

A backend for collaborative task management (think a small Jira/Trello): users create projects, invite teammates with
roles, and manage tasks with a validated workflow, filtering, pagination and **optimistic concurrency control**.

Built with FastAPI, PostgreSQL 17, SQLAlchemy 2.0, Alembic and Pydantic v2. 250 automated tests run against a real PostgreSQL.

> **Verification status (read this first).** Verified in the build environment: the full pytest suite (250 tests, run repeatedly),
> the Alembic migration (upgrade / downgrade / upgrade, and a drift check), the container start command
> (`alembic upgrade head && uvicorn ...`) against a fresh database with a 24-request live workflow, and the benchmark.
> **Not verified: Docker itself** (`Dockerfile`, `docker-compose.yml`). Docker was not available where this was built, so those two
> files are written carefully but have never been executed. See [Known limitations](#known-limitations).

## Table of contents
1. [Key features](#key-features) · 2. [Technology stack](#technology-stack) · 3. [Architecture](#system-architecture) · 4. [Data model](#database-design)
5. [Folder structure](#folder-structure) · 6. [API reference](#api-endpoints) · 7. [Authentication](#authentication-workflow) · 8. [RBAC](#authorization-and-rbac)
9. [Transactions](#transaction-management) · 10. [Concurrency](#concurrency-handling) · 11. [SQL optimization](#sql-optimization) · 12. [Benchmarks](#benchmark-methodology-and-results)
13. [Testing](#testing) · 14. [Docker](#docker-setup) · 15. [Local setup](#local-development-setup) · 16. [Configuration](#environment-variables) · 17. [Examples](#example-api-workflow) · 18. [Limitations](#known-limitations) · 19. [Future work](#future-enhancements)

## Key features

- **Auth:** registration, login, Argon2id password hashing, JWT access tokens with expiry, algorithm pinned on decode.
- **Projects & membership:** many-to-many users ↔ projects with roles `OWNER`, `ADMIN`, `MEMBER`.
- **RBAC enforced in the service layer**, with privilege-escalation and last-owner protection (including under concurrency).
- **Tasks:** CRUD, assignment, priorities, validated status workflow.
- **Filter / search / sort / paginate in SQL**, with an explicit sort allow-list and bound parameters only.
- **Integrity in the database:** composite foreign key guaranteeing an assignee is a member of the task's *own* project, check constraints, explicit delete policies.
- **Optimistic locking** (`version` column) in two layers, verified with real multi-threaded tests (see [Concurrency](#concurrency-handling)).
- **Uniform error envelope**, no stack traces or internals leaked; unexpected errors are logged server-side.
- **Alembic migrations**, Docker Compose setup, benchmark script with real `EXPLAIN ANALYZE` output.

## Technology stack

| Concern | Choice | Notes |
|---|---|---|
| Language / web | Python 3.12 (3.11+ works), FastAPI | synchronous endpoints; FastAPI runs them in a thread pool |
| Database | PostgreSQL 17 | native enums, composite FK, `ON DELETE SET NULL (col)` (needs PG ≥ 15) |
| ORM / migrations | SQLAlchemy 2.0 (typed `Mapped[]`), Alembic | psycopg 3 driver |
| Validation | Pydantic v2 | request, response and query-string models |
| Security | `argon2-cffi`, `PyJWT` | no `passlib` (unmaintained) |
| Tests | pytest + FastAPI `TestClient` | real PostgreSQL, never SQLite |
| Packaging | Docker, Docker Compose | |
| **Redis** | **not used** | the spec allowed it only for a *justified* cache/rate-limit. Nothing here needed it; adding it would be decoration. Rate limiting is listed under future work. |

Sync (not async) database access is used consistently: it is simpler, SQLAlchemy's mature path, and adequate here. The pool size (`DB_POOL_SIZE`) bounds concurrency.

## System architecture

```mermaid
flowchart LR
    Client([Client]) -->|HTTP + Bearer JWT| R

    subgraph API["FastAPI application"]
        direction TB
        R["Routers<br/>HTTP in/out, status codes, OpenAPI"]
        D["Dependencies<br/>DB session, current user,<br/>project membership, RBAC gate"]
        S["Services<br/>business rules, authorization,<br/>transactions (unit_of_work)"]
        P["permissions.py<br/>pure RBAC policy"]
        Rp["Repositories<br/>SQLAlchemy 2.0 queries only"]
        M["Models<br/>tables, constraints, indexes"]
        R --> D --> S
        S --> P
        S --> Rp --> M
    end

    M -->|psycopg 3| DB[("PostgreSQL 17")]
    Sch["Schemas (Pydantic)<br/>validation + serialization"] -.-> R
    Core["core: config, security, exceptions"] -.-> S
```

| Layer | Responsibility | Must **not** |
|---|---|---|
| Routers | parse HTTP, call one service method, declare OpenAPI responses | contain business logic |
| Schemas | validate input, shape output (never exposes `password_hash`) | touch the DB |
| Services | rules, authorization, transaction boundaries, translate DB errors → domain errors | build HTTP responses |
| Repositories | queries | commit, or decide permissions |
| Models | tables, constraints, indexes | |
| Core | settings, JWT/Argon2, exceptions + handlers, RBAC policy | |

**Transaction boundary:** repositories only `flush()`; services wrap each use case in `unit_of_work(db)` which commits on success and rolls back on *any* exception.

## Database design

```mermaid
erDiagram
    USERS ||--o{ PROJECT_MEMBERSHIPS : "has"
    PROJECTS ||--o{ PROJECT_MEMBERSHIPS : "has"
    USERS ||--o{ PROJECTS : "created (RESTRICT)"
    PROJECTS ||--o{ TASKS : "contains (CASCADE)"
    PROJECT_MEMBERSHIPS |o--o{ TASKS : "assignee must be a member (composite FK)"
    USERS |o--o{ TASKS : "created_by (SET NULL)"

    USERS {
        uuid id PK
        varchar name
        varchar email UK "stored lower-case, CHECK email = lower(email)"
        varchar password_hash
        timestamptz created_at
        timestamptz updated_at
    }
    PROJECTS {
        uuid id PK
        varchar name
        text description
        uuid owner_id FK "creator; ON DELETE RESTRICT"
        timestamptz created_at
        timestamptz updated_at
    }
    PROJECT_MEMBERSHIPS {
        uuid id PK
        uuid project_id FK "ON DELETE CASCADE"
        uuid user_id FK "ON DELETE CASCADE"
        project_role role "OWNER | ADMIN | MEMBER"
        timestamptz joined_at
    }
    TASKS {
        uuid id PK
        uuid project_id FK "ON DELETE CASCADE"
        uuid assigned_to "nullable; part of composite FK"
        uuid created_by FK "nullable; ON DELETE SET NULL"
        varchar title
        text description
        task_status status "TODO | IN_PROGRESS | DONE"
        task_priority priority "LOW | MEDIUM | HIGH"
        date due_date
        int version "optimistic lock, starts at 1"
        timestamptz created_at
        timestamptz updated_at
    }
```

### Relationships and the decisions behind them

- **Users ↔ Projects is many-to-many** through `project_memberships`, which carries the `role`. `UNIQUE (project_id, user_id)` prevents duplicate memberships *and* is the target of the composite foreign key below *and* is the lookup index for the "is this user in this project?" check made on nearly every request.
- **Assignee must belong to the task's project: enforced by the database.** `tasks (project_id, assigned_to) → project_memberships (project_id, user_id)`. Being a member of project *B* does not allow assignment in project *A*. The check is skipped while `assigned_to IS NULL` (default `MATCH SIMPLE`), so unassigned tasks are valid. This closes the race where a member is removed between the service's check and the insert.
- **Delete policies are explicit:**
  | Parent → child | Policy | Why |
  |---|---|---|
  | project → memberships, tasks | `CASCADE` | deleting a project removes everything inside it |
  | user → memberships | `CASCADE` | a deleted user leaves all projects |
  | user → projects (`owner_id`) | `RESTRICT` | a project's creator cannot vanish from under it |
  | user → tasks (`created_by`) | `SET NULL` | keep the task, forget the author |
  | membership → tasks (assignee) | `SET NULL (assigned_to)` | removing a member **un-assigns** their tasks atomically; `project_id` is `NOT NULL` so only the one column is nulled (PostgreSQL 15+ syntax) |
- **`projects.owner_id` means "creator".** The *current* owners are the memberships with role `OWNER` (there can be several).
- **`tasks.created_by`** is an addition to the specified columns; it is what lets a plain `MEMBER` edit tasks they created.
- **Other constraints:** `email = lower(email)`, non-blank `name`/`title`, `version >= 1`, native enum types (invalid values are rejected by PostgreSQL itself). `priority` is declared `LOW, MEDIUM, HIGH` so `ORDER BY priority` sorts semantically, not alphabetically.
- **Not enforceable in the schema:** "a project always has at least one OWNER". PostgreSQL cannot express it with a plain constraint; it is enforced with a row lock plus an application check, and tested under concurrency.

## Folder structure

```
taskflow/
├── app/
│   ├── main.py                    app factory, router wiring, /health
│   ├── core/
│   │   ├── config.py              settings from environment (no hard-coded secrets)
│   │   ├── security.py            Argon2id hashing, JWT create/decode
│   │   ├── dependencies.py        session, current user, membership + RBAC gates, service providers
│   │   ├── exceptions.py          domain errors + centralised handlers (uniform JSON)
│   │   └── permissions.py         pure RBAC policy (the single source of truth for roles)
│   ├── database/                  base.py (naming convention), session.py (engine, unit_of_work), errors.py
│   ├── models/                    user, project, membership, task, enums
│   ├── schemas/                   user, project, membership, task, common (Page, ErrorResponse)
│   ├── repositories/              user, project, membership, task
│   ├── services/                  auth, project, membership, task, access
│   └── routers/                   auth, projects, memberships, tasks, responses
├── alembic/versions/0001_initial_schema.py
├── tests/                         11 modules + conftest/helpers (250 tests)
├── scripts/                       benchmark.py, example_workflow.py
├── docs/benchmark_results.md      generated by scripts/benchmark.py (full plans)
├── Dockerfile · docker-compose.yml · .env.example · requirements*.txt · alembic.ini · pytest.ini
```

Additions beyond the suggested tree, each for a reason: `core/permissions.py` (testable RBAC policy), `database/errors.py` (maps PostgreSQL constraint names to domain errors), `services/access.py` (shared membership lookup), `routers/responses.py` (OpenAPI error docs), `requirements-dev.txt` (keeps test tooling out of the runtime image).

## API endpoints

Interactive docs: Swagger UI `http://localhost:8000/docs` · ReDoc `http://localhost:8000/redoc` · OpenAPI JSON `http://localhost:8000/openapi.json`.
Every endpoint documents its auth, purpose and error codes there. All errors share this shape:

```json
{"error": {"code": "conflict", "message": "Task was modified by someone else. Reload it and retry.", "details": {"submitted_version": 1, "current_version": 2}}}
```

| Method | URL | Purpose | Auth | Success | Errors |
|---|---|---|---|---|---|
| POST | `/auth/register` | create account | none | 201 | 409 duplicate e-mail, 422 |
| POST | `/auth/login` | get JWT | none | 200 | 401, 422 |
| GET | `/auth/me` | current user | Bearer | 200 | 401 |
| POST | `/projects` | create project (caller becomes OWNER) | Bearer | 201 | 401, 422 |
| GET | `/projects` | my projects (paginated) | Bearer | 200 | 401, 422 |
| GET | `/projects/{id}` | project details | member | 200 | 401, 404 |
| PATCH | `/projects/{id}` | update name/description | OWNER, ADMIN | 200 | 401, 403, 404, 422 |
| DELETE | `/projects/{id}` | delete project + members + tasks | OWNER | 204 | 401, 403, 404 |
| POST | `/projects/{id}/members` | invite registered user by e-mail | OWNER (any role), ADMIN (MEMBER only) | 201 | 403, 404, 409, 422 |
| GET | `/projects/{id}/members` | list members | member | 200 | 401, 404 |
| PATCH | `/projects/{id}/members/{user_id}` | change role | OWNER | 200 | 403, 404, 409 last owner |
| DELETE | `/projects/{id}/members/{user_id}` | remove member / leave | see RBAC | 204 | 403, 404, 409 last owner |
| POST | `/projects/{id}/tasks` | create task | member | 201 | 400 bad assignee, 404, 422 |
| GET | `/projects/{id}/tasks` | list: filter / search / sort / paginate | member | 200 | 404, 422 |
| GET | `/tasks/{id}` | task details (includes `version`) | member | 200 | 404 |
| PATCH | `/tasks/{id}` | edit title/description/priority/due date | see RBAC; needs `version` | 200 | 403, 404, **409**, 422 |
| PATCH | `/tasks/{id}/assign` | assign / unassign | see RBAC; needs `version` | 200 | 400, 403, 404, **409** |
| PATCH | `/tasks/{id}/status` | change status | see RBAC; needs `version` | 200 | 400 transition, 403, 404, **409** |
| DELETE | `/tasks/{id}` | delete task | see RBAC | 204 | 403, 404 |
| GET | `/health` | liveness + DB check | none | 200 / 503 | |

**Listing tasks:** `GET /projects/{id}/tasks?status=TODO&priority=HIGH&assigned_to=<uuid>&due_before=2026-12-31&due_after=2026-01-01&search=login&sort_by=due_date&order=asc&page=1&limit=10`

- `sort_by` ∈ `created_at | updated_at | due_date | priority` (explicit allow-list; anything else is 422). `order` ∈ `asc | desc`. Tasks without a due date always sort last.
- `page ≥ 1`, `1 ≤ limit ≤ 100` (default 20). A page past the end returns an empty `items` list (not an error).
- Unknown query parameters are rejected (422) so a typo cannot silently drop a filter.
- Response: `{items, total, page, limit, pages}`. Ordering has an `id` tie-breaker so pages never overlap or skip rows.
- `search` is a case-insensitive title match; `%` and `_` are escaped, so searching for `100%` finds the literal text.

**Status workflow:** `TODO → IN_PROGRESS`, `IN_PROGRESS → TODO`, `IN_PROGRESS → DONE`, `DONE → IN_PROGRESS`. Anything else (e.g. `TODO → DONE`) is `400` with the allowed targets in `details`.

**Why 404 instead of 403 for non-members:** a user who is not in a project gets `404` for it and everything inside it, so the API does not confirm that a project or task exists. A member lacking the *role* gets `403`.

## Authentication workflow

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant A as FastAPI
    participant DB as PostgreSQL
    C->>A: POST /auth/register {name, email, password}
    A->>DB: INSERT users (Argon2id hash; unique e-mail)
    A-->>C: 201 {id, name, email, created_at}
    C->>A: POST /auth/login {email, password}
    A->>DB: SELECT user by e-mail
    A->>A: verify Argon2 hash (dummy hash if user unknown)
    A-->>C: 200 {access_token, token_type, expires_in}
    C->>A: GET /projects  (Authorization: Bearer token)
    A->>A: verify signature, exp, pinned algorithm
    A->>DB: SELECT user by sub
    A-->>C: 200 ... (401 if token or user is invalid)
```

Security measures, each covered by a test: Argon2id hashes (never plaintext, never returned); `SECRET_KEY` required, ≥ 32 chars, taken from the environment; tokens carry `exp` (required) and are decoded with the algorithm pinned (tampered, expired, wrong-secret, `alg=none`, missing-`exp` and non-UUID-subject tokens are all rejected with 401); login returns the **same** error for unknown e-mail and wrong password and does one Argon2 verification either way (timing); e-mails are normalised to lower-case; validation errors never echo the submitted password; the production profile refuses the development placeholder secret.

## Authorization and RBAC

| Capability | OWNER | ADMIN | MEMBER |
|---|:-:|:-:|:-:|
| View project, members, tasks | ✅ | ✅ | ✅ |
| Create tasks | ✅ | ✅ | ✅ |
| Update project name/description | ✅ | ✅ | ❌ |
| Delete project | ✅ | ❌ | ❌ |
| Invite members | any role | **MEMBER only** | ❌ |
| Change a member's role | ✅ | ❌ | ❌ |
| Remove members | anyone | **MEMBER only** | ❌ (may remove themselves) |
| Edit / assign / change status / delete tasks | any task | any task | only tasks they **created or are assigned to** |

Invariants: an admin cannot remove an owner (or another admin); nobody can escalate their own role; **the last owner cannot be demoted, removed or leave**; roles are per project; a demoted or removed user loses access on their very next request (permissions are read from the database each time, not from the token).

**Where it is enforced:** the policy lives in `core/permissions.py` as pure functions. `Services` are the authoritative layer and always check. Routers add an early gate for coarse rules (`require_project_role(...)` dependency) and resolve membership (`get_project_membership`, 404 for non-members). Member and task-by-id operations are checked entirely in services, because they must re-check inside a transaction.

## Transaction management

- `unit_of_work(db)` (`database/session.py`) commits when the block succeeds and rolls back on **any** exception, then re-raises.
- Creating a project inserts the project **and** its OWNER membership in one transaction. A test forces the second insert to fail and asserts no orphan project remains.
- PostgreSQL integrity errors are translated by **constraint name** (`violated_constraint`) into domain errors: duplicate e-mail → 409, duplicate membership → 409, bad assignee → 400. Two requests can both pass an application "does it exist?" pre-check, so the constraint is the real guarantee; the pre-check only provides a nicer message.
- Isolation level: PostgreSQL's default `READ COMMITTED`. Correctness under concurrency comes from optimistic versioning and one row lock, not from raising the isolation level.
- Uncommitted work is invisible to other sessions (tested), and `get_db` always closes the session so no transaction leaks out of a request.

## Concurrency handling

Problem: Rahul and Priya both open a task at version 1. Rahul saves. Priya saves. Without protection Priya silently overwrites Rahul (a *lost update*).

**Layer 1: the version the client read must still be current.** Every task mutation requires `version` in the body. If it differs from the stored version the service answers `409 Conflict` immediately, with `current_version` in `details`. This catches the common case where the client's copy is simply stale.

**Layer 2: a conditional UPDATE closes the race window.** Two requests can both read version 1 *and* both pass layer 1 before either writes. The `Task` mapper uses SQLAlchemy's `version_id_col`, so every UPDATE/DELETE is `... WHERE id = :id AND version = :loaded_version` and bumps the counter. The loser's statement matches zero rows after the winner commits, SQLAlchemy raises `StaleDataError`, the service rolls back and returns `409`.

```mermaid
sequenceDiagram
    autonumber
    participant R as Rahul
    participant P as Priya
    participant API as TaskFlow
    participant DB as PostgreSQL
    R->>API: GET /tasks/T
    API-->>R: version 1
    P->>API: GET /tasks/T
    API-->>P: version 1
    R->>API: PATCH /tasks/T {version: 1, title: "Rahul"}
    API->>DB: UPDATE tasks SET ..., version = 2 WHERE id = T AND version = 1
    DB-->>API: 1 row
    API-->>R: 200 (version 2)
    P->>API: PATCH /tasks/T {version: 1, title: "Priya"}
    API->>API: layer 1: stored version is 2, not 1
    API-->>P: 409 Conflict {current_version: 2}
    Note over P,API: Even if Priya's request had already passed layer 1,<br/>layer 2's UPDATE ... WHERE version = 1 would match 0 rows → StaleDataError → 409
    P->>API: GET /tasks/T, then PATCH {version: 2}
    API-->>P: 200 (version 3)
```

**Membership changes use a pessimistic lock instead**, because "is this the last owner?" is a check across several rows that a version counter on one row cannot protect. `MembershipService` takes `SELECT ... FOR UPDATE` on the project row, so changes to one project's membership are serialised, and it re-reads the actor's own role *after* acquiring the lock (an actor demoted a millisecond earlier is honoured).

**How it is tested** (`tests/test_concurrency.py`, separate sessions and real threads):

- the Rahul/Priya scenario step by step over HTTP;
- 2 and 6 threads, held at a barrier *after* their SELECT so all read version 1, then released to write: exactly **one** succeeds, the rest get `ConflictError`, final version is exactly 2 and the data is the winner's, not a blend;
- a status change racing an edit; five workers doing read-modify-write with retry: **no edit is lost** and the version ends at exactly 6;
- a stale in-memory ORM object cannot overwrite a newer row (`StaleDataError`), nor delete it;
- two owners demoting / removing each other at the same instant never leave a project with zero owners; concurrent duplicate invites and registrations create exactly one row.

Mutation checks were run on this suite: removing only layer 1 fails the Rahul/Priya test; removing only layer 2 (`version_id_col`) fails 7 tests, including the pure race tests; removing the project lock fails the two-owners race test. Both layers are independently needed and independently tested.

**Not covered:** projects and memberships are last-write-wins (no version column); `DELETE /tasks/{id}` carries no version (an `If-Match` header would be the natural addition).

### Task creation flow

```mermaid
flowchart TD
    A["POST /projects/{id}/tasks"] --> B{"Valid Bearer token?"}
    B -- no --> E1["401"]
    B -- yes --> C{"Caller is a project member?"}
    C -- no --> E2["404 (existence not leaked)"]
    C -- yes --> D{"Body valid? (Pydantic)"}
    D -- no --> E3["422"]
    D -- yes --> F{"assigned_to set?"}
    F -- yes --> G{"Assignee is a project member?"}
    G -- no --> E4["400"]
    F -- no --> H["BEGIN: INSERT task (version 1, created_by = caller)"]
    G -- yes --> H
    H --> I{"DB constraints satisfied?<br/>(composite FK, checks)"}
    I -- no --> J["ROLLBACK, 400"]
    I -- yes --> K["COMMIT, 201 TaskRead"]
```

## SQL optimization

Indexes were chosen from the actual query shapes the repository generates, not added blindly:

| Index | Serves |
|---|---|
| `uq_users_email` (unique) | login lookup; duplicate protection |
| `uq_project_memberships_project_user` (unique) | per-request authorization check; composite-FK target |
| `ix_project_memberships_user_id` | "projects of user X" (`GET /projects`) |
| `ix_tasks_project_id_created_at` | default list order (newest first) within a project |
| `ix_tasks_project_id_due_date` | sort by due date / due-date window within a project |
| `ix_tasks_project_id_status` | per-status counts (index-only scan) |
| `ix_tasks_assigned_to` | "tasks assigned to X"; also the lookup behind the `ON DELETE SET NULL` action |
| `ix_projects_owner_id` | the `RESTRICT` check when deleting a user |

Not indexed on purpose: sorting by `priority` / `updated_at` (the `project_id` prefix already narrows to one project's rows), and title `ILIKE '%x%'` search (needs `pg_trgm`; listed under future work).

### Benchmark methodology and results

`scripts/benchmark.py` creates a throw-away database from the real migration, seeds it with set-based SQL, then for each query: drops the four secondary task indexes → `ANALYZE` → 3 warm-ups + N timed runs of `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` → creates the indexes → `ANALYZE` → repeats. It reports the **median** of PostgreSQL's own `Execution Time`, the plan shape, the index sizes, and the write-side cost. The full plans are in [`docs/benchmark_results.md`](docs/benchmark_results.md).

**Environment of the recorded run** (2026-11-14T19:38:15):

- PostgreSQL: 17.12 (running locally, same host as the script, no network)
- Host: Linux-6.18.44-fc-v470-x86_64-with-glibc2.39; CPU: Intel(R) Xeon(R) Processor @ 2.10GHz; logical CPUs: 1; RAM: 3992 MiB
- Settings (defaults, not tuned): shared_buffers=128MB, work_mem=4MB, effective_cache_size=4GB, random_page_cost=4, max_parallel_workers_per_gather=2
- Python 3.12.3; method: 15 timed runs per query after 3 warm-ups (warm cache); reported value = **median** of PostgreSQL's own `Execution Time` from `EXPLAIN (ANALYZE)`
- `EXPLAIN ANALYZE` adds instrumentation overhead, so absolute times are slightly higher than an un-instrumented query; the before/after comparison uses identical instrumentation.

**Dataset:**

| table | rows |
|---|---|
| users | 2,000 |
| projects | 200 |
| project_memberships | 4,000 |
| tasks | 400,000 |

- tasks table size (heap + TOAST): 60 MB; target project holds 2,000 tasks, 1,020 with status TODO
- seeding took 8.9s. Tasks are interleaved across projects, ~70% assigned, ~20% without due date.

**Results** (median of 15 runs, before vs after creating the indexes):

| Query | Endpoint / purpose | Without indexes | With indexes | Speed-up |
|---|---|---:|---:|---:|
| Q1 status filter + newest first | GET /projects/{id}/tasks?status=TODO (default sort, page 1) | 32.218 ms | 0.037 ms | 870.8x |
| Q2 sort by due date | GET /projects/{id}/tasks?sort_by=due_date&order=asc | 26.386 ms | 0.030 ms | 879.5x |
| Q3 newest first, deep page | GET /projects/{id}/tasks?page=3 (default sort) | 26.824 ms | 0.044 ms | 609.6x |
| Q4 count per status | total for a filtered list / dashboard counter | 32.177 ms | 0.078 ms | 412.5x |
| Q5 due-date window | GET /projects/{id}/tasks?due_after=...&due_before=... | 40.760 ms | 0.031 ms | 1314.8x |
| Q6 assigned to user (all projects) | 'my tasks' lookup; also the FK ON DELETE SET NULL action when a member is removed | 28.122 ms | 0.164 ms | 171.5x |
| C1 control: membership lookup | authorization check on every request (unique index, present in both phases) | 0.007 ms | 0.006 ms | 1.2x |
| C2 control: user by email | login (unique index, present in both phases) | 0.008 ms | 0.006 ms | 1.3x |

**Cost of the indexes:**

| Index | Size | Build time on 400k rows |
|---|---|---|
| ix_tasks_project_id_status | 4368 kB | 287 ms |
| ix_tasks_assigned_to | 4088 kB | 201 ms |
| ix_tasks_project_id_created_at | 17 MB | 345 ms |
| ix_tasks_project_id_due_date | 6008 kB | 284 ms |

Bulk `INSERT` of 20,000 task rows (median of 3, rolled back): **305 ms without** the four indexes vs **471 ms with** them (1.54x).

**How to read this honestly:**

- Numbers are from a **single-vCPU sandbox**, a **warm cache**, **synthetic data** and a **local** socket (no network). Expect different absolute values on your hardware; the *relationship* (a project-scoped query touching ~2,000 of 400,000 rows goes from a full scan to an index range scan) is what transfers.
- The "without indexes" column pays a parallel sequential scan of the whole 400k-row table, so the speed-ups are large *because the table is large relative to one project*. With a table of a few thousand rows the difference would be negligible.
- C1/C2 are controls (unique indexes in both phases): they stayed at ≈1.0x, which shows the measurement noise floor.
- `ix_tasks_project_id_status` is **not** what serves the filtered list (Q1): the planner chose the `created_at` index and filtered status on the fly. It is used for the count query (Q4, index-only scan). Whether it earns its write cost depends on whether such counts matter to you; I kept it because it is in the specification and Q4 benefits.
- **The first benchmark run was flawed and I fixed it:** my synthetic data had `status` correlated with project, so the benchmarked project had zero TODO rows, and the "my tasks" query stopped early because of clustered data. The numbers above are from the corrected script (status/assignee/due date are hash-based pseudo-random).
- Re-run it yourself: `python scripts/benchmark.py` (`--tasks`, `--projects`, `--users`, `--members`, `--runs`).

## Testing

```bash
# needs a PostgreSQL you can create databases on; the test DB is dropped and rebuilt from the migrations
export TEST_DATABASE_URL=postgresql+psycopg://taskflow:change-me@localhost:5433/taskflow_test
pip install -r requirements-dev.txt
pytest -q
```

Safety: the suite refuses to run unless the database name ends in `_test`. The database is dropped and recreated **from the Alembic migration** once per session (so the migration itself is tested), and tables are truncated after every test.

| Module | Tests | Covers |
|---|---:|---|
| `test_auth.py` | 30 | registration, duplicate e-mail (case-insensitive, concurrent), login, invalid credentials, JWT tampering/expiry/alg=none |
| `test_authorization.py` | 30 | full RBAC matrix (unit), 401 on every endpoint, outsiders, member-vs-admin task rules, instant demotion |
| `test_concurrency.py` | 16 | Rahul/Priya, thread races, lost-update retry, owner races, commit/rollback/atomicity, 500 handling |
| `test_config.py` | 4 | required secret, production refuses placeholder secret |
| `test_database_constraints.py` | 16 | composite FK, cascades, SET NULL, RESTRICT, checks, enums, expected indexes |
| `test_filtering.py` | 37 | every filter, search escaping + injection, sort order incl. NULLs/priority, invalid params rejected |
| `test_memberships.py` | 26 | invites, duplicates, escalation blocked, role changes, last-owner rules, removal unassigns tasks |
| `test_pagination.py` | 25 | boundaries, stable partition across pages for every sort, totals, invalid values |
| `test_projects.py` | 19 | creation + OWNER membership, listing, 404-for-outsiders, role-gated update/delete, cascade delete |
| `test_tasks.py` | 37 | create/update/delete, validation, assignment rules, status workflow, version conflicts |
| `test_users.py` | 10 | Argon2 hashing, schema normalisation, no sensitive fields, user DB constraints |

Result when this was built: **250 passed in about 20 seconds on one vCPU**, identical across three consecutive runs (concurrency tests included). A `StarletteDeprecationWarning` about `httpx` appears; it comes from the test client library, not from this code.

**Are the tests meaningful?** They were mutation-checked: nine deliberate bugs were injected one at a time (version check removed, `version_id_col` removed, last-owner guard removed, project lock removed, admin may grant any role, membership check dropped, `TODO→DONE` allowed, LIKE wildcards unescaped, password hash returned) and **every one was caught** by a failing test. Several tests were also initially wrong (a vacuous assertion, a barrier that outlived its test) and were fixed rather than loosened.

## Docker setup

> Reminder: these files have **not been executed** in the build environment (no Docker available). The start command they run was executed separately and works.

```bash
docker compose up --build          # API + PostgreSQL; migrations run automatically on start
```

| What | URL |
|---|---|
| API | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |

```bash
docker compose down                # stop (database volume is kept)
docker compose down -v             # stop AND delete the database volume
docker compose --profile test run --rm tests    # run the test-suite in a container
```

- `db`: `postgres:17`, named volume `pgdata`, `pg_isready` health check, published on `127.0.0.1` only.
- `api`: waits for `db` to be **healthy** (`depends_on: condition: service_healthy`), then `alembic upgrade head && exec uvicorn ...`; runs as a non-root user; has its own health check against `/health`.
- Both services share the private `backend` network; the API reaches the database at host `db`.
- **Secrets:** nothing is committed. Compose reads `.env` (see `.env.example`). So that `docker compose up --build` works with *no* setup, compose falls back to a clearly named **development-only** secret and DB password; the app **refuses to start** with that secret when `ENVIRONMENT=production`. For anything real: `cp .env.example .env` and set `SECRET_KEY` (`python -c "import secrets; print(secrets.token_hex(32))"`) and `POSTGRES_PASSWORD`.

## Local development setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env               # then set DATABASE_URL (uncomment) and SECRET_KEY
createdb taskflow                  # PostgreSQL >= 15 required (17 recommended)
alembic upgrade head
uvicorn app.main:app --reload      # http://localhost:8000/docs
```

Create a new migration after changing models: `alembic revision --autogenerate -m "message"`, **review it** (autogenerate does not drop PostgreSQL enum types in `downgrade`; the initial migration does that by hand), then `alembic check` should report no drift.

## Environment variables

| Variable | Required | Default | Purpose |
|---|:-:|---|---|
| `DATABASE_URL` | yes | – | `postgresql+psycopg://user:pw@host:5432/db` |
| `SECRET_KEY` | yes | – | JWT signing key, ≥ 32 characters |
| `ENVIRONMENT` | no | `development` | `production` rejects the placeholder secret |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | no | `60` | JWT lifetime |
| `JWT_ALGORITHM` | no | `HS256` | pinned on decode |
| `LOG_LEVEL` | no | `INFO` | |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | no | `10` / `10` | SQLAlchemy pool |
| `ARGON2_TIME_COST` / `ARGON2_MEMORY_COST_KIB` / `ARGON2_PARALLELISM` | no | `3` / `65536` / `4` | hashing cost (lowered only in tests) |
| `TEST_DATABASE_URL` | tests | `postgresql+psycopg://taskflow@127.0.0.1:5432/taskflow_test` | name must end in `_test` |
| `POSTGRES_USER/PASSWORD/DB`, `DB_PORT`, `API_PORT` | compose | `taskflow` / `taskflow` / `taskflow` / `5432` / `8000` | |

## Example API workflow

These requests and responses were **recorded from a live run** of `scripts/example_workflow.py` against the real server (access tokens shortened, `$TOKEN` is the value from login). The script asserts every status code.

### 1. Register Priya

```bash
curl -X POST 'http://localhost:8000/auth/register' \
  -H 'Content-Type: application/json' \
  -d '{"name": "Priya Sharma", "email": "priya.a2d212@example.com", "password": "S3cure-passw0rd"}'
```

**→ HTTP 201**

```json
{
  "id": "921e133c-c5cc-4108-8902-79df71bbc618",
  "name": "Priya Sharma",
  "email": "priya.a2d212@example.com",
  "created_at": "2026-11-14T19:39:56.428173Z"
}
```

### 2. Priya logs in

```bash
curl -X POST 'http://localhost:8000/auth/login' \
  -H 'Content-Type: application/json' \
  -d '{"email": "priya.a2d212@example.com", "password": "S3cure-passw0rd"}'
```

**→ HTTP 200**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5…",
  "token_type": "bearer",
  "expires_in": 3600
}
```

### 3. Priya creates a project (becomes OWNER)

```bash
curl -X POST 'http://localhost:8000/projects' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "Website Redesign", "description": "Q1 marketing site refresh"}'
```

**→ HTTP 201**

```json
{
  "id": "f29aff12-0900-4a35-83ed-7cf6ae2209f5",
  "name": "Website Redesign",
  "description": "Q1 marketing site refresh",
  "owner_id": "921e133c-c5cc-4108-8902-79df71bbc618",
  "created_at": "2026-11-14T19:39:57.130587Z",
  "updated_at": "2026-11-14T19:39:57.130587Z"
}
```

### 4. Priya invites Rahul as ADMIN

```bash
curl -X POST 'http://localhost:8000/projects/f29aff12-0900-4a35-83ed-7cf6ae2209f5/members' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"email": "rahul.a2d212@example.com", "role": "ADMIN"}'
```

**→ HTTP 201**

```json
{
  "id": "2294db57-1b2b-4bda-a207-fb30eac2b5ed",
  "project_id": "f29aff12-0900-4a35-83ed-7cf6ae2209f5",
  "user_id": "0a954442-ab9d-461a-9b38-d95bfe85ea03",
  "role": "ADMIN",
  "joined_at": "2026-11-14T19:39:57.139672Z",
  "user": {
    "id": "0a954442-ab9d-461a-9b38-d95bfe85ea03",
    "name": "Rahul Verma",
    "email": "rahul.a2d212@example.com"
  }
}
```

### 5. Priya creates a task

```bash
curl -X POST 'http://localhost:8000/projects/f29aff12-0900-4a35-83ed-7cf6ae2209f5/tasks' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title": "Design landing page hero", "priority": "HIGH", "due_date": "2026-12-01"}'
```

**→ HTTP 201**

```json
{
  "id": "3f6bd96b-1fff-4763-b356-ffa2428438a2",
  "project_id": "f29aff12-0900-4a35-83ed-7cf6ae2209f5",
  "assigned_to": null,
  "created_by": "921e133c-c5cc-4108-8902-79df71bbc618",
  "title": "Design landing page hero",
  "description": null,
  "status": "TODO",
  "priority": "HIGH",
  "due_date": "2026-12-01",
  "version": 1,
  "created_at": "2026-11-14T19:39:57.152099Z",
  "updated_at": "2026-11-14T19:39:57.152099Z"
}
```

### 6. Rahul reads the task and sees version 1

```bash
curl -X GET 'http://localhost:8000/tasks/3f6bd96b-1fff-4763-b356-ffa2428438a2' \
  -H "Authorization: Bearer $TOKEN"
```

**→ HTTP 200**

```json
{
  "id": "3f6bd96b-1fff-4763-b356-ffa2428438a2",
  "project_id": "f29aff12-0900-4a35-83ed-7cf6ae2209f5",
  "assigned_to": null,
  "created_by": "921e133c-c5cc-4108-8902-79df71bbc618",
  "title": "Design landing page hero",
  "description": null,
  "status": "TODO",
  "priority": "HIGH",
  "due_date": "2026-12-01",
  "version": 1,
  "created_at": "2026-11-14T19:39:57.152099Z",
  "updated_at": "2026-11-14T19:39:57.152099Z"
}
```

### 7. Rahul starts work (TODO -> IN_PROGRESS)

```bash
curl -X PATCH 'http://localhost:8000/tasks/3f6bd96b-1fff-4763-b356-ffa2428438a2/status' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"status": "IN_PROGRESS", "version": 1}'
```

**→ HTTP 200**

```json
{
  "id": "3f6bd96b-1fff-4763-b356-ffa2428438a2",
  "project_id": "f29aff12-0900-4a35-83ed-7cf6ae2209f5",
  "assigned_to": null,
  "created_by": "921e133c-c5cc-4108-8902-79df71bbc618",
  "title": "Design landing page hero",
  "description": null,
  "status": "IN_PROGRESS",
  "priority": "HIGH",
  "due_date": "2026-12-01",
  "version": 2,
  "created_at": "2026-11-14T19:39:57.152099Z",
  "updated_at": "2026-11-14T19:39:57.179717Z"
}
```

### 8. Priya edits with her stale version 1 -> 409

```bash
curl -X PATCH 'http://localhost:8000/tasks/3f6bd96b-1fff-4763-b356-ffa2428438a2' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"version": 1, "title": "Hero section v2"}'
```

**→ HTTP 409**

```json
{
  "error": {
    "code": "conflict",
    "message": "Task was modified by someone else. Reload it and retry.",
    "details": {
      "submitted_version": 1,
      "current_version": 2
    }
  }
}
```

### 9. TODO -> DONE shortcut is blocked -> 400

```bash
curl -X PATCH 'http://localhost:8000/tasks/3f6bd96b-1fff-4763-b356-ffa2428438a2/status' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"status": "DONE", "version": 3}'
```

**→ HTTP 400**

```json
{
  "error": {
    "code": "bad_request",
    "message": "Invalid status transition TODO -> DONE",
    "details": {
      "allowed": [
        "IN_PROGRESS"
      ]
    }
  }
}
```

### 10. Filter, sort and paginate

```bash
curl -X GET 'http://localhost:8000/projects/f29aff12-0900-4a35-83ed-7cf6ae2209f5/tasks?priority=HIGH&sort_by=created_at&order=asc&page=1&limit=10' \
  -H "Authorization: Bearer $TOKEN"
```

**→ HTTP 200**

```json
{
  "items": [
    {
      "id": "3f6bd96b-1fff-4763-b356-ffa2428438a2",
      "project_id": "f29aff12-0900-4a35-83ed-7cf6ae2209f5",
      "assigned_to": null,
      "created_by": "921e133c-c5cc-4108-8902-79df71bbc618",
      "title": "Hero section v2",
      "description": null,
      "status": "TODO",
      "priority": "HIGH",
      "due_date": "2026-12-01",
      "version": 4,
      "created_at": "2026-11-14T19:39:57.152099Z",
      "updated_at": "2026-11-14T19:39:57.199562Z"
    },
    {
      "id": "0edeae75-dd3c-4420-8020-343b80f456a5",
      "project_id": "f29aff12-0900-4a35-83ed-7cf6ae2209f5",
      "assigned_to": null,
      "created_by": "921e133c-c5cc-4108-8902-79df71bbc618",
      "title": "Fix mobile nav",
      "description": null,
      "status": "TODO",
      "priority": "HIGH",
      "due_date": null,
      "version": 1,
      "created_at": "2026-11-14T19:39:57.169427Z",
      "updated_at": "2026-11-14T19:39:57.169427Z"
    }
  ],
  "total": 2,
  "page": 1,
  "limit": 10,
  "pages": 1
}
```

### 11. Rahul (ADMIN) cannot delete the project -> 403

```bash
curl -X DELETE 'http://localhost:8000/projects/f29aff12-0900-4a35-83ed-7cf6ae2209f5' \
  -H "Authorization: Bearer $TOKEN"
```

**→ HTTP 403**

```json
{
  "error": {
    "code": "forbidden",
    "message": "Requires one of these roles: OWNER",
    "details": null
  }
}
```

### 12. Anonymous request -> 401

```bash
curl -X GET 'http://localhost:8000/projects'
```

**→ HTTP 401**

```json
{
  "error": {
    "code": "unauthorized",
    "message": "Authentication required",
    "details": null
  }
}
```

### 13. Bad query parameter -> 422

```bash
curl -X GET 'http://localhost:8000/projects/f29aff12-0900-4a35-83ed-7cf6ae2209f5/tasks?sort_by=password_hash' \
  -H "Authorization: Bearer $TOKEN"
```

**→ HTTP 422**

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": [
      {
        "field": "query.sort_by",
        "message": "Input should be 'created_at', 'updated_at', 'due_date' or 'priority'",
        "type": "literal_error"
      }
    ]
  }
}
```


## Known limitations

Stated plainly rather than hidden:

- **Docker artefacts untested.** `Dockerfile` and `docker-compose.yml` were never run (no Docker in the build environment). The YAML parses and the container's start command was verified separately; a first real `docker compose up --build` could still reveal a typo. Local PostgreSQL used for all verification was 17.12 (conda-forge build), the same major version as the `postgres:17` image.
- **No Redis / rate limiting.** Login and registration are not throttled; Argon2 cost is the only brake on brute force.
- **No token revocation or refresh tokens.** A leaked token works until it expires. No password reset, e-mail verification, or user-deletion endpoint.
- **User enumeration is possible** through registration (`409`) and invitations (`404 no registered user`). Login itself does not leak it.
- Members can see the e-mail addresses of other members of the same project.
- **Only tasks are version-checked.** Project edits are last-write-wins; `DELETE /tasks/{id}` has no version precondition.
- The "at least one owner" rule is enforced by a row lock + application check, not by a database constraint.
- Title search is an unindexed `ILIKE '%…%'` (scoped to one project's rows).
- Benchmarks are from one machine with synthetic, warm-cache data (see above). `OFFSET` pagination gets slower for very deep pages; keyset pagination would fix that.
- **Mermaid diagrams were not render-tested** (no renderer in the build environment); they follow standard syntax and GitHub renders Mermaid natively, but a typo is possible.
- Sync SQLAlchemy: concurrency is bounded by the thread pool and the connection pool.

## Future enhancements

Rate limiting (Redis) and login throttling · refresh tokens + revocation · `If-Match`/ETag on tasks and projects · keyset (cursor) pagination · `pg_trgm` index for title search · audit log / activity feed · comments and attachments · e-mail invitations for unregistered users · soft delete · CI pipeline running the suite against `postgres:17` · structured JSON logging with request IDs.
