"""Command line interface for Obsidian Jarvis."""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
import time
from pathlib import Path

from .config import load_config, Config
from .pipeline import Pipeline, STAGES


def _now() -> str:
    return _dt.datetime.now().strftime("%H:%M:%S")


def _log(msg: str) -> None:
    print(f"[{_now()}] {msg}", flush=True)


def _build_pipeline(args) -> Pipeline:
    overrides = {
        "inbox": getattr(args, "inbox", None),
        "projects": getattr(args, "projects", None),
        "llm_backend": getattr(args, "backend", None),
        "llm_model": getattr(args, "model", None),
        "poll_interval": getattr(args, "interval", None),
    }
    config = load_config(
        vault=getattr(args, "vault", None),
        config_file=getattr(args, "config", None),
        overrides=overrides,
    )
    return Pipeline(config, logger=_log)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_init(args) -> int:
    config = load_config(vault=args.vault, config_file=args.config)
    config.ensure_dirs()
    example = config.inbox_path / "example-idea.md"
    if not example.exists():
        example.write_text(_EXAMPLE_IDEA, encoding="utf-8")
    _log(f"Initialized vault at {config.vault_path}")
    _log(f"  inbox:    {config.inbox_path}")
    _log(f"  projects: {config.projects_path}")
    _log(f"  example:  {example}")
    return 0


def cmd_capture(args) -> int:
    config = load_config(vault=args.vault, config_file=args.config)
    config.ensure_dirs()
    idea = args.text or sys.stdin.read()
    idea = idea.strip()
    if not idea:
        _log("Nothing to capture (empty idea).")
        return 1
    lines = idea.splitlines()
    title = lines[0].lstrip("# ").strip()[:60] or "idea"
    # Avoid duplicating a heading the user already wrote as the first line.
    body = "\n".join(lines[1:]).strip() if lines[0].lstrip().startswith("#") else idea
    slug = _slugify(title)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = config.inbox_path / f"{stamp}-{slug}.md"
    created = _dt.datetime.now().isoformat(timespec="seconds")
    path.write_text(
        f"---\nstatus: idea\ncreated: {created}\n---\n\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )
    _log(f"Captured idea -> {path}")
    if args.run:
        return _run_once(args)
    return 0


def cmd_run(args) -> int:
    return _run_once(args)


def _run_once(args) -> int:
    pipeline = _build_pipeline(args)
    _log(f"Scanning inbox: {pipeline.config.inbox_path}")
    results = pipeline.run_once()
    if not results:
        _log("No pending ideas to process.")
    else:
        _log(f"Done. {len(results)} stage transition(s).")
        for r in results:
            _log(f"  {r.note.path.name}: {r.from_status} -> {r.to_status}")
    return 0


def cmd_watch(args) -> int:
    pipeline = _build_pipeline(args)
    interval = pipeline.config.poll_interval
    _log(f"Watching {pipeline.config.inbox_path} every {interval}s. Ctrl-C to stop.")
    _log("Jarvis is awake. Drop ideas in the inbox and go to sleep. 😴")
    try:
        while True:
            results = pipeline.run_once()
            if results:
                _log(f"Processed {len(results)} stage transition(s).")
            time.sleep(interval)
    except KeyboardInterrupt:
        _log("Stopping watcher. Good morning. ☀️")
    return 0


def cmd_status(args) -> int:
    config = load_config(vault=args.vault, config_file=args.config)
    config.ensure_dirs()
    from .note import Note

    counts = {s: 0 for s in STAGES}
    rows = []
    for path in sorted(config.inbox_path.glob("*.md")):
        note = Note.load(path)
        st = note.status if note.status in counts else "idea"
        counts[st] += 1
        rows.append((st, note.title, path.name))

    _log(f"Vault: {config.vault_path}")
    print("\nStatus summary:")
    for s in STAGES:
        print(f"  {s:<10} {counts[s]}")
    if rows:
        print("\nIdeas:")
        for st, title, name in rows:
            print(f"  [{st:<8}] {title}  ({name})")
    return 0


# --------------------------------------------------------------------------- #

def _slugify(text: str) -> str:
    import re

    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "idea")[:48]


_EXAMPLE_IDEA = """---
status: idea
created: 2026-01-01T03:00:00
---

# Voice-to-task inbox

3am idea: a tiny tool where I ramble a voice memo and it gets transcribed,
split into individual tasks, tagged by project, and dropped into my todo list
automatically. I have way more ideas than time to type them out.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Turn raw Obsidian idea notes into finished projects, automatically.",
    )
    parser.add_argument("--vault", help="Path to the Obsidian vault.")
    parser.add_argument("--config", help="Path to a YAML/JSON config file.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create vault folders and an example idea.")
    p_init.set_defaults(func=cmd_init)

    p_cap = sub.add_parser("capture", help="Capture a new idea into the inbox.")
    p_cap.add_argument("text", nargs="?", help="Idea text (or pipe via stdin).")
    p_cap.add_argument("--run", action="store_true", help="Process it immediately.")
    p_cap.set_defaults(func=cmd_capture)

    p_run = sub.add_parser("run", help="Process all pending ideas once and exit.")
    _add_run_opts(p_run)
    p_run.set_defaults(func=cmd_run)

    p_watch = sub.add_parser("watch", help="Continuously process ideas (run overnight).")
    _add_run_opts(p_watch)
    p_watch.add_argument("--interval", type=int, help="Seconds between scans.")
    p_watch.set_defaults(func=cmd_watch)

    p_status = sub.add_parser("status", help="Show pipeline status for all ideas.")
    p_status.set_defaults(func=cmd_status)

    return parser


def _add_run_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--inbox", help="Inbox folder name within the vault.")
    p.add_argument("--projects", help="Projects folder name within the vault.")
    p.add_argument("--backend", help="LLM backend: offline | openai")
    p.add_argument("--model", help="LLM model name.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, RuntimeError) as exc:
        _log(f"Error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
