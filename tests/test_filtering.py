from datetime import date

import pytest

from tests.helpers import create_task


@pytest.fixture
def board(client, team):
    """Seven tasks with deliberately mixed attributes."""
    p, o, m = team.project_id, team.owner, team.member

    def mk(title, **kw):
        return create_task(client, o, p, title=title, **kw)

    t = {
        "login": mk("Fix login bug", priority="HIGH", due_date="2026-12-01", assigned_to=m.id),
        "docs": mk("Write docs", priority="LOW", due_date="2026-12-15"),
        "deploy": mk("Deploy to prod", priority="MEDIUM", due_date="2026-11-20", assigned_to=team.admin.id),
        "perf": mk("100% faster queries", priority="HIGH"),
        "under": mk("snake_case cleanup", priority="LOW", due_date="2027-01-10"),
        "login2": mk("LOGIN page redesign", priority="MEDIUM", due_date="2026-12-05", assigned_to=m.id),
        "done": mk("Archive old data", priority="LOW", due_date="2026-11-01"),
    }
    # move two tasks through the workflow
    client.patch(f"/tasks/{t['deploy']['id']}/status", json={"status": "IN_PROGRESS", "version": 1}, headers=o.headers)
    client.patch(f"/tasks/{t['done']['id']}/status", json={"status": "IN_PROGRESS", "version": 1}, headers=o.headers)
    client.patch(f"/tasks/{t['done']['id']}/status", json={"status": "DONE", "version": 2}, headers=o.headers)
    return t


def query(client, team, qs="", actor=None):
    r = client.get(f"/projects/{team.project_id}/tasks?{qs}", headers=(actor or team.owner).headers)
    return r


def titles(r):
    return [i["title"] for i in r.json()["items"]]


def test_no_filters_returns_everything(client, team, board):
    assert query(client, team).json()["total"] == 7


@pytest.mark.parametrize(
    "qs, expected",
    [
        ("status=TODO", {"Fix login bug", "Write docs", "100% faster queries", "snake_case cleanup", "LOGIN page redesign"}),
        ("status=IN_PROGRESS", {"Deploy to prod"}),
        ("status=DONE", {"Archive old data"}),
        ("priority=HIGH", {"Fix login bug", "100% faster queries"}),
        ("priority=LOW", {"Write docs", "snake_case cleanup", "Archive old data"}),
        ("status=TODO&priority=HIGH", {"Fix login bug", "100% faster queries"}),
        ("due_before=2026-11-20", {"Deploy to prod", "Archive old data"}),
        ("due_after=2026-12-15", {"Write docs", "snake_case cleanup"}),
        ("due_after=2026-12-01&due_before=2026-12-05", {"Fix login bug", "LOGIN page redesign"}),
        ("search=login", {"Fix login bug", "LOGIN page redesign"}),
        ("search=DEPLOY", {"Deploy to prod"}),
        ("search=zzz-nothing", set()),
    ],
)
def test_filters(client, team, board, qs, expected):
    r = query(client, team, qs)
    assert r.status_code == 200
    assert set(titles(r)) == expected
    assert r.json()["total"] == len(expected)


def test_filter_by_assignee(client, team, board):
    r = query(client, team, f"assigned_to={team.member.id}")
    assert set(titles(r)) == {"Fix login bug", "LOGIN page redesign"}


def test_tasks_without_due_date_never_match_date_filters(client, team, board):
    assert "100% faster queries" not in titles(query(client, team, "due_after=2000-01-01"))
    assert "100% faster queries" not in titles(query(client, team, "due_before=2100-01-01"))


def test_search_treats_percent_and_underscore_literally(client, team, board):
    assert titles(query(client, team, "search=100%25")) == ["100% faster queries"]
    assert titles(query(client, team, "search=snake_case")) == ["snake_case cleanup"]
    assert query(client, team, "search=%25").json()["total"] == 1  # '%' alone is NOT a wildcard
    assert query(client, team, "search=_").json()["total"] == 1  # only the title containing '_'


def test_search_is_not_sql_injectable(client, team, board):
    r = query(client, team, "search=x'%3B DROP TABLE tasks%3B --")
    assert r.status_code == 200 and r.json()["total"] == 0
    assert query(client, team).json()["total"] == 7


def test_filters_are_scoped_to_the_project(client, team, board, new_account):
    from tests.helpers import add_member, create_project
    other = create_project(client, team.owner, "Other project")
    create_task(client, team.owner, other["id"], title="Fix login bug elsewhere")
    assert query(client, team, "search=login").json()["total"] == 2


def test_sort_by_due_date_asc_puts_missing_dates_last(client, team, board):
    assert titles(query(client, team, "sort_by=due_date&order=asc")) == [
        "Archive old data", "Deploy to prod", "Fix login bug", "LOGIN page redesign",
        "Write docs", "snake_case cleanup", "100% faster queries",
    ]


def test_sort_by_due_date_desc_still_puts_missing_dates_last(client, team, board):
    result = titles(query(client, team, "sort_by=due_date&order=desc"))
    assert result[0] == "snake_case cleanup" and result[-1] == "100% faster queries"


def test_sort_by_priority_uses_semantic_order_not_alphabetical(client, team, board):
    r = query(client, team, "sort_by=priority&order=desc").json()["items"]
    order = [i["priority"] for i in r]
    assert order == ["HIGH", "HIGH", "MEDIUM", "MEDIUM", "LOW", "LOW", "LOW"]
    asc = [i["priority"] for i in query(client, team, "sort_by=priority&order=asc").json()["items"]]
    assert asc == sorted(asc, key=["LOW", "MEDIUM", "HIGH"].index)


def test_sort_by_created_at(client, team, board):
    asc = titles(query(client, team, "sort_by=created_at&order=asc"))
    assert asc[0] == "Fix login bug" and asc[-1] == "Archive old data"
    assert titles(query(client, team, "sort_by=created_at&order=desc")) == list(reversed(asc))


def test_sort_by_updated_at_reflects_recent_edits(client, team, board):
    # "Fix login bug" was created first; editing it makes it the most recently updated.
    client.patch(f"/tasks/{board['login']['id']}", json={"version": 1, "description": "touched"}, headers=team.owner.headers)
    assert titles(query(client, team, "sort_by=updated_at&order=desc"))[0] == "Fix login bug"


def test_default_sort_is_newest_first(client, team, board):
    assert titles(query(client, team))[0] == "Archive old data"


@pytest.mark.parametrize(
    "qs",
    [
        "status=BLOCKED", "priority=URGENT", "assigned_to=not-a-uuid", "due_before=yesterday",
        "sort_by=title", "sort_by=password_hash", "sort_by=due_date;DROP TABLE tasks", "sort_by=id",
        "order=sideways", "unknown_param=1", "search=", "search=" + "x" * 101,
    ],
)
def test_invalid_query_parameters_are_rejected(client, team, board, qs):
    r = query(client, team, qs)
    assert r.status_code == 422, qs
    assert r.json()["error"]["code"] == "validation_error"
    assert query(client, team).json()["total"] == 7  # nothing was harmed


def test_response_shape(client, team, board):
    body = query(client, team, "limit=3").json()
    assert set(body) == {"items", "total", "page", "limit", "pages"}
    assert set(body["items"][0]) == {
        "id", "project_id", "assigned_to", "created_by", "title", "description",
        "status", "priority", "due_date", "version", "created_at", "updated_at",
    }
