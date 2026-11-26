from types import SimpleNamespace

import pytest

from app.schemas.visa import (
    GeneratedDocument,
    GeneratedRoadmapStep,
    GeneratedVisaPlan,
    VisaPlanCreateRequest,
)


def get_auth_token(client, email="visa@example.com", password="testpass123"):
    client.post("/auth/register", json={"email": email, "password": password})
    response = client.post("/auth/login", json={"email": email, "password": password})
    return response.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def sample_plan(**overrides) -> GeneratedVisaPlan:
    data = {
        "overview": "A tourist visa is straightforward for this applicant.",
        "roadmap": [
            GeneratedRoadmapStep(
                step=1, title="Check eligibility", detail="Read the rules."
            ),
            GeneratedRoadmapStep(
                step=2, title="Gather documents", detail="Collect paperwork."
            ),
        ],
        "documents": [
            GeneratedDocument(
                category="Identity",
                title="Passport",
                description="Valid for at least six months.",
                is_required=True,
            ),
            GeneratedDocument(
                category="Financial",
                title="Bank statement",
                description="Last three months of activity.",
                is_required=False,
            ),
        ],
        "interview_tips": ["Arrive early", "Bring originals"],
    }
    data.update(overrides)
    return GeneratedVisaPlan(**data)


VALID_PAYLOAD = {
    "country": "Germany",
    "nationality": "Bangladeshi",
    "purpose": "tourism",
    "travel_date": "2026-06-01",
    "duration_of_stay": "14 days",
    "background": "Previously traveled to Malaysia.",
}


@pytest.fixture(autouse=True)
def mock_visa_generator(monkeypatch):
    monkeypatch.setattr(
        "app.routers.visa.generate_visa_plan", lambda request: sample_plan()
    )


def test_create_plan_returns_overview_roadmap_and_checklist(client):
    token = get_auth_token(client)
    response = client.post("/visa/plans", json=VALID_PAYLOAD, headers=auth(token))
    assert response.status_code == 201
    data = response.json()
    assert data["country"] == "Germany"
    assert data["nationality"] == "Bangladeshi"
    assert data["purpose"] == "tourism"
    assert data["overview"].startswith("A tourist visa")
    assert [s["step"] for s in data["roadmap"]] == [1, 2]
    assert len(data["documents"]) == 2
    assert data["documents"][0]["title"] == "Passport"
    assert data["documents"][0]["is_required"] is True
    assert data["documents"][0]["collected"] is False
    assert data["interview_tips"] == ["Arrive early", "Bring originals"]


def test_create_plan_requires_login(client):
    response = client.post("/visa/plans", json=VALID_PAYLOAD)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"country": "G"},
        {"nationality": ""},
        {"purpose": "smuggling"},
        {"background": "x" * 2001},
        {"travel_date": "x" * 41},
    ],
)
def test_create_plan_validation(client, overrides):
    token = get_auth_token(client)
    payload = {**VALID_PAYLOAD, **overrides}
    response = client.post("/visa/plans", json=payload, headers=auth(token))
    assert response.status_code == 422


def test_ai_failure_returns_502_and_saves_nothing(client, monkeypatch):
    def boom(request):
        raise RuntimeError("gemini down")

    monkeypatch.setattr("app.routers.visa.generate_visa_plan", boom)
    token = get_auth_token(client)
    response = client.post("/visa/plans", json=VALID_PAYLOAD, headers=auth(token))
    assert response.status_code == 502
    assert client.get("/visa/plans", headers=auth(token)).json() == []


def test_list_plans_newest_first_with_document_counts(client):
    token = get_auth_token(client)
    client.post("/visa/plans", json=VALID_PAYLOAD, headers=auth(token))
    client.post(
        "/visa/plans",
        json={**VALID_PAYLOAD, "country": "France"},
        headers=auth(token),
    )
    response = client.get("/visa/plans", headers=auth(token))
    assert response.status_code == 200
    plans = response.json()
    assert [p["country"] for p in plans] == ["France", "Germany"]
    for plan in plans:
        assert plan["documents_total"] == 2
        assert plan["documents_collected"] == 0


def test_get_plan_detail(client):
    token = get_auth_token(client)
    created = client.post("/visa/plans", json=VALID_PAYLOAD, headers=auth(token)).json()
    response = client.get(f"/visa/plans/{created['id']}", headers=auth(token))
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == created["id"]
    assert data["background"] == VALID_PAYLOAD["background"]
    assert len(data["documents"]) == 2


def test_other_users_plan_is_404(client):
    token_a = get_auth_token(client, "owner@example.com")
    created = client.post(
        "/visa/plans", json=VALID_PAYLOAD, headers=auth(token_a)
    ).json()
    token_b = get_auth_token(client, "other@example.com")
    response = client.get(f"/visa/plans/{created['id']}", headers=auth(token_b))
    assert response.status_code == 404


def test_toggle_document_status(client):
    token = get_auth_token(client)
    plan = client.post("/visa/plans", json=VALID_PAYLOAD, headers=auth(token)).json()
    document = plan["documents"][0]

    response = client.patch(
        f"/visa/plans/{plan['id']}/documents/{document['id']}",
        json={"collected": True},
        headers=auth(token),
    )
    assert response.status_code == 200
    assert response.json()["collected"] is True

    detail = client.get(f"/visa/plans/{plan['id']}", headers=auth(token)).json()
    assert detail["documents"][0]["collected"] is True

    listed = client.get("/visa/plans", headers=auth(token)).json()
    assert listed[0]["documents_collected"] == 1


def test_toggle_document_wrong_plan_is_404(client):
    token_a = get_auth_token(client, "owner@example.com")
    plan_a = client.post(
        "/visa/plans", json=VALID_PAYLOAD, headers=auth(token_a)
    ).json()
    token_b = get_auth_token(client, "intruder@example.com")
    plan_b = client.post(
        "/visa/plans", json=VALID_PAYLOAD, headers=auth(token_b)
    ).json()

    # a document of plan A does not exist under plan B
    response = client.patch(
        f"/visa/plans/{plan_b['id']}/documents/{plan_a['documents'][0]['id']}",
        json={"collected": True},
        headers=auth(token_b),
    )
    assert response.status_code == 404


def test_toggle_document_requires_login(client):
    token = get_auth_token(client)
    plan = client.post("/visa/plans", json=VALID_PAYLOAD, headers=auth(token)).json()
    response = client.patch(
        f"/visa/plans/{plan['id']}/documents/{plan['documents'][0]['id']}",
        json={"collected": True},
    )
    assert response.status_code == 401


def test_generate_visa_plan_parses_structured_output(monkeypatch):
    from app.core import gemini_client

    parsed = sample_plan()
    fake_models = SimpleNamespace(
        generate_content=lambda **kwargs: SimpleNamespace(parsed=parsed)
    )
    monkeypatch.setattr(gemini_client, "client", SimpleNamespace(models=fake_models))

    request = VisaPlanCreateRequest(**VALID_PAYLOAD)
    result = gemini_client.generate_visa_plan(request)
    assert len(result.documents) == 2
    assert len(result.roadmap) == 2


def test_generate_visa_plan_rejects_empty_output(monkeypatch):
    from app.core import gemini_client

    fake_models = SimpleNamespace(
        generate_content=lambda **kwargs: SimpleNamespace(parsed=None)
    )
    monkeypatch.setattr(gemini_client, "client", SimpleNamespace(models=fake_models))

    request = VisaPlanCreateRequest(**VALID_PAYLOAD)
    with pytest.raises(ValueError):
        gemini_client.generate_visa_plan(request)
