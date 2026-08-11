"""Tests for the dependency-free vault reader (assistant/vault.py).

No Ollama, no embeddings, no network — vault.py reads files and git only,
so these run fast and offline. The vault directory is monkeypatched to a
tmp_path so tests never touch the real E:\\Local\\brain.
"""
import pytest

from assistant import vault


@pytest.fixture
def fake_vault(tmp_path, monkeypatch):
    """A miniature vault: two real notes plus the folders vault.py expects."""
    (tmp_path / "notes").mkdir()
    (tmp_path / "templates").mkdir()
    (tmp_path / "notes" / "Alpha note.md").write_text(
        "---\ntags: [x]\n---\n\n# Alpha note\n\nBody text linking to [[Beta note]].\n",
        encoding="utf-8",
    )
    (tmp_path / "notes" / "Beta note.md").write_text(
        "# Beta note\n\nPoints back at [[Alpha note]].\n", encoding="utf-8",
    )
    # templates/ is in SKIP_DIRS and must never be listed
    (tmp_path / "templates" / "note.md").write_text("# Template\n", encoding="utf-8")
    monkeypatch.setattr(vault, "vault_dir", lambda: tmp_path)
    return tmp_path


def test_list_notes_skips_template_dir(fake_vault):
    names = {n["name"] for n in vault.list_notes()}
    assert names == {"Alpha note", "Beta note"}


def test_list_notes_extracts_heading_and_links(fake_vault):
    alpha = next(n for n in vault.list_notes() if n["name"] == "Alpha note")
    assert alpha["heading"] == "Alpha note"
    assert alpha["links"] == ["Beta note"]
    assert alpha["folder"] == "notes"


def test_read_note_returns_body(fake_vault):
    body = vault.read_note("notes/Beta note.md")
    assert body is not None and "Points back at" in body


def test_read_note_missing_returns_none(fake_vault):
    assert vault.read_note("notes/Nope.md") is None


def test_search_notes_finds_body_text(fake_vault):
    hits = vault.search_notes("points back")
    assert [h["name"] for h in hits] == ["Beta note"]


def test_search_notes_is_case_insensitive(fake_vault):
    assert vault.search_notes("POINTS BACK")


# ---------- wiki-link parsing ----------

def test_wikilink_spanning_a_line_break_resolves_to_one_target(fake_vault):
    """A [[link]] wrapped across a newline must normalize to the same target
    as the inline form. Before this was fixed the captured name kept the
    newline, so it matched no note and silently became a phantom
    "unresolved" node in the link graph (measured: 2 of 17 vault notes hit
    this). Obsidian itself collapses the whitespace."""
    (fake_vault / "notes" / "Wrapped.md").write_text(
        "# Wrapped\n\nSee [[Alpha\nnote]] for details.\n", encoding="utf-8",
    )
    wrapped = next(n for n in vault.list_notes() if n["name"] == "Wrapped")
    assert wrapped["links"] == ["Alpha note"]


def test_wikilink_collapses_runs_of_internal_whitespace(fake_vault):
    (fake_vault / "notes" / "Spaced.md").write_text(
        "# Spaced\n\n[[Alpha    note]]\n", encoding="utf-8",
    )
    spaced = next(n for n in vault.list_notes() if n["name"] == "Spaced")
    assert spaced["links"] == ["Alpha note"]


def test_wikilink_strips_alias_and_heading_anchor(fake_vault):
    (fake_vault / "notes" / "Fancy.md").write_text(
        "# Fancy\n\n[[Alpha note#Some heading|display text]]\n", encoding="utf-8",
    )
    fancy = next(n for n in vault.list_notes() if n["name"] == "Fancy")
    assert fancy["links"] == ["Alpha note"]


def test_wikilinks_are_deduplicated(fake_vault):
    (fake_vault / "notes" / "Dupe.md").write_text(
        "# Dupe\n\n[[Alpha note]] and again [[Alpha note]] and [[Alpha\nnote]]\n",
        encoding="utf-8",
    )
    dupe = next(n for n in vault.list_notes() if n["name"] == "Dupe")
    assert dupe["links"] == ["Alpha note"]
