import pytest

from tests.helpers import create_task


@pytest.fixture
def twenty_five(client, team):
    for i in range(25):
        create_task(client, team.owner, team.project_id, title=f"Task {i:02d}")


def page(client, team, qs=""):
    return client.get(f"/projects/{team.project_id}/tasks?{qs}", headers=team.owner.headers)


def test_empty_project(client, team):
    body = page(client, team).json()
    assert body == {"items": [], "total": 0, "page": 1, "limit": 20, "pages": 0}


def test_defaults(client, team, twenty_five):
    body = page(client, team).json()
    assert (body["total"], body["page"], body["limit"], body["pages"], len(body["items"])) == (25, 1, 20, 2, 20)


@pytest.mark.parametrize("p, expected_len", [(1, 10), (2, 10), (3, 5)])
def test_page_sizes(client, team, twenty_five, p, expected_len):
    body = page(client, team, f"limit=10&page={p}").json()
    assert len(body["items"]) == expected_len and body["pages"] == 3 and body["page"] == p


def test_page_beyond_the_end_is_empty_not_an_error(client, team, twenty_five):
    r = page(client, team, "limit=10&page=4")
    assert r.status_code == 200
    assert r.json()["items"] == [] and r.json()["total"] == 25


def test_exact_multiple_has_no_phantom_page(client, team, twenty_five):
    assert page(client, team, "limit=5").json()["pages"] == 5


def test_limit_larger_than_total(client, team, twenty_five):
    body = page(client, team, "limit=100").json()
    assert len(body["items"]) == 25 and body["pages"] == 1


def test_limit_boundaries(client, team, twenty_five):
    assert page(client, team, "limit=1").json()["pages"] == 25
    assert page(client, team, "limit=100").status_code == 200
    assert page(client, team, "limit=101").status_code == 422
    assert page(client, team, "limit=0").status_code == 422
    assert page(client, team, "limit=-1").status_code == 422


@pytest.mark.parametrize("qs", ["page=0", "page=-3", "page=abc", "limit=abc", "page=1.5", "page=999999999999"])
def test_invalid_paging_values(client, team, twenty_five, qs):
    assert page(client, team, qs).status_code == 422


@pytest.mark.parametrize("sort", ["created_at", "updated_at", "due_date", "priority"])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_pages_are_a_stable_partition_for_every_sort(client, team, twenty_five, sort, order):
    """Walking all pages yields every task exactly once - even when the sort key has ties
    (all 25 tasks share the same priority and no due date)."""
    seen: list[str] = []
    for p in range(1, 6):
        seen += [i["id"] for i in page(client, team, f"limit=5&page={p}&sort_by={sort}&order={order}").json()["items"]]
    assert len(seen) == 25 and len(set(seen)) == 25


def test_total_reflects_filters_not_just_the_page(client, team, twenty_five):
    body = page(client, team, "search=Task 1&limit=3").json()  # Task 10..19
    assert body["total"] == 10 and len(body["items"]) == 3 and body["pages"] == 4


def test_pagination_does_not_leak_across_projects(client, team, twenty_five):
    from tests.helpers import create_project
    other = create_project(client, team.owner, "Other")
    create_task(client, team.owner, other["id"], title="elsewhere")
    assert page(client, team).json()["total"] == 25
