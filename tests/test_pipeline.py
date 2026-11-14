import json

import pytest

from task_agent.watcher import Watcher

from .conftest import make_task

EXPECTED_FIRST_PASS = {
    "01_weekly_sales_summary": "completed",
    "02_followup_by_product": "completed",
    "03_vendor_invoice": "completed",
    "04_low_stock_check": "completed",
    "05_price_update": "awaiting_approval",
    "06_customer_complaint": "completed",
    "07_schedule_meeting": "awaiting_approval",
    "08_drop_table": "refused",
    "09_quarterly_forecast": "awaiting_approval",
    "10_vague_request": "awaiting_approval",
}


def run_inbox(pipeline):
    w = Watcher(pipeline)
    w.process_all()
    w.shutdown()


def test_sample_inbox_end_to_end(pipeline, inbox):
    run_inbox(pipeline)
    got = {t["id"]: t["status"] for t in pipeline.db.list_tasks()}
    assert got == EXPECTED_FIRST_PASS
    # inbox was claimed: folders moved to processed/
    assert not [p for p in inbox.iterdir() if p.name != "processed"]


def test_follow_up_uses_thread_memory(pipeline, inbox, settings):
    run_inbox(pipeline)
    response = (settings.output_dir / "02_followup_by_product" / "response.md").read_text()
    assert "By product" in response and "Trail Tent 2P $5,191.00" in response
    events = [a["event"] for a in pipeline.db.audit_log("02_followup_by_product")]
    assert "memory_loaded" in events
    memory = (settings.output_dir / "threads" / "T-100" / "memory.md").read_text()
    assert memory.count("intent: sales_summary") == 2


def test_invoice_mismatch_is_flagged(pipeline, inbox, settings):
    run_inbox(pipeline)
    data = json.loads((settings.output_dir / "03_vendor_invoice" / "invoice.json").read_text())
    assert data["computed_subtotal"] == 847.50 and data["stated_subtotal"] == 867.50
    assert data["issues"]


def test_approval_executes_and_is_audited(pipeline, inbox, settings):
    run_inbox(pipeline)
    assert pipeline.approve("05_price_update", "boss@bluebird.example.com") == "completed"
    csv_text = (settings.output_dir / "05_price_update" / "price_list.csv").read_text()
    assert "BB-104,Insulated Bottle,27.99" in csv_text
    actors = {a["event"]: a["actor"] for a in pipeline.db.audit_log("05_price_update")}
    assert actors["approved"] == "boss@bluebird.example.com"


def test_reject_and_double_decision(pipeline, inbox):
    run_inbox(pipeline)
    assert pipeline.reject("07_schedule_meeting", "boss", "no") == "rejected"
    with pytest.raises(ValueError):
        pipeline.approve("07_schedule_meeting", "boss")
    with pytest.raises(KeyError):
        pipeline.approve("does_not_exist", "boss")


def test_refused_task_writes_explanation(pipeline, inbox, settings):
    run_inbox(pipeline)
    text = (settings.output_dir / "08_drop_table" / "response.md").read_text()
    assert "DROP statement" in text and "No action was taken" in text


def test_bad_input_fails_without_retrying(pipeline, settings):
    folder = make_task(settings.inbox_dir, "x1", "Weekly sales summary please")  # no CSV
    assert pipeline.handle(folder) == "failed"
    assert pipeline.db.get_task("x1")["attempts"] == 1


class FlakyLLM:
    """Returns garbage first, then valid JSON: exercises the classify retry path."""
    name = "flaky"

    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def complete(self, prompt, *, purpose):
        self.calls += 1
        if purpose == "classify" and self.calls == 1:
            return "I'm not sure, sorry!"
        return self.inner.complete(prompt, purpose=purpose)


def test_classifier_retries_on_malformed_output(pipeline, settings):
    pipeline.llm = FlakyLLM(pipeline.llm)
    folder = make_task(settings.inbox_dir, "x2", "Can you draft a reply to this complaint?")
    assert pipeline.handle(folder) == "completed"
    assert "classify_retry" in [a["event"] for a in pipeline.db.audit_log("x2")]


class BrokenLLM:
    name = "broken"

    def complete(self, prompt, *, purpose):
        raise ConnectionError("provider down")


def test_provider_outage_parks_task_for_human(pipeline, settings):
    pipeline.llm = BrokenLLM()
    folder = make_task(settings.inbox_dir, "x3", "Weekly sales summary please")
    assert pipeline.handle(folder) == "awaiting_approval"
    assert pipeline.db.get_task("x3")["intent"] == "general_question"


def test_invalid_task_folder_does_not_crash_watcher(pipeline, settings):
    bad = settings.inbox_dir / "broken_task"
    bad.mkdir(parents=True)
    (bad / "email.json").write_text("{not json")
    w = Watcher(pipeline)
    assert w.process_all() == ["invalid"]
    w.shutdown()
