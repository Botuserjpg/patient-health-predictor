import pytest
import app as app_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    application = app_module.create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.db")})
    monkeypatch.setattr(
        app_module,
        "get_prediction",
        lambda values, app: (
            "Possible risk. Risk level: Low. Consult a doctor. Informational only; not medical advice."
        ),
    )
    return application.test_client()


CASES = [
    ("Robyn Pears", "28.01.1960", "robyn@gmail.com", 232, 13.5, 180, True),
    ("John Hunter", "10.12.1990", "hunter@gmail", 94, 15, 200, False),
    ("Darryl Tim", "12.11.1993", "timd@smh.com.au", 240, 18.6, 326, True),
    ("Peter Larry", "31.11.2001", "Larry@hotmail.com", 88, 11.5, 165, False),
    ("Sam Kerr", "09.12.2006", "sam@news.com.au", 198, 8.9, 192, True),
    ("Robyn Pears", "28.01.1960", "robyn@gmail.com", 232, 13.5, 180, True),
    ("Madison XI", "12.09.1998", "madison@hotmail.com", 85, 7.6, 163, True),
    ("Kris Wu", "19.10.2030", "Kris.wu.gmail.com", 79, 16.5, 197, False),
    ("Tenille Singh", "30.11.1991", "Tenille@yahoo.com", 101, 15, 310, True),
    ("Prem Kumar", "28.04.1990", "prem@telstra.com", 155, 12.5, 265, True),
]


def as_payload(case):
    name, dob, email, glucose, haemoglobin, cholesterol, _ = case
    return dict(
        name=name,
        dob=dob,
        email=email,
        glucose=glucose,
        haemoglobin=haemoglobin,
        cholesterol=cholesterol,
    )


def test_ten_required_scenarios_and_duplicate(client):
    for index, case in enumerate(CASES):
        response = client.post("/api/patients", json=as_payload(case))
        if index == 5:
            assert response.status_code == 409
            assert "already exists" in response.get_json()["errors"]["email"]
        elif index == 7:
            assert response.status_code == 400
            assert set(response.get_json()["errors"]) >= {"dob", "email"}
        elif not case[-1]:
            assert response.status_code == 400
        else:
            assert response.status_code == 201
    records = client.get("/api/patients").get_json()
    assert len(records) == 6
    assert next(row for row in records if row["name"] == "Madison XI")
    assert {row["dob"] for row in records} >= {"28/01/1960", "09/12/2006"}


def test_edit_email_does_not_rerun_ai_and_delete_refreshes(client, monkeypatch):
    response = client.post("/api/patients", json=as_payload(CASES[0]))
    original = response.get_json()["remarks"]
    monkeypatch.setattr(
        app_module, "get_prediction", lambda *args: pytest.fail("email-only update called AI")
    )
    update = as_payload(CASES[0])
    update["email"] = "gokul@infocare.com"
    assert client.put("/api/patients/1", json=update).get_json()["remarks"] == original
    assert client.get("/api/patients").get_json()[0]["email"] == "gokul@infocare.com"
    assert client.delete("/api/patients/1").status_code == 200
    assert client.get("/api/patients").get_json() == []


def test_edit_lab_value_reruns_ai(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        app_module,
        "get_prediction",
        lambda values, app: (
            calls.append(values.copy())
            or "Updated risk. Risk level: Moderate. Consult a healthcare provider."
        ),
    )
    assert client.post("/api/patients", json=as_payload(CASES[0])).status_code == 201
    updated = as_payload(CASES[0])
    updated["glucose"] = 106
    response = client.put("/api/patients/1", json=updated)
    assert response.status_code == 200 and len(calls) == 2
    assert response.get_json()["remarks"].startswith("Updated risk")


def test_update_to_an_existing_email_and_dob_is_rejected_before_ai(client, monkeypatch):
    first = as_payload(CASES[0])
    second = as_payload(CASES[2])
    assert client.post("/api/patients", json=first).status_code == 201
    assert client.post("/api/patients", json=second).status_code == 201
    monkeypatch.setattr(
        app_module,
        "get_prediction",
        lambda *args: pytest.fail("AI called for duplicate update"),
    )
    response = client.put(
        "/api/patients/2", json={**second, "email": first["email"], "dob": first["dob"]}
    )
    assert response.status_code == 409
    assert "already exists" in response.get_json()["errors"]["email"]


