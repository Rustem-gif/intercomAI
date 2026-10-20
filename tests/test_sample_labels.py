"""QA's reference verdicts on a calibration sample, and how a run is measured against them."""
import sys
from pathlib import Path

from intercom_summary.storage.calibration_store import CalibrationStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import import_calibration_sample as imp  # noqa: E402


def _sample(db):
    cal = CalibrationStore(db)
    cal.create("s", "sample")
    cal.replace_items("s", [{"conversation_id": "111111", "seq": 1},
                            {"conversation_id": "222222", "seq": 2}])
    return cal


def test_labels_attach_to_members_and_never_change_membership(tmp_path):
    cal = _sample(tmp_path / "c.db")
    res = cal.set_labels("s", [
        {"conversation_id": "111111", "human_score": 92, "human_note": "ok"},
        {"conversation_id": "222222", "human_result": "fail"},
        {"conversation_id": "999999", "human_score": 50},
    ], labelled_by="kate")
    assert res == {"labelled": 2, "not_in_sample": ["999999"]}
    labels = cal.labels("s")
    assert labels["111111"]["human_score"] == 92 and labels["222222"]["human_result"] == "FAIL"
    assert cal.conversation_ids("s") == ["111111", "222222"]      # frozen membership untouched
    cal.close()


def test_a_run_row_falls_back_to_the_reference_label(tmp_path):
    from intercom_summary.web.api import _calibration_row, _calibration_summary

    cal = _sample(tmp_path / "c.db")
    cal.set_labels("s", [{"conversation_id": "111111", "human_score": 92},
                         {"conversation_id": "222222", "human_result": "FAIL"}])
    run = cal.start_run("s", "kb-v41")
    for cid, score, result in (("111111", 70, "FAIL"), ("222222", 60, "FAIL")):
        cal._conn.execute(
            "INSERT INTO calibration_results (run_id, conversation_id, status, overall_score, "
            "payload_json) VALUES (?,?,?,?,?)",
            (run, cid, "graded", score, f'{{"overall_result": "{result}", "rule_results": []}}'))
    cal._conn.commit()
    rows = [_calibration_row(r, "kb-v41") for r in cal.results(run)]
    cal.close()
    a, b = rows
    assert a["human_source"] == "sample" and a["human_result"] == "PASS"
    assert a["result_agrees"] is False and a["delta"] == 22
    assert b["human_result"] == "FAIL" and b["result_agrees"] is True and b["delta"] is None
    s = _calibration_summary(rows)
    assert s["result_compared"] == 2 and s["result_agreement"] == 50.0


def test_label_parser_reads_a_headed_table():
    rows = [
        ["Звіт", "x"],
        ["№", "Chat ID", "Бал QA", "Результат", "Коментар"],
        ["1", "215561100542128", "92", "PASS", "tone a bit dry"],
        ["2", "215561109180469", "", "Fail", "missed escalation"],
        ["3", "215561082277825.0", "0.75", "", ""],
        ["4", "not-an-id", "90", "", ""],
    ]
    labels = imp.parse_labels(rows)
    assert [(lab["conversation_id"], lab["human_score"], lab["human_result"]) for lab in labels] == [
        ("215561100542128", 92, "PASS"),
        ("215561109180469", None, "FAIL"),
        ("215561082277825", 75, None),
    ]
    assert labels[0]["human_note"] == "tone a bit dry"


def test_the_member_list_document_has_no_verdicts():
    """The QA department's sample list (№, Chat ID, date, agent, category, pilot, assessor,
    status, link) carries no score column — nothing must be read as a verdict."""
    rows = [["№", "Chat ID", "Дата", "Агент", "Категорія", "Пілот-20", "Оцінювач(і)", "Статус",
             "Посилання"],
            ["1", "215561100542128", "2026-08-03", "Oswald", "Скарга", "Так", "—", "Не розпочато",
             "Відкрити ↗"]]
    assert imp.parse_labels(rows) == []
