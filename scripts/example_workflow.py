#!/usr/bin/env python
"""Walks through a realistic workflow against a RUNNING server and records every exchange.

    python scripts/example_workflow.py http://localhost:8000 [output.json]

Used to generate the real request/response examples in the README.
"""

import json
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
OUT = sys.argv[2] if len(sys.argv) > 2 else None
log: list[dict] = []


def call(client, title, method, path, *, token=None, body=None, expect=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.request(method, path, json=body, headers=headers)
    if expect is not None:
        assert r.status_code == expect, f"{title}: expected {expect}, got {r.status_code}: {r.text}"
    log.append({
        "title": title, "method": method, "path": path, "auth": bool(token), "body": body,
        "status": r.status_code, "response": r.json() if r.content else None,
    })
    return r.json() if r.content else None


with httpx.Client(base_url=BASE, timeout=20) as c:
    suffix = __import__("secrets").token_hex(3)
    priya_mail, rahul_mail = f"priya.{suffix}@example.com", f"rahul.{suffix}@example.com"
    call(c, "Register Priya", "POST", "/auth/register", body={"name": "Priya Sharma", "email": priya_mail, "password": "S3cure-passw0rd"}, expect=201)
    call(c, "Register Rahul", "POST", "/auth/register", body={"name": "Rahul Verma", "email": rahul_mail, "password": "An0ther-passw0rd"}, expect=201)
    pt = call(c, "Priya logs in", "POST", "/auth/login", body={"email": priya_mail, "password": "S3cure-passw0rd"}, expect=200)["access_token"]
    rt = call(c, "Rahul logs in", "POST", "/auth/login", body={"email": rahul_mail, "password": "An0ther-passw0rd"}, expect=200)["access_token"]
    project = call(c, "Priya creates a project (becomes OWNER)", "POST", "/projects", token=pt,
                   body={"name": "Website Redesign", "description": "Q1 marketing site refresh"}, expect=201)
    pid = project["id"]
    call(c, "Priya invites Rahul as ADMIN", "POST", f"/projects/{pid}/members", token=pt, body={"email": rahul_mail, "role": "ADMIN"}, expect=201)
    task = call(c, "Priya creates a task", "POST", f"/projects/{pid}/tasks", token=pt,
                body={"title": "Design landing page hero", "priority": "HIGH", "due_date": "2026-12-01"}, expect=201)
    tid = task["id"]
    for i, (t, p) in enumerate([("Write copy", "MEDIUM"), ("Set up analytics", "LOW"), ("Fix mobile nav", "HIGH")]):
        call(c, f"(setup) create task {i + 1}", "POST", f"/projects/{pid}/tasks", token=pt, body={"title": t, "priority": p}, expect=201)
    call(c, "Rahul reads the task and sees version 1", "GET", f"/tasks/{tid}", token=rt, expect=200)
    call(c, "Rahul starts work (TODO -> IN_PROGRESS)", "PATCH", f"/tasks/{tid}/status", token=rt, body={"status": "IN_PROGRESS", "version": 1}, expect=200)
    call(c, "Priya edits with her stale version 1 -> 409", "PATCH", f"/tasks/{tid}", token=pt, body={"version": 1, "title": "Hero section v2"}, expect=409)
    call(c, "Priya moves it back to TODO (allowed)", "PATCH", f"/tasks/{task['id']}/status", token=pt, body={"status": "TODO", "version": 2}, expect=200)
    call(c, "TODO -> DONE shortcut is blocked -> 400", "PATCH", f"/tasks/{tid}/status", token=pt, body={"status": "DONE", "version": 3}, expect=400)
    call(c, "Priya reloads, retries with version 3 -> 200", "PATCH", f"/tasks/{tid}", token=pt, body={"version": 3, "title": "Hero section v2"}, expect=200)
    call(c, "Filter, sort and paginate", "GET", f"/projects/{pid}/tasks?priority=HIGH&sort_by=created_at&order=asc&page=1&limit=10", token=pt, expect=200)
    call(c, "Rahul (ADMIN) cannot delete the project -> 403", "DELETE", f"/projects/{pid}", token=rt, expect=403)
    call(c, "Anonymous request -> 401", "GET", "/projects", expect=401)
    call(c, "Bad query parameter -> 422", "GET", f"/projects/{pid}/tasks?sort_by=password_hash", token=pt, expect=422)
    for path in ("/docs", "/redoc", "/openapi.json", "/health"):
        r = c.get(path)
        log.append({"title": f"GET {path}", "method": "GET", "path": path, "status": r.status_code, "response": None})
        assert r.status_code == 200, path

if OUT:
    json.dump(log, open(OUT, "w"), indent=2, default=str)
print(f"OK: {len(log)} requests, all returned the expected status codes")
