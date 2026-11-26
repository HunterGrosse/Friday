"""
FRIDAY's long-term memory (Step 2).

The model forgets everything when the program closes. Long-term memory is
just files on disk that we read at startup and paste into the system prompt.

Two kinds of memory live in the memory/ folder (git-ignored, so your personal
stuff never gets committed):

  facts.json     Things you explicitly asked her to remember.
                 "remember that my sister's birthday is May 3rd"

  journal.jsonl  A short recap she writes at the end of each conversation,
                 one JSON object per line. The most recent few are loaded at
                 startup so she knows what you talked about last time.
"""

import json
from datetime import datetime
from pathlib import Path

MEMORY_DIR = Path(__file__).parent / "memory"
FACTS_FILE = MEMORY_DIR / "facts.json"
JOURNAL_FILE = MEMORY_DIR / "journal.jsonl"

# How many past conversation recaps to load into her head at startup.
RECAPS_TO_LOAD = 5


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------
def load_facts() -> list[dict]:
    if not FACTS_FILE.exists():
        return []
    try:
        return json.loads(FACTS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"[!] {FACTS_FILE} is corrupted; starting with no facts.")
        return []


def save_facts(facts: list[dict]) -> None:
    MEMORY_DIR.mkdir(exist_ok=True)
    FACTS_FILE.write_text(json.dumps(facts, indent=2), encoding="utf-8")


def add_fact(text: str) -> None:
    facts = load_facts()
    facts.append({"fact": text, "saved": _now()})
    save_facts(facts)


def forget_fact(number: int) -> str | None:
    """Remove fact #number (1-based, as shown by list). Returns its text."""
    facts = load_facts()
    if not 1 <= number <= len(facts):
        return None
    removed = facts.pop(number - 1)
    save_facts(facts)
    return removed["fact"]


# ---------------------------------------------------------------------------
# Journal (conversation recaps)
# ---------------------------------------------------------------------------
def load_recent_recaps(limit: int = RECAPS_TO_LOAD) -> list[dict]:
    if not JOURNAL_FILE.exists():
        return []
    entries = []
    for line in JOURNAL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # skip a damaged line rather than crash
    return entries[-limit:]


def add_recap(text: str) -> None:
    MEMORY_DIR.mkdir(exist_ok=True)
    with JOURNAL_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"date": _now(), "recap": text}) + "\n")


# ---------------------------------------------------------------------------
# Turning memory into prompt text
# ---------------------------------------------------------------------------
def memory_prompt_section(user_name: str) -> str:
    """Build the part of the system prompt that holds her memories."""
    facts = load_facts()
    recaps = load_recent_recaps()

    lines = [f"\nToday is {datetime.now().strftime('%A, %B %d, %Y')}.\n"]

    lines.append(f"Things {user_name} asked you to remember:")
    if facts:
        lines += [f"- {f['fact']} (saved {f['saved']})" for f in facts]
    else:
        lines.append("- Nothing yet.")

    lines.append("\nYour notes from recent conversations (oldest first):")
    if recaps:
        lines += [f"- {r['date']}: {r['recap']}" for r in recaps]
    else:
        lines.append(f"- None. This is your first conversation with {user_name}.")

    lines.append(
        "\nUse these memories naturally, the way a friend would. Don't recite "
        "them or announce that you remember things unless it fits."
    )
    return "\n".join(lines)
