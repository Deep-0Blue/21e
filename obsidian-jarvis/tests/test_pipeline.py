"""End-to-end tests for the offline pipeline."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.config import Config
from jarvis.note import Note
from jarvis.pipeline import Pipeline, STAGES


def _make_vault(tmp_path: Path) -> Config:
    config = Config(vault=tmp_path, llm_backend="offline")
    config.ensure_dirs()
    return config


def _write_idea(config: Config, name: str, text: str) -> Path:
    path = config.inbox_path / name
    path.write_text(f"---\nstatus: idea\n---\n\n{text}\n", encoding="utf-8")
    return path


def test_full_pipeline_ships_a_project(tmp_path):
    config = _make_vault(tmp_path)
    idea_path = _write_idea(
        config,
        "habit-tracker.md",
        "# Habit tracker\n\nA tiny CLI to log daily habits and show streaks.",
    )

    pipeline = Pipeline(config)
    note = Note.load(idea_path)
    results = pipeline.run_note(note)

    assert note.status == "shipped"
    statuses = [r.to_status for r in results]
    assert statuses == ["refined", "planned", "built", "shipped"]

    # A project folder was generated with runnable files.
    project_rel = note.frontmatter["project_path"]
    project_dir = config.vault_path / project_rel
    assert (project_dir / "README.md").exists()
    assert (project_dir / "CHANGELOG.md").exists()
    py_files = list(project_dir.rglob("*.py"))
    assert py_files, "expected generated python files"


def test_run_once_is_idempotent(tmp_path):
    config = _make_vault(tmp_path)
    _write_idea(config, "a.md", "# Idea A\n\nDo something useful.")

    pipeline = Pipeline(config)
    first = pipeline.run_once()
    assert first, "first run should make progress"

    # Everything is shipped now; a second run does nothing.
    second = pipeline.run_once()
    assert second == []

    note = Note.load(config.inbox_path / "a.md")
    assert note.status == "shipped"


def test_resumes_from_middle_stage(tmp_path):
    config = _make_vault(tmp_path)
    path = config.inbox_path / "mid.md"
    path.write_text("---\nstatus: refined\n---\n\n# Mid\n\nResume me.\n", encoding="utf-8")

    pipeline = Pipeline(config)
    note = Note.load(path)
    results = pipeline.run_note(note)

    assert [r.from_status for r in results][0] == "refined"
    assert note.status == "shipped"


def test_stage_order_constant():
    assert STAGES == ["idea", "refined", "planned", "built", "shipped"]