@pytest.mark.parametrize(
    "dob,expected",
    [
        ("12/05/1988", "12/05/1988"),
        ("12.05.1988", "12/05/1988"),
        ("12-05-1988", "12/05/1988"),
        ("03 - 09 - 2004", "03/09/2004"),
    ],
)
def test_date_formats_normalize(client, dob, expected):
    payload = as_payload(CASES[0])
    payload["dob"] = dob
    assert client.post("/api/patients", json=payload).get_json()["dob"] == expected


def test_invalid_input_returns_all_field_errors_before_ai_or_insert(client, monkeypatch):
    monkeypatch.setattr(
        app_module, "get_prediction", lambda *args: pytest.fail("AI called for invalid data")
    )
    monkeypatch.setattr(
        app_module, "connect_db", lambda *args: pytest.fail("database accessed for invalid data")
    )
    payload = as_payload(CASES[7])
    response = client.post("/api/patients", json=payload)
    assert response.status_code == 400
    assert set(response.get_json()["errors"]) == {"dob", "email"}


@pytest.mark.parametrize("dob", ["31.11.2001", "01/01/1800", "01/01/2030"])
def test_impossible_too_old_and_future_dates_rejected(client, dob):
    response = client.post("/api/patients", json={**as_payload(CASES[0]), "dob": dob})
    assert response.status_code == 400 and "dob" in response.get_json()["errors"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", None),
        ("name", 123),
        ("email", "first..last@example.com"),
        ("email", ".first@example.com"),
        ("email", "person@localhost"),
    ],
)
def test_non_string_names_and_malformed_emails_are_rejected(client, field, value):
    response = client.post("/api/patients", json={**as_payload(CASES[0]), field: value})
    assert response.status_code == 400 and field in response.get_json()["errors"]


def test_name_length_boundary(client):
    payload = as_payload(CASES[0])
    payload["name"] = "A" * 120
    assert client.post("/api/patients", json=payload).status_code == 201
    payload["name"] = "A" * 121
    response = client.post("/api/patients", json=payload)
    assert response.status_code == 400 and "name" in response.get_json()["errors"]


def test_email_is_lowercased_before_storage(client):
    response = client.post("/api/patients", json=as_payload(CASES[8]))
    assert response.status_code == 201
    assert response.get_json()["email"] == "tenille@yahoo.com"


@pytest.mark.parametrize(
    "field,value",
    [
        ("glucose", 0),
        ("glucose", 2001),
        ("glucose", True),
        ("glucose", "nan"),
        ("haemoglobin", -1),
        ("haemoglobin", 41),
        ("cholesterol", 0),
        ("cholesterol", 2001),
    ],
)
def test_lab_bounds(client, field, value):
    response = client.post("/api/patients", json={**as_payload(CASES[0]), field: value})
    assert response.status_code == 400
    assert field in response.get_json()["errors"]


def test_crud_not_found_and_duplicate(client):
    payload = as_payload(CASES[0])
    assert client.get("/api/patients/99").status_code == 404
    assert client.put("/api/patients/99", json=payload).status_code == 404
    assert client.delete("/api/patients/99").status_code == 404
    assert client.post("/api/patients", json=payload).status_code == 201
    conflict = client.post("/api/patients", json=payload)
    assert conflict.status_code == 409 and "errors" in conflict.get_json()


def test_database_has_email_and_dob_uniqueness_index(client):
    with app_module.connect_db(client.application) as db:
        index = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("uq_patients_email_dob",),
        ).fetchone()
    assert index and "lower(email), dob" in index["sql"]


