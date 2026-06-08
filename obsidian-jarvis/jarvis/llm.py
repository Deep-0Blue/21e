"""LLM client.

Two backends are supported:

* ``offline`` (default) - a deterministic, dependency-free "engine" that produces
  structured, useful output from the idea text alone. It needs no API key or
  network, so the whole pipeline runs anywhere, including CI.
* ``openai`` - any OpenAI-compatible chat-completions endpoint (OpenAI, Azure,
  Ollama, LM Studio, OpenRouter, ...) selected via the config's ``llm_base_url``.
"""

from __future__ import annotations

import json
import re
import textwrap
import urllib.error
import urllib.request
from typing import Optional


class LLM:
    def __init__(self, config) -> None:
        self.config = config
        self.backend = (config.llm_backend or "offline").lower()

    def complete(self, system: str, user: str) -> str:
        if self.backend == "offline":
            return _OfflineEngine().respond(system, user)
        if self.backend in ("openai", "openai-compatible", "ollama"):
            return self._openai_complete(system, user)
        raise ValueError(f"Unknown llm_backend: {self.backend!r}")

    def complete_json(self, system: str, user: str) -> dict:
        raw = self.complete(system + "\n\nRespond with valid JSON only.", user)
        return _extract_json(raw)

    def _openai_complete(self, system: str, user: str) -> str:  # pragma: no cover
        if not self.config.llm_api_key:
            raise RuntimeError(
                "llm_backend is 'openai' but no API key is set. "
                "Set JARVIS_LLM_API_KEY / OPENAI_API_KEY, or use the offline backend."
            )
        url = self.config.llm_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.config.llm_model,
            "temperature": self.config.llm_temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.llm_api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc
        return body["choices"][0]["message"]["content"]


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start : end + 1])
        raise


# --------------------------------------------------------------------------- #
# Offline engine: deterministic structured output derived from the idea text.
# --------------------------------------------------------------------------- #

_STOP = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "with",
    "that", "this", "it", "is", "are", "be", "as", "at", "by", "from", "i", "my",
    "me", "we", "you", "your", "so", "into", "all", "way", "app", "tool", "idea",
    "build", "make", "want", "need", "should", "could", "would", "can", "will",
}


class _OfflineEngine:
    """Heuristic responder that keys off markers placed in the user prompt."""

    def respond(self, system: str, user: str) -> str:
        if "STAGE:REFINE" in user:
            return self._refine(user)
        if "STAGE:PLAN" in user:
            return self._plan(user)
        if "STAGE:BUILD" in user:
            return self._build(user)
        if "STAGE:SHIP" in user:
            return self._ship(user)
        return _strip_marker(user)

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    def _idea(user: str) -> str:
        return _strip_marker(user).strip()

    @staticmethod
    def _keywords(text: str, limit: int = 8) -> list[str]:
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9']+", text.lower())
        seen: list[str] = []
        for w in words:
            if w in _STOP or len(w) < 3:
                continue
            if w not in seen:
                seen.append(w)
            if len(seen) >= limit:
                break
        return seen

    @staticmethod
    def _slug(text: str) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        return (s or "project")[:48]

    @staticmethod
    def _title(text: str) -> str:
        first = text.strip().splitlines()[0] if text.strip() else "Untitled Idea"
        first = re.sub(r"^#+\s*", "", first).strip()
        return first[:80] if first else "Untitled Idea"

    # -- stages ------------------------------------------------------------- #

    def _refine(self, user: str) -> str:
        idea = self._idea(user)
        title = self._title(idea)
        kws = self._keywords(idea)
        problem = (
            f"People dealing with \"{kws[0]}\"" if kws else "The target user"
        ) + " currently lack a focused, low-friction way to get this done."
        users = ", ".join(kws[:3]) or "early adopters"
        features = [
            f"Capture and organize {kws[0] if kws else 'inputs'} quickly",
            f"Automate the repetitive part of {kws[1] if len(kws) > 1 else 'the workflow'}",
            "Provide a clear status/overview dashboard",
            "Export or share the result",
        ]
        data = {
            "title": title,
            "summary": textwrap.shorten(idea.replace("\n", " "), 220, placeholder="..."),
            "problem": problem,
            "audience": users,
            "value_proposition": f"Turn a raw {title.lower()} idea into a working result with minimal effort.",
            "key_features": features,
            "open_questions": [
                "Who is the primary user and what is their #1 pain?",
                "What is the smallest version that is still useful?",
                "How will success be measured?",
            ],
            "tags": kws[:5],
            "slug": self._slug(title),
        }
        return json.dumps(data, indent=2)

    def _plan(self, user: str) -> str:
        idea = self._idea(user)
        title = self._title(idea)
        data = {
            "name": title,
            "slug": self._slug(title),
            "stack": ["Python 3", "Standard library", "pytest"],
            "milestones": [
                {"name": "Scaffold", "tasks": ["Create project layout", "Add README and config"]},
                {"name": "Core", "tasks": ["Implement core module", "Define data model"]},
                {"name": "Interface", "tasks": ["Add CLI entrypoint", "Wire core to CLI"]},
                {"name": "Quality", "tasks": ["Write smoke tests", "Document usage"]},
            ],
            "risks": ["Scope creep", "Unclear primary user"],
            "definition_of_done": [
                "Project runs end-to-end with one command",
                "README explains setup and usage",
                "A basic test passes",
            ],
        }
        return json.dumps(data, indent=2)

    def _build(self, user: str) -> str:
        idea = self._idea(user)
        title = self._title(idea)
        slug = self._slug(title)
        module = re.sub(r"[^a-z0-9_]+", "_", slug.replace("-", "_")) or "app"
        summary = textwrap.shorten(idea.replace("\n", " "), 140, placeholder="...")

        readme = f"""# {title}

> {summary}

Generated by **Obsidian Jarvis** from a raw idea note.

## Run

```bash
python -m {module}
```

## Test

```bash
python -m pytest
```
"""
        main_py = f'''"""{title} - generated starter implementation."""

from __future__ import annotations


def run() -> str:
    """Entry point. Replace with the real {title} logic."""
    return "Hello from {title}!"


if __name__ == "__main__":
    print(run())
'''
        init_py = f'"""{title} package."""\n\nfrom .main import run\n\n__all__ = ["run"]\n'
        main_module_py = f"from .main import run\n\nif __name__ == \"__main__\":\n    print(run())\n"
        test_py = f'''from {module}.main import run


def test_run_returns_greeting():
    assert "{title}" in run()
'''
        files = {
            "README.md": readme,
            f"{module}/__init__.py": init_py,
            f"{module}/__main__.py": main_module_py,
            f"{module}/main.py": main_py,
            f"tests/test_smoke.py": test_py,
            "requirements.txt": "# Add runtime dependencies here.\n",
        }
        return json.dumps({"slug": slug, "module": module, "files": files}, indent=2)

    def _ship(self, user: str) -> str:
        idea = self._idea(user)
        title = self._title(idea)
        data = {
            "release_notes": f"Initial release of {title}, scaffolded from an idea note.",
            "next_steps": [
                "Replace generated stubs with real logic",
                "Add the first real feature from the plan",
                "Set up CI and publish",
            ],
            "announcement": f"Shipped a first cut of {title}. Built overnight from a single idea note.",
        }
        return json.dumps(data, indent=2)


def _strip_marker(text: str) -> str:
    return re.sub(r"STAGE:[A-Z]+\n?", "", text)
