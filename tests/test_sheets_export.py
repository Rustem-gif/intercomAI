import gspread
import pytest

from intercom_summary import backup, sheets_export
from intercom_summary.settings import settings
from intercom_summary.storage.conversation_comments_store import ConversationCommentsStore
from intercom_summary.storage.db import connect
from intercom_summary.storage.grade_disputes_store import GradeDisputesStore
from intercom_summary.storage.grades_store import GradesStore


class FakeWS:
    _ids = iter(range(100, 10_000))

    def __init__(self, title):
        self.title, self.cells, self.id = title, {}, next(self._ids)

    def clear(self):
        self.cells = {}

    def resize(self, rows, cols):
        pass

    def update(self, range_name, values, value_input_option=None):
        start = int(range_name[1:])
        for i, row in enumerate(values):
            self.cells[start + i] = row

    def update_title(self, title):
        self.book.tabs[title] = self.book.tabs.pop(self.title)
        self.title = title

    def rows(self):
        return [self.cells[k] for k in sorted(self.cells)]


class FakeBook:
    def __init__(self, fail=False):
        self.tabs, self.fail, self.batches = {}, fail, []

    def worksheet(self, title):
        if self.fail:
            raise RuntimeError("403 The caller does not have permission")
        if title not in self.tabs:
            raise gspread.WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, title, rows, cols):
        ws = FakeWS(title)
        ws.book = self
        self.tabs[title] = ws
        return ws

    def del_worksheet(self, ws):
        del self.tabs[ws.title]

    def fetch_sheet_metadata(self, params=None):
        return {"sheets": [{"properties": {"sheetId": ws.id}} for ws in self.tabs.values()]}

    def batch_update(self, body):
        self.batches.append(body["requests"])


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


def test_one_row_per_chat_with_ai_grade_and_all_qa_work(db):
    g = GradesStore(db)
    g.save_override("c1", 60, "missed KYC", "kate")
    g.close()
    c = ConversationCommentsStore(db)
    c.add("c1", "kate", "first")
    c.add("c1", "daria", "coach the KYC step")
    c.close()
    d = GradeDisputesStore(db)
    d.create("c1", "Ada", "I did ask for documents", created_via="portal", created_by="Ada")
    d.close()

    book = FakeBook()
    assert sheets_export.export_grades(db, client=FakeClient(book)) == 1
    rows = book.tabs[sheets_export.TAB].rows()
    assert rows[0] == sheets_export.HEADER
    r = dict(zip(rows[0], rows[1]))
    assert r["Chat id"] == "c1" and r["AI score"] == 83 and r["AI result"] == "PASS"
    assert r["Created"] == "2026-11-02 12:00"                       # Kyiv time, not raw ISO
    assert r["QA score"] == 60 and r["QA reason"] == "missed KYC" and r["QA by"] == "kate"
    assert r["Comments"] == 2 and r["Last comment"] == "daria: coach the KYC step"
    assert r["Dispute"] == "open" and r["Last QA action"]
    assert r["Note"] == sheets_export.RESTORED_NOTE
    assert "QA actions" not in book.tabs                             # unified: no second tab


def test_every_write_formats_the_tab(db):
    book = FakeBook()
    sheets_export.export_grades(db, client=FakeClient(book))
    kinds = {next(iter(req)) for batch in book.batches for req in batch}
    assert {"addBanding", "setBasicFilter", "addConditionalFormatRule",
            "updateDimensionProperties", "repeatCell", "updateSheetProperties"} <= kinds
    status = {row[0]: row[1] for row in book.tabs["Status"].rows()[1:]}
    assert status["Graded chats"] == 1 and status["Re-scored by QA"] == 0
    assert status["Last updated (Kyiv)"]


def test_old_two_tab_layout_is_retired(db):
    book = FakeBook()
    book.add_worksheet("Grades", 1, 1)
    book.add_worksheet("QA actions", 1, 1)
    sheets_export.export_grades(db, client=FakeClient(book))
    assert set(book.tabs) == {sheets_export.TAB, "Status"}


def test_a_failing_sheet_turns_the_badge_red(db):
    with pytest.raises(RuntimeError):
        sheets_export.export_grades(db, client=FakeClient(FakeBook(fail=True)))
    problems = backup.health(db)["problems"]
    assert any("Google Sheet copy failing" in p for p in problems)
    sheets_export.export_grades(db, client=FakeClient(FakeBook()))      # a success clears it
    assert not any("Google Sheet" in p for p in backup.health(db)["problems"])


def test_disabled_without_a_sheet_id(db):
    object.__setattr__(settings, "gsheet_id", "")
    assert sheets_export.export_grades(db, client=FakeClient(FakeBook())) == 0
    assert sheets_export.status(db) == {"enabled": False}
