from fastapi.testclient import TestClient

from task_agent.api import create_app
from task_agent.watcher import Watcher


def _client(pipeline):
    w = Watcher(pipeline)
    w.process_all()
    w.shutdown()
    return TestClient(create_app(pipeline))


def test_health_and_list(pipeline, inbox):
    client = _client(pipeline)
    assert client.get("/health").json()["llm_provider"] == "mock"
    held = client.get("/tasks", params={"status": "awaiting_approval"}).json()
    assert {t["id"] for t in held} == {"05_price_update", "07_schedule_meeting",
                                       "09_quarterly_forecast", "10_vague_request"}


def test_task_detail_includes_audit(pipeline, inbox):
    client = _client(pipeline)
    body = client.get("/tasks/08_drop_table").json()
    assert body["status"] == "refused"
    assert [a["event"] for a in body["audit"]][-1] == "refused"
    assert client.get("/tasks/nope").status_code == 404


def test_approve_and_reject_endpoints(pipeline, inbox):
    client = _client(pipeline)
    r = client.post("/tasks/05_price_update/approve", json={"approver": "boss"})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    r = client.post("/tasks/05_price_update/approve", json={"approver": "boss"})
    assert r.status_code == 409
    r = client.post("/tasks/07_schedule_meeting/reject", json={"approver": "boss", "reason": "dup"})
    assert r.json()["status"] == "rejected"
    assert client.post("/tasks/07_schedule_meeting/reject", json={"approver": ""}).status_code == 422


def test_audit_endpoint(pipeline, inbox):
    client = _client(pipeline)
    events = client.get("/audit", params={"task_id": "01_weekly_sales_summary"}).json()
    assert [e["event"] for e in events] == ["received", "classified", "executed", "completed"]
