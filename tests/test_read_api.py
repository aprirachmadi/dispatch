import pytest


def test_company_detail_and_not_found(client):
    assert client.get("/companies/1").json()["sla_tier"] == "gold"
    assert client.get("/companies/999").status_code == 404
    assert client.get("/companies/0").status_code == 422


def test_literal_search_is_case_insensitive_and_filters_before_pagination(client):
    for subject in ("No match here", "Another PRINTER report", "Third printer report"):
        response = client.post(
            "/tickets",
            json={
                "company_id": 1,
                "subject": subject,
                "description": "Synthetic request",
                "category": "hardware",
                "priority": "high",
            },
        )
        assert response.status_code == 201
    matches = client.get(
        "/tickets",
        params={
            "query": "  printer  ",
            "priority": "high",
            "category": "hardware",
            "company_id": 1,
            "status": "new",
            "limit": 1,
            "offset": 1,
        },
    ).json()
    assert len(matches) == 1
    assert matches[0]["subject"] == "Third printer report"
    assert client.get("/tickets?query=password").json()[0]["id"] == 1
    assert client.get("/tickets?priority=urgent").json() == []


@pytest.mark.parametrize("query", ["%", "_", "' OR 1=1 --"])
def test_search_does_not_treat_input_as_sql_or_wildcards(client, query):
    assert client.get("/tickets", params={"query": query}).json() == []
    assert len(client.get("/tickets").json()) == 1
    client.post("/tickets", json={"company_id": 1, "subject": query, "description": "Literal"})
    found = client.get("/tickets", params={"query": query}).json()
    assert len(found) == 1
    assert found[0]["subject"] == query


@pytest.mark.parametrize("query", ["", "   ", "x" * 201])
def test_invalid_search_text(client, query):
    assert client.get("/tickets", params={"query": query}).status_code == 422


def test_invalid_priority_filter(client):
    assert client.get("/tickets?priority=critical").status_code == 422


@pytest.mark.parametrize("endpoint", ["/tickets", "/contacts"])
def test_huge_company_filter_rejected(client, endpoint):
    assert client.get(endpoint, params={"company_id": 2**63}).status_code == 422


@pytest.mark.parametrize("endpoint", ["/tickets", "/companies"])
def test_huge_path_id_rejected(client, endpoint):
    assert client.get(f"{endpoint}/{2**63}").status_code == 422


@pytest.mark.parametrize("field", ["company_id", "contact_id"])
def test_huge_post_reference_rejected_before_sqlite(client, field):
    payload = {"company_id": 1, "subject": "Synthetic", "description": "Test", field: 2**63}
    assert client.post("/tickets", json=payload).status_code == 422