def test_ai_provider_failure_modes(monkeypatch):
    import ai_service

    patient = {**as_payload(CASES[0]), "dob": "28/01/1960"}
    for key in ("AI_BASE_URL", "AI_MODEL", "AI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    remark = ai_service.predict_health_risk(patient)
    assert "local .env" in remark
    assert "AI_BASE_URL, AI_MODEL, AI_API_KEY" in remark


def test_ai_template_placeholders_are_treated_as_missing(monkeypatch, caplog):
    import ai_service

    patient = {**as_payload(CASES[0]), "dob": "28/01/1960"}
    monkeypatch.setenv("AI_BASE_URL", "https://api.groq.com/openai/v1/chat/completions")
    monkeypatch.setenv("AI_MODEL", "YOUR_GROQ_MODEL")
    monkeypatch.setenv("AI_API_KEY", "YOUR_GROQ_API_KEY")
    monkeypatch.setattr(
        ai_service.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("provider called with template credentials"),
    )
    remark = ai_service.predict_health_risk(patient)
    assert "AI_MODEL, AI_API_KEY" in remark
    assert "AI_MODEL" in caplog.text and "AI_API_KEY" in caplog.text


def test_ai_timeout_retries_once(monkeypatch):
    import ai_service
    from requests import Timeout

    patient = {**as_payload(CASES[0]), "dob": "28/01/1960"}
    for key, value in (
        ("AI_BASE_URL", "https://provider.invalid/v1/chat/completions"),
        ("AI_MODEL", "test"),
        ("AI_API_KEY", "test-key"),
    ):
        monkeypatch.setenv(key, value)
    calls = []

    def timeout(*args, **kwargs):
        calls.append(kwargs["timeout"])
        raise Timeout("simulated timeout")

    monkeypatch.setattr(ai_service.requests, "post", timeout)
    assert "Prediction unavailable" in ai_service.predict_health_risk(patient)
    assert calls == [15, 15]


def test_ai_bad_key_and_malformed_response(monkeypatch):
    import ai_service

    patient = {**as_payload(CASES[0]), "dob": "28/01/1960"}
    for key, value in (
        ("AI_BASE_URL", "https://provider.invalid/v1/chat/completions"),
        ("AI_MODEL", "test"),
        ("AI_API_KEY", "bad"),
    ):
        monkeypatch.setenv(key, value)

    class Response:
        status_code = 400

        def json(self):
            return {"error": {"message": "Invalid Auth key."}}

        def raise_for_status(self):
            raise ai_service.requests.HTTPError("bad key", response=self)

    calls = []
    monkeypatch.setattr(ai_service.requests, "post", lambda *a, **k: calls.append(k) or Response())
    assert (
        "provider rejected the API key" in ai_service.predict_health_risk(patient)
        and len(calls) == 1
    )
    monkeypatch.setattr(
        ai_service.requests,
        "post",
        lambda *a, **k: type(
            "R",
            (),
            {
                "raise_for_status": lambda self: None,
                "json": lambda self: {"choices": [{"message": {"content": "maybe concerning"}}]},
            },
        )(),
    )
    assert "Prediction unavailable" in ai_service.predict_health_risk(patient)


def test_ai_bad_request_does_not_retry(monkeypatch):
    import ai_service

    patient = {**as_payload(CASES[0]), "dob": "28/01/1960"}
    for key, value in (
        ("AI_BASE_URL", "https://provider.invalid/v1/chat/completions"),
        ("AI_MODEL", "bad-model"),
        ("AI_API_KEY", "test-key"),
    ):
        monkeypatch.setenv(key, value)

    class BadRequest:
        status_code = 400

        def json(self):
            return {"error": {"message": "model does not exist"}}

        def raise_for_status(self):
            raise ai_service.requests.HTTPError("bad request", response=self)

    calls = []
    monkeypatch.setattr(
        ai_service.requests, "post", lambda *a, **k: calls.append(1) or BadRequest()
    )
    remark = ai_service.predict_health_risk(patient)
    assert "provider rejected this request" in remark and calls == [1]


def test_groq_gpt_oss_disables_reasoning_and_returns_remark(monkeypatch):
    import ai_service

    patient = {**as_payload(CASES[0]), "dob": "28/01/1960"}
    monkeypatch.setenv("AI_BASE_URL", "https://api.groq.com/openai/v1/chat/completions")
    monkeypatch.setenv("AI_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("AI_API_KEY", "test-key")
    request = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "Possible low risk. Risk level: Low. Please consult a healthcare provider."
                        }
                    }
                ]
            }

    def post(*args, **kwargs):
        request.update(kwargs)
        return Response()

    monkeypatch.setattr(ai_service.requests, "post", post)
    assert "Risk level: Low" in ai_service.predict_health_risk(patient)
    assert request["json"]["include_reasoning"] is False
    assert request["json"]["max_completion_tokens"] == 512


def test_ui_route_and_rerun(client):
    page = client.get("/")
    assert b"Patient overview" in page.data
    assert b"Average haemoglobin" in page.data
    assert b"aiNotice" in page.data
    assert b"styles.css?v=4" in page.data
    assert b"app.js?v=4" in page.data
    assert b"QUICK FILTERS" in page.data
    assert b'data-filter="out-of-range"' in page.data
    assert b'data-filter="high-risk"' in page.data
    assert b'data-filter="ai-review"' in page.data
    assert b'id="exportCsv"' in page.data
    patient = client.post("/api/patients", json=as_payload(CASES[0])).get_json()
    assert client.post(f"/api/patients/{patient['id']}/prediction").status_code == 200
