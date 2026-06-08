"""Reading and writing Obsidian markdown notes with YAML frontmatter."""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _dump_yaml(data: dict[str, Any]) -> str:
    try:
        import yaml  # type: ignore

        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    except ImportError:
        return _dump_yaml_minimal(data)


def _load_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except ImportError:
        return _load_yaml_minimal(text)


def _dump_yaml_minimal(data: dict[str, Any]) -> str:
    """Tiny YAML emitter for flat dicts and simple lists (no PyYAML needed)."""
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_scalar(item)}")
        else:
            lines.append(f"{key}: {_scalar(value)}")
    return "\n".join(lines)


def _scalar(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value)
    if s == "" or any(c in s for c in ":#") or s.strip() != s:
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _load_yaml_minimal(text: str) -> dict[str, Any]:
    """Tiny YAML reader matching :func:`_dump_yaml_minimal`'s output."""
    data: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if raw.lstrip().startswith("- ") and current_list_key:
            data[current_list_key].append(_unscalar(raw.strip()[2:]))
            continue
        if ":" in raw:
            key, _, val = raw.partition(":")
            key = key.strip()
            val = val.strip()
            if val == "":
                data[key] = []
                current_list_key = key
            else:
                data[key] = _unscalar(val)
                current_list_key = None
    return data


def _unscalar(s: str) -> Any:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1].replace('\\"', '"')
    if s == "true":
        return True
    if s == "false":
        return False
    return s


@dataclass
class Note:
    """An Obsidian markdown note: frontmatter dict + body text."""

    path: Path
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    @classmethod
    def load(cls, path: Path) -> "Note":
        path = Path(path)
        raw = path.read_text(encoding="utf-8")
        match = _FM_RE.match(raw)
        if match:
            fm = _load_yaml(match.group(1))
            body = raw[match.end():]
        else:
            fm = {}
            body = raw
        return cls(path=path, frontmatter=fm, body=body)

    def render(self) -> str:
        fm = _dump_yaml(self.frontmatter) if self.frontmatter else ""
        if fm:
            return f"---\n{fm}\n---\n\n{self.body.lstrip()}"
        return self.body

    def save(self) -> None:
        self.path.write_text(self.render(), encoding="utf-8")

    # -- convenience helpers -------------------------------------------------

    @property
    def status(self) -> str:
        return str(self.frontmatter.get("status", "idea"))

    @status.setter
    def status(self, value: str) -> None:
        self.frontmatter["status"] = value

    @property
    def title(self) -> str:
        if self.frontmatter.get("title"):
            return str(self.frontmatter["title"])
        for line in self.body.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return self.path.stem.replace("-", " ").title()

    def append_section(self, heading: str, content: str) -> None:
        block = f"\n\n## {heading}\n\n{content.strip()}\n"
        self.body = self.body.rstrip() + block

    def touch(self) -> None:
        self.frontmatter["updated"] = _dt.datetime.now().isoformat(timespec="seconds")
