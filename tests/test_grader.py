import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from intercom_summary.intercom.models import Admin, Conversation, Message
from intercom_summary.qa.grader import Grader
from intercom_summary.qa.grading_common import GradeParseError
from intercom_summary.qa.report import aggregate, report_markdown
from intercom_summary.qa.rulesets import get_ruleset


class FakeClaude:
    """Mimics `client.beta.messages.create`: replies from a queue, records every request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return reply


def reply(data=None, *, stop_reason="end_turn", model="claude-sonnet-5-5", text=None,
          category=None):
    body = text if text is not None else json.dumps(data)
    content = [
        SimpleNamespace(type="thinking", thinking=""),   # adaptive thinking, display omitted
        SimpleNamespace(type="text", text=body),
    ]
    return SimpleNamespace(
        content=content, stop_reason=stop_reason, model=model,
        stop_details=SimpleNamespace(category=category) if category else None,
        usage=SimpleNamespace(input_tokens=900, cache_read_input_tokens=3000,
                              cache_creation_input_tokens=0, output_tokens=700),
    )


def _convo():
    return Conversation(
        id="42",
        created_at=datetime(2026, 10, 6, tzinfo=timezone.utc),
        updated_at=None,
        state="closed",
        subject="Withdrawal",
        assignee=Admin(id="1", name="Ada", email="ada@co.com"),
        messages=[Message(0, "admin", "Ada", None, "Hello, how can I help?")],
    )


def _v41(**over):
    data = {
        "case_type": "Withdrawal", "risk_flag": "Financial",
        "expected_handling": "Agent action", "data_sufficiency": "Sufficient",
        "requests": [{"text": "Where is my withdrawal?", "status": "unresolved", "material": True}],
        "criteria": [
            {"id": "resp-no-ghost", "v": "fail", "ev": "Hello, how can I help?"},
            {"id": "comm-tone", "v": "fail", "ev": "Hello, how can I help?"},
            {"id": "crit-data-care", "v": "pass", "ev": "no card data requested"},
        ],
        "outcome_status": "Unresolved",
        "summary": "Never answered the withdrawal question.",
        "manual_review_needed": False,
        # The model may still volunteer a number; it must be ignored.
        "overall_score": 99,
    }
    data.update(over)
    return data


def _flat(**over):
    data = {
        "overall_score": 12,  # ignored — the flat model recomputes from the catalogue too
        "critical_fail": False,
        "criteria": [
            {"id": "open-greet", "v": "pass", "ded": 0, "ev": "Hello, how can I help?"},
            {"id": "info-actionable", "v": "fail", "ded": -8, "ev": "Hello, how can I help?"},
        ],
        "violations": ["Did not confirm resolution"],
        "summary": "ok",
        "coaching": ["Ask if anything else is needed"],
    }
    data.update(over)
    return data


# ── the v4.1 gated ruleset ─────────────────────────────────────────────────────────────
def test_v41_grade_is_scored_by_the_gates_not_the_model():
    fake = FakeClaude(reply(_v41()))
    grade = Grader(ruleset_id="kb-v41", client=fake).grade(_convo())

    # Major Outcome Failure caps at 75; comm-tone −3 → 72. The model's 99 is ignored.
    assert grade.overall_score == 72
    assert grade.overall_result == "FAIL"
    assert grade.case_type == "Withdrawal"
    assert grade.risk_flag == "Financial"
    assert grade.outcome_status == "Unresolved"
    assert grade.critical_fail is False
    assert grade.ruleset_id == "kb-v41"
    assert grade.agent_email == "ada@co.com"


def test_rules_version_is_the_prompt_hash_both_backends_stamp():
    """Same stamp as OllamaGrader, so switching backend leaves existing grades current."""
    rs = get_ruleset("kb-v41")
    grader = Grader(ruleset_id="kb-v41", client=FakeClaude(reply(_v41())))
    assert grader.rules_version == rs.version
    assert grader.grade(_convo()).rules_version == rs.version


def test_grade_records_the_model_that_actually_served_it():
    fake = FakeClaude(reply(_v41(), model="claude-opus-5-5"))   # e.g. a refusal fallback
    grade = Grader(ruleset_id="kb-v41", client=fake).grade(_convo())
    assert grade.model == "claude-opus-5-5"


def test_request_shape_is_valid_for_sonnet_5_5():
    fake = FakeClaude(reply(_v41()))
    Grader(ruleset_id="kb-v41", model="claude-sonnet-5-5", client=fake).grade(_convo())
    call = fake.calls[0]

    assert call["model"] == "claude-sonnet-5-5"
    # Forced tool use and non-default sampling are both 400s on Sonnet 5.5.
    assert "tool_choice" not in call and "tools" not in call
    assert "temperature" not in call and "thinking" not in call
    fmt = call["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert call["output_config"]["effort"] in ("low", "medium", "high", "xhigh", "max")
    # The ruleset prompt is the cached system block; the transcript is the user turn.
    system = call["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert system[0]["text"] == get_ruleset("kb-v41").prompt_text
    assert "Hello, how can I help?" in call["messages"][0]["content"]


def test_refusal_fallback_is_requested_and_can_be_turned_off():
    from intercom_summary.settings import settings

    fake = FakeClaude(reply(_v41()))
    Grader(ruleset_id="kb-v41", client=fake).grade(_convo())
    assert fake.calls[0]["extra_body"] == {"fallbacks": "default"}
    assert fake.calls[0]["betas"] == ["server-side-fallback-2026-07-01"]

    # settings is a frozen dataclass — bypass with object.__setattr__.
    object.__setattr__(settings, "qa_refusal_fallback", False)
    try:
        fake = FakeClaude(reply(_v41()))
        Grader(ruleset_id="kb-v41", client=fake).grade(_convo())
        assert "extra_body" not in fake.calls[0] and "betas" not in fake.calls[0]
    finally:
        object.__setattr__(settings, "qa_refusal_fallback", True)


def test_refusal_raises_so_the_chat_is_skipped_not_saved():
    fake = FakeClaude(reply(text="", stop_reason="refusal", category="general_harms"))
    with pytest.raises(GradeParseError, match="general_harms"):
        Grader(ruleset_id="kb-v41", client=fake).grade(_convo())
    assert len(fake.calls) == 1   # a refusal is not retried — the fallback already was


def test_unusable_output_is_retried_once_then_raises():
    fake = FakeClaude(reply(_v41(criteria=[])))
    with pytest.raises(GradeParseError):
        Grader(ruleset_id="kb-v41", client=fake).grade(_convo())
    assert len(fake.calls) == 2


def test_truncated_output_is_retried_and_the_retry_is_used():
    fake = FakeClaude(reply(text='{"case_type": "Withd', stop_reason="max_tokens"), reply(_v41()))
    grade = Grader(ruleset_id="kb-v41", client=fake).grade(_convo())
    assert len(fake.calls) == 2
    assert grade.overall_score == 72


# ── the flat rulesets (pre-v4.1 standard, VIP) ─────────────────────────────────────────
def test_flat_ruleset_grade_is_recomputed_from_the_catalogue():
    fake = FakeClaude(reply(_flat()))
    grade = Grader(ruleset_id="default", client=fake).grade(_convo())
    expected = 100 - get_ruleset("default").deductions["info-actionable"]
    assert grade.overall_score == expected
    assert grade.violations == ["Did not confirm resolution"]
    assert grade.ruleset_id == "default"


def test_aggregate_and_report():
    grade = Grader(ruleset_id="default", client=FakeClaude(reply(_flat()))).grade(_convo())
    agg = aggregate([grade])
    assert agg["Ada"]["count"] == 1
    assert agg["Ada"]["avg_score"] == float(grade.overall_score)
    md = report_markdown([grade])
    assert "Ada" in md and f"{grade.overall_score}/100" in md


def test_report_xlsx_prints_display_names_for_overrides(tmp_path):
    """The export is for humans: "Overridden By" should read the name, not the login."""
    from openpyxl import load_workbook

    from intercom_summary.qa.report import report_xlsx

    grade = Grader(ruleset_id="default", client=FakeClaude(reply(_flat()))).grade(_convo())
    grade.human_score = 90
    grade.overridden_by = "analyst"

    header_and_row = lambda path: load_workbook(path)["Conversations"]

    # Mapped → the name.
    ws = header_and_row(report_xlsx([grade], tmp_path / "named.xlsx", {"analyst": "Daria"}))
    assert ws.cell(row=1, column=5).value == "Overridden By"
    assert ws.cell(row=2, column=5).value == "Daria"

    # Unmapped, and no map at all → the raw username, exactly as before.
    ws = header_and_row(report_xlsx([grade], tmp_path / "other.xlsx", {"kate": "Kate"}))
    assert ws.cell(row=2, column=5).value == "analyst"
    ws = header_and_row(report_xlsx([grade], tmp_path / "bare.xlsx"))
    assert ws.cell(row=2, column=5).value == "analyst"


# ── structured-output schema ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("rid", ["default", "vip", "kb-v41"])
def test_strict_schema_closes_every_object_and_pins_criterion_ids(rid):
    from intercom_summary.qa.rulesets import strict_schema

    rs = get_ruleset(rid)
    schema = strict_schema(rs)

    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)

    objs = list(objects(schema))
    assert objs and all(o["additionalProperties"] is False for o in objs)
    ids = schema["properties"]["criteria"]["items"]["properties"]["id"]["enum"]
    assert ids == [c["id"] for c in rs.criteria]
    # Field names under `properties` survive (they are not JSON-schema keywords).
    assert "summary" in schema["properties"]
