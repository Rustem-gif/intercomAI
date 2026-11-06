import gspread
import pytest

from intercom_summary import backup, sheets_export
from intercom_summary.settings import settings
from intercom_summary.storage.db import connect
from intercom_summary.storage.grades_store import GradesStore


class FakeWS:
    def __init__(self, title):
        self.title, self.cells, self.appended, self.cleared = title, {}, [], 0

    def clear(self):
        self.cells, self.cleared = {}, self.cleared + 1

    def resize(self, rows, cols):
        pass

    def freeze(self, rows):
        pass

    def update(self, range_name, values, value_input_option=None):
        start = int(range_name[1:])
        for i, row in enumerate(values):
            self.cells[start + i] = row

    def append_rows(self, rows, value_input_option=None):
        self.appended.extend(rows)

    def get_values(self, rng):
        return [self.cells[1][:1]] if 1 in self.cells else []

    def rows(self):
        return [self.cells[k] for k in sorted(self.cells)]


class FakeBook:
    def __init__(self, fail=False):
        self.tabs, self.fail = {}, fail

    def worksheet(self, title):
        if self.fail:
            raise RuntimeError("403 The caller does not have permission")
        if title not in self.tabs:
            raise gspread.WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = FakeWS(title)
        return self.tabs[title]


class FakeClient:
    def __init__(self, book):
        self.book = book

    def open_by_key(self, key):
        return self.book


@pytest.fixture
def db(tmp_path):
    object.__setattr__(settings, "gsheet_id", "sheet123")
    path = tmp_path / "grades.db"
    conn = connect(path)
    conn.execute("INSERT INTO conversations (id, agent_name, brand, created_at, payload_json) "
                 "VALUES ('c1', 'Ada', 'King Billy', '2026-11-02T10:00:00+00:00', '{}')")
    conn.execute("INSERT INTO grades (conversation_id, agent_name, overall_score, summary, "
                 "graded_at, payload_json) VALUES ('c1', 'Ada', 83, 'Restored from the service "
                 "log after…', '2026-11-02T11:00:00+00:00', '{\"overall_result\": \"PASS\"}')")
    conn.commit()
    conn.close()
    yield path
    object.__setattr__(settings, "gsheet_id", "")


def test_grades_tab_has_ai_and_qa_columns(db):
    g = GradesStore(db)
    g.save_override("c1", 60, "missed KYC", "kate")
    g.close()
    book = FakeBook()
    assert sheets_export.export_grades(db, client=FakeClient(book)) == 1
    rows = book.tabs["Grades"].rows()
    assert rows[0] == sheets_export.GRADE_HEADER
    r = dict(zip(rows[0], rows[1]))
    assert r["Chat id"] == "c1" and r["AI score"] == 83 and r["AI result"] == "PASS"
    assert r["QA score"] == 60 and r["QA reason"] == "missed KYC" and r["QA by"] == "kate"
    assert r["Restored from log"] == "yes"
    status = dict((row[0], row[1]) for row in book.tabs["Status"].rows()[1:])
    assert status["Grades tab rewritten"]


def test_actions_tab_appends_only_new_journal_entries(db):
    book = FakeBook()
    g = GradesStore(db)
    g.save_override("c1", 60, "missed KYC", "kate")
    g.close()
    assert sheets_export.sync_actions(db, client=FakeClient(book)) == 1
    ws = book.tabs["QA actions"]
    assert ws.rows()[0] == sheets_export.ACTION_HEADER
    assert ws.appended[0][1:6] == ["grade.override", "c1", "kate", 60, "missed KYC"]

    assert sheets_export.sync_actions(db, client=FakeClient(book)) == 0      # nothing new
    g = GradesStore(db)
    g.save_override("c1", 70, "on second look", "daria")
    g.close()
    assert sheets_export.sync_actions(db, client=FakeClient(book)) == 1
    assert [r[3] for r in ws.appended] == ["kate", "daria"]


def test_a_failing_sheet_turns_the_badge_red(db):
    with pytest.raises(RuntimeError):
        sheets_export.sync_actions(db, client=FakeClient(FakeBook(fail=True)))
    problems = backup.health(db)["problems"]
    assert any("Google Sheet copy failing" in p for p in problems)
    # A later success clears it.
    sheets_export.sync_actions(db, client=FakeClient(FakeBook()))
    assert not any("Google Sheet" in p for p in backup.health(db)["problems"])


def test_disabled_without_a_sheet_id(db):
    object.__setattr__(settings, "gsheet_id", "")
    assert sheets_export.export_grades(db, client=FakeClient(FakeBook())) == 0
    assert sheets_export.status(db) == {"enabled": False}
