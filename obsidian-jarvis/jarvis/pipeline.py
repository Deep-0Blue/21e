"""The idea -> finished project pipeline.

Each idea note moves through an ordered set of stages. The note's frontmatter
``status`` records where it is, so the pipeline is resumable and idempotent: a
crash or restart simply picks up from the last completed stage.

    idea -> refined -> planned -> built -> shipped
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Config
from .llm import LLM
from .note import Note


# Ordered status flow.
STAGES = ["idea", "refined", "planned", "built", "shipped"]
NEXT = {STAGES[i]: STAGES[i + 1] for i in range(len(STAGES) - 1)}


@dataclass
class StageResult:
    note: Note
    from_status: str
    to_status: str
    message: str


class Pipeline:
    def __init__(self, config: Config, llm: LLM | None = None, logger: Callable[[str], None] | None = None):
        self.config = config
        self.llm = llm or LLM(config)
        self.log = logger or (lambda msg: None)
        config.ensure_dirs()

    # -- discovery ---------------------------------------------------------- #

    def pending_notes(self) -> list[Note]:
        """Idea notes that have not finished the pipeline yet."""
        notes: list[Note] = []
        for path in sorted(self.config.inbox_path.glob("*.md")):
            note = Note.load(path)
            if note.frontmatter.get("jarvis_ignore"):
                continue
            if note.status != "shipped":
                notes.append(note)
        return notes

    # -- driving ------------------------------------------------------------ #

    def advance(self, note: Note) -> StageResult | None:
        """Run the single next stage for a note."""
        status = note.status if note.status in STAGES else "idea"
        handler = {
            "idea": self._stage_refine,
            "refined": self._stage_plan,
            "planned": self._stage_build,
            "built": self._stage_ship,
        }.get(status)
        if handler is None:
            return None

        self.log(f"  -> {status} stage for {note.path.name}")
        result = handler(note)
        note.touch()
        note.save()
        return result

    def run_note(self, note: Note, max_stages: int = 10) -> list[StageResult]:
        """Run a note all the way to 'shipped' (or until no stage applies)."""
        results: list[StageResult] = []
        for _ in range(max_stages):
            result = self.advance(note)
            if result is None:
                break
            results.append(result)
            if note.status == "shipped":
                break
        return results

    def run_once(self) -> list[StageResult]:
        """Process every pending idea note to completion. One watcher 'tick'."""
        results: list[StageResult] = []
        notes = self.pending_notes()[: self.config.max_per_tick]
        for note in notes:
            self.log(f"Processing: {note.title} ({note.path.name})")
            results.extend(self.run_note(note))
        return results

    # -- stages ------------------------------------------------------------- #

    def _idea_text(self, note: Note) -> str:
        return f"# {note.title}\n\n{note.body}".strip()

    def _stage_refine(self, note: Note) -> StageResult:
        data = self.llm.complete_json(
            "You are a product strategist. Refine a raw idea into a crisp spec.",
            f"STAGE:REFINE\n{self._idea_text(note)}",
        )
        lines = [f"- **Problem:** {data.get('problem', '')}",
                 f"- **Audience:** {data.get('audience', '')}",
                 f"- **Value:** {data.get('value_proposition', '')}"]
        features = "\n".join(f"- {f}" for f in data.get("key_features", []))
        questions = "\n".join(f"- {q}" for q in data.get("open_questions", []))
        note.append_section("Refined Spec", "\n".join(lines))
        if features:
            note.append_section("Key Features", features)
        if questions:
            note.append_section("Open Questions", questions)
        if data.get("title"):
            note.frontmatter.setdefault("title", data["title"])
        if data.get("slug"):
            note.frontmatter["slug"] = data["slug"]
        if data.get("tags"):
            note.frontmatter["tags"] = data["tags"]
        note.status = "refined"
        return StageResult(note, "idea", "refined", "Refined raw idea into a spec")

    def _stage_plan(self, note: Note) -> StageResult:
        plan = self.llm.complete_json(
            "You are a tech lead. Produce a build plan with milestones and tasks.",
            f"STAGE:PLAN\n{self._idea_text(note)}",
        )
        md = [f"**Stack:** {', '.join(plan.get('stack', []))}", ""]
        for ms in plan.get("milestones", []):
            md.append(f"### {ms.get('name', 'Milestone')}")
            for task in ms.get("tasks", []):
                md.append(f"- [ ] {task}")
            md.append("")
        dod = plan.get("definition_of_done", [])
        if dod:
            md.append("**Definition of Done:**")
            md.extend(f"- {d}" for d in dod)
        note.append_section("Build Plan", "\n".join(md).strip())
        note.frontmatter["plan"] = json.dumps(plan)
        note.status = "planned"
        return StageResult(note, "refined", "planned", "Created build plan")

    def _stage_build(self, note: Note) -> StageResult:
        result = self.llm.complete_json(
            "You are a senior engineer. Generate a runnable starter project.",
            f"STAGE:BUILD\n{self._idea_text(note)}",
        )
        slug = result.get("slug") or note.frontmatter.get("slug") or _safe_slug(note.title)
        project_dir = self.config.projects_path / slug
        files = result.get("files", {})
        written = self._write_project(project_dir, files)

        rel = project_dir.relative_to(self.config.vault_path)
        listing = "\n".join(f"- `{f}`" for f in sorted(files))
        note.append_section(
            "Generated Project",
            f"Project scaffolded at `{rel}` ({written} files):\n\n{listing}",
        )
        note.frontmatter["project_path"] = str(rel)
        note.frontmatter["slug"] = slug
        note.status = "built"
        return StageResult(note, "planned", "built", f"Built project at {rel}")

    def _stage_ship(self, note: Note) -> StageResult:
        info = self.llm.complete_json(
            "You are a release manager. Write release notes and next steps.",
            f"STAGE:SHIP\n{self._idea_text(note)}",
        )
        project_rel = note.frontmatter.get("project_path")
        if project_rel:
            project_dir = self.config.vault_path / project_rel
            self._write_release_files(project_dir, note, info)

        nxt = "\n".join(f"- {s}" for s in info.get("next_steps", []))
        body = f"**Release notes:** {info.get('release_notes', '')}\n\n**Next steps:**\n{nxt}"
        note.append_section("Shipped", body)
        note.frontmatter["shipped_at"] = _dt.datetime.now().isoformat(timespec="seconds")
        note.status = "shipped"
        return StageResult(note, "built", "shipped", "Shipped project")

    # -- io helpers --------------------------------------------------------- #

    @staticmethod
    def _write_project(project_dir: Path, files: dict[str, str]) -> int:
        count = 0
        for rel, content in files.items():
            target = project_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            count += 1
        return count

    @staticmethod
    def _write_release_files(project_dir: Path, note: Note, info: dict) -> None:
        project_dir.mkdir(parents=True, exist_ok=True)
        changelog = project_dir / "CHANGELOG.md"
        entry = (
            f"## v0.1.0 - {_dt.date.today().isoformat()}\n\n"
            f"{info.get('release_notes', 'Initial release.')}\n\n"
            + "".join(f"- {s}\n" for s in info.get("next_steps", []))
        )
        existing = changelog.read_text(encoding="utf-8") if changelog.exists() else "# Changelog\n\n"
        changelog.write_text(existing + entry + "\n", encoding="utf-8")


def _safe_slug(text: str) -> str:
    import re

    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "project")[:48]
