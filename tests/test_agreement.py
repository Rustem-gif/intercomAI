"""AI ↔ QA PASS/FAIL agreement (qa/agreement.py) and the QC manager's "Agree"."""
from intercom_summary.qa.agreement import (
    paired_agreement,
    population_agreement,
    result_of,
    split_of,
    wilson,
)
from intercom_summary.qa.schema import ConversationGrade
from intercom_summary.storage.grades_store import GradesStore


def test_split_is_stable_and_roughly_70_30():
    ids = [str(215561000000000 + i) for i in range(2000)]
    first = [split_of(i) for i in ids]
    assert first == [split_of(i) for i in ids]
    share = first.count("holdout") / len(first)
    assert 0.25 < share < 0.35


def test_result_of_uses_the_85_threshold():
    assert result_of(85) == "PASS" and result_of(84) == "FAIL" and result_of(None) is None
    assert result_of(10, "PASS") == "PASS"


def test_population_agreement_weights_each_side_by_how_often_the_ai_gives_it():
    # 90 AI PASS (10 reviewed, all agree), 10 AI FAIL (10 reviewed, 2 agree).
    rows = ([{"ai": "PASS", "human": "PASS"}] * 10 + [{"ai": "PASS", "human": None}] * 80
            + [{"ai": "FAIL", "human": "FAIL"}] * 2 + [{"ai": "FAIL", "human": "PASS"}] * 8)
    r = population_agreement(rows)
    assert r["estimate"] == 0.92                 # 0.9·1.0 + 0.1·0.2
    assert r["reviewed_only"] == 0.6             # the misleading reviewed-only ratio
    assert r["known_disagreements"] == 8
    assert r["ci95"][0] < 0.92 < r["ci95"][1]


def test_an_unreviewed_side_makes_the_estimate_unknown():
    r = population_agreement([{"ai": "PASS", "human": None}, {"ai": "FAIL", "human": "PASS"}])
    assert r["estimate"] is None and r["sides"]["PASS"]["rate"] is None


def test_paired_agreement_and_wilson():
    a = {"1": "FAIL", "2": "PASS", "3": "FAIL"}
    b = {"1": "PASS", "2": "PASS", "3": "FAIL", "4": "PASS"}
    r = paired_agreement(a, b)
    assert (r["n"], r["same"], r["a_fail_b_pass"]) == (3, 2, ["1"])
    lo, hi = wilson(95, 100)
    assert 0.88 < lo < 0.95 < hi < 0.99


def _save(store, cid, score):
    store.save(ConversationGrade(conversation_id=cid, agent_name="Ada", overall_score=score,
                                 summary="s", rules_version="r", model="m", ruleset_id="kb-v41",
                                 graded_at="2026-10-10T00:00:00+00:00"))


def test_confirm_counts_as_agreement_and_a_regrade_clears_it(tmp_path):
    store = GradesStore(tmp_path / "g.db")
    for cid, score in (("1", 95), ("2", 70), ("3", 100)):
        _save(store, cid, score)
    assert store.confirm("1", "qa")
    store.save_override("2", 100, "no mistakes", "qa")
    assert not store.confirm("2", "qa")              # a re-scored grade has a human verdict
    r = store.result_agreement("kb-v41")
    assert r["sides"]["PASS"] == {**r["sides"]["PASS"], "reviewed": 1, "agreed": 1}
    assert r["sides"]["FAIL"]["reviewed"] == 1 and r["sides"]["FAIL"]["agreed"] == 0
    assert r["confirmed"] == 1 and r["overridden"] == 1
    assert store.get("1")["human_confirmed_by"] == "qa"
    _save(store, "1", 90)                            # the AI re-grades: the agreement was
    assert store.get("1")["human_confirmed_by"] is None   # with a grade that no longer exists
    store.close()
