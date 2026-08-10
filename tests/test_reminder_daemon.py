from assistant import reminder_daemon as daemon
from assistant import reminders


def test_ps_quote_wraps_in_single_quotes():
    assert daemon._ps_quote("Stand up") == "'Stand up'"


def test_ps_quote_escapes_single_quotes_by_doubling():
    assert daemon._ps_quote("it's time") == "'it''s time'"


def test_toast_script_embeds_title_and_message():
    script = daemon._toast_script("Reminder", "Stand up and stretch")
    assert "Reminder" in script
    assert "Stand up and stretch" in script


def test_toast_script_collapses_newlines_to_one_line():
    script = daemon._toast_script("Reminder", "line one\nline two")
    assert "line one line two" in script
    assert "\nline two" not in script


def test_toast_script_escapes_embedded_quotes():
    script = daemon._toast_script("it's", "say 'hi' to the team")
    # doubled single quotes keep the PowerShell string intact
    assert "it''s" in script
    assert "say ''hi'' to the team" in script


def test_check_once_fires_due_reminders_and_marks_them(monkeypatch):
    fired = []
    delivered = []
    monkeypatch.setattr(
        reminders, "due_reminders",
        lambda: [{"id": 1, "text": "Stand up"}, {"id": 2, "text": "Drink water"}],
    )
    monkeypatch.setattr(reminders, "mark_fired", lambda rid: fired.append(rid))
    monkeypatch.setattr(daemon, "show_toast",
                        lambda title, message: delivered.append(message) or True)

    assert daemon.check_once() == 2
    assert fired == [1, 2]
    assert delivered == ["Stand up", "Drink water"]


def test_check_once_marks_fired_even_when_toast_fails(monkeypatch):
    fired = []
    monkeypatch.setattr(reminders, "due_reminders",
                        lambda: [{"id": 7, "text": "Stand up"}])
    monkeypatch.setattr(reminders, "mark_fired", lambda rid: fired.append(rid))
    monkeypatch.setattr(daemon, "show_toast", lambda title, message: False)

    assert daemon.check_once() == 1
    assert fired == [7]  # no double-fire loop on a broken toast path


def test_check_once_no_due_reminders_fires_nothing(monkeypatch):
    monkeypatch.setattr(reminders, "due_reminders", lambda: [])
    assert daemon.check_once() == 0


def test_show_toast_uses_powershell_and_reports_success(monkeypatch):
    captured = {}

    class _FakeResult:
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeResult()

    monkeypatch.setattr(daemon.subprocess, "run", fake_run)
    assert daemon.show_toast("Title", "Message") is True
    assert captured["cmd"][0] == "powershell"
    assert "Title" in captured["cmd"][-1] and "Message" in captured["cmd"][-1]


def test_show_toast_falls_back_to_msg_popup(monkeypatch):
    calls = []

    class _FakeResult:
        returncode = 1

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeResult()

    monkeypatch.setattr(daemon.subprocess, "run", fake_run)
    assert daemon.show_toast("Title", "Message") is True
    assert calls[0][0] == "powershell"
    assert calls[1][0] == "msg"


def test_show_toast_reports_failure_when_all_paths_fail(monkeypatch):
    class _FakeResult:
        returncode = 1

    def fake_run(cmd, **kwargs):
        raise RuntimeError("no powershell")

    monkeypatch.setattr(daemon.subprocess, "run", fake_run)
    assert daemon.show_toast("Title", "Message") is False
