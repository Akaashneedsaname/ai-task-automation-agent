import json
from pathlib import Path

import pytest

from task_agent.classifier import ClassificationError, parse_classification
from task_agent.models import Classification, EmailTask
from task_agent.safety import evaluate, find_prohibited

INTERNAL = {"bluebird.example.com"}


def _task(body: str, sender: str = "x@bluebird.example.com") -> EmailTask:
    return EmailTask("t1", sender, "subj", body, "T", "", [])


def _c(**kw) -> Classification:
    base = dict(intent="sales_summary", summary="", plan=[], estimated_minutes=5,
                actions=[], confidence=0.9)
    return Classification(**{**base, **kw})


def test_parse_accepts_json_wrapped_in_text():
    raw = 'Sure! {"intent": "draft_reply", "summary": "s", "plan": ["a"], ' \
          '"estimated_minutes": 3, "actions": [], "confidence": 0.8} hope that helps'
    c = parse_classification(raw)
    assert c.intent == "draft_reply" and c.estimated_minutes == 3


@pytest.mark.parametrize("raw", [
    "no json here",
    '{"intent": "launch_rockets", "estimated_minutes": 1}',
    '{"intent": "draft_reply", "estimated_minutes": "soon"}',
    '{"intent": "draft_reply", "plan": "not a list"}',
])
def test_parse_rejects_bad_output(raw):
    with pytest.raises(ClassificationError):
        parse_classification(raw)


def test_parse_clamps_confidence():
    raw = json.dumps({"intent": "draft_reply", "confidence": 7, "estimated_minutes": -4})
    c = parse_classification(raw)
    assert c.confidence == 1.0 and c.estimated_minutes == 0


@pytest.mark.parametrize("text", [
    "please DROP TABLE orders", "truncate the logs", "DELETE FROM customers;",
    "delete all records from last year",
])
def test_prohibited_operations_detected(text):
    assert find_prohibited(text)


def test_scoped_delete_is_not_prohibited():
    assert not find_prohibited("DELETE FROM carts WHERE id = 42;")


def test_refusal_beats_everything():
    d = evaluate(_task("DROP TABLE orders"), _c(confidence=1.0), minutes_threshold=30,
                 internal_domains=INTERNAL)
    assert d.status == "refused"


def test_read_only_task_proceeds():
    d = evaluate(_task("weekly sales"), _c(), minutes_threshold=30, internal_domains=INTERNAL)
    assert d.status == "proceed" and d.reasons == []


@pytest.mark.parametrize("kw,expected", [
    ({"actions": ["update_records"]}, "side effects"),
    ({"estimated_minutes": 90}, "90 min"),
    ({"confidence": 0.2}, "low classification confidence"),
])
def test_needs_approval_reasons(kw, expected):
    d = evaluate(_task("x"), _c(**kw), minutes_threshold=30, internal_domains=INTERNAL)
    assert d.status == "needs_approval"
    assert any(expected in r for r in d.reasons)


def test_external_sender_with_side_effect_flagged():
    d = evaluate(_task("x", sender="someone@outside.example.org"),
                 _c(actions=["make_payment"]), minutes_threshold=30, internal_domains=INTERNAL)
    assert "external sender requested a side effect" in d.reasons
