"""
Anonymizer for Zulip message content.

PURPOSE
-------
This module replaces real Zulip user identities with stable, opaque aliases
(e.g. {User1}, {User2}) before message content is returned from the MCP server.
The goal is to ensure that real names are never sent to Claude (or any other LLM
API) when the anonymize flag is enabled.

ALIAS FORMAT
------------
Aliases are formatted as {User1}, {User2}, {User3}, etc. (1-indexed, no zero-padding).
This format was chosen because it is:
  - Trivially findable with a regex: r'\\{User\\d+\\}'
  - Easy to replace in bulk with a simple str.replace or dict-driven substitution
  - Clearly synthetic (won't collide with real names)

DE-ANONYMIZATION
----------------
The mapping is persisted to a JSON file (default: ~/.config/zulip_mcp/anonymizer_map.json).
The file maps normalized_name -> alias, e.g.:
  {
    "Lissa Hyacinth": "{User1}",
    "Alex N Hornstein": "{User2}"
  }

To de-anonymize a piece of text:
  1. Load the JSON file.
  2. Invert the dict: alias -> real_name.
  3. For each alias in the inverted dict, replace occurrences in the text.

The mapping is append-only and persists across server restarts. A given real
person will always receive the same alias for the lifetime of the map file.
Delete or reset the file to start fresh.

NAME NORMALIZATION
------------------
Zulip sender names and @-mentions often include pronouns and batch codes:
  "Lissa Hyacinth (they) (SP1'26)"
  @**Alex N Hornstein (he) (SP1'26)**

We normalize by stripping any trailing parenthesized groups so that partial
mentions (e.g. "@**Lissa Hyacinth**") resolve to the same alias as the full
sender name. Normalization is:
  1. Strip trailing " (anything)" groups (greedy, handles multiple groups)
  2. Strip leading/trailing whitespace

Examples:
  "Lissa Hyacinth (they) (SP1'26)"  -> "Lissa Hyacinth"
  "Alex N Hornstein (he) (SP1'26)"  -> "Alex N Hornstein"
  "Lissa Hyacinth"                  -> "Lissa Hyacinth"  (already normalized)

MENTION PATTERNS HANDLED
-------------------------
Zulip raw markup (apply_markdown=False) uses these mention formats:
  @**Full Name**                    # plain mention
  @**Full Name (pronouns) (batch)** # mention with metadata
  @**Full Name|user_id**            # mention with user ID suffix
  @**Full Name (pronouns)|user_id** # mention with metadata and user ID

Group mentions (@**all**, @**here**, @**everyone**) are left unchanged.
"""

import json
import re
from pathlib import Path

# Default path for the persistent alias map.
DEFAULT_MAP_PATH = Path.home() / ".config" / "zulip_mcp" / "anonymizer_map.json"

# Matches trailing parenthesized groups like " (he)" or " (SP1'26)".
_TRAILING_PARENS_RE = re.compile(r"\s*\(.*?\)\s*$")

# Matches Zulip @-mention markup: @**Name** or @**Name|user_id**
# Capture group 1 = everything inside the ** delimiters.
_MENTION_RE = re.compile(r"@\*\*([^*]+)\*\*")

# Group mentions that should be left unchanged.
_GROUP_MENTIONS = {"all", "here", "everyone"}


def _normalize_name(name: str) -> str:
    """
    Strip trailing parenthesized groups from a Zulip display name.

    "Lissa Hyacinth (they) (SP1'26)" -> "Lissa Hyacinth"
    "Alex N Hornstein (he)"          -> "Alex N Hornstein"
    "Plain Name"                     -> "Plain Name"
    """
    result = name.strip()
    # Remove parenthesized suffixes repeatedly until none remain.
    while True:
        stripped = _TRAILING_PARENS_RE.sub("", result).strip()
        if stripped == result:
            break
        result = stripped
    return result


def _mention_name(raw: str) -> str:
    """
    Extract the display-name portion from a raw mention string.

    The raw string is everything inside @**...**:
      "Alex N Hornstein (he) (SP1'26)"  -> "Alex N Hornstein (he) (SP1'26)"
      "Alex N Hornstein|12345"          -> "Alex N Hornstein"
      "Alex N Hornstein (he)|12345"     -> "Alex N Hornstein (he)"

    After this, the result is passed through _normalize_name.
    """
    # Strip optional |user_id suffix before normalizing.
    name = raw.split("|")[0]
    return _normalize_name(name)


class Anonymizer:
    """
    Stateful name anonymizer that persists its mapping to disk.

    Usage
    -----
    anonymizer = Anonymizer()                      # loads existing map from disk
    anon_sender, anon_content = anonymizer.anonymize_message(sender, content)

    The same Anonymizer instance should be reused across all tool calls in a
    server session so that aliases are consistent (same person = same alias).

    Thread safety: not thread-safe; the MCP server is single-threaded so this
    is fine.
    """

    def __init__(self, map_path: Path = DEFAULT_MAP_PATH):
        """
        Load the existing alias map from disk, or start with an empty map.

        Parameters
        ----------
        map_path:
            Path to the JSON file storing the normalized_name -> alias mapping.
            Created (with parent directories) on first write.
        """
        self._map_path = map_path
        # normalized_name -> alias, e.g. {"Lissa Hyacinth": "{User1}"}
        self._map: dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def anonymize_message(
        self, sender_full_name: str, content: str
    ) -> tuple[str, str]:
        """
        Anonymize a single message.

        Parameters
        ----------
        sender_full_name:
            The raw sender name from the Zulip API (may include pronouns/batch).
        content:
            The raw message content (Zulip markup, apply_markdown=False).

        Returns
        -------
        (anon_sender, anon_content):
            anon_sender  — alias string, e.g. "{User3}"
            anon_content — content with all @**mentions** replaced by their aliases
        """
        anon_sender = self._alias_for(sender_full_name)
        anon_content = self._anonymize_content(content)
        return anon_sender, anon_content

    def anonymize_topic(self, topic: str) -> str:
        """
        Replace any known real names within a topic string with their aliases.

        Zulip topic names sometimes contain user names verbatim (e.g. "Adam Hajari's
        checkin"). This does a simple find/replace for every normalized name currently
        in the map. Only names already known to the anonymizer (i.e. seen as a sender
        or @-mention) are replaced — unknown names in topics are left as-is.

        For de-anonymization: same invert-and-replace approach as message content.
        """
        result = topic
        for name, alias in self._map.items():
            result = result.replace(name, alias)
        return result

    def get_map(self) -> dict[str, str]:
        """
        Return a copy of the current normalized_name -> alias mapping.

        Intended for local inspection / de-anonymization tooling only.
        Never include this in any response sent to an LLM.
        """
        return dict(self._map)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _alias_for(self, full_name: str) -> str:
        """Look up or assign an alias for a display name."""
        key = _normalize_name(full_name)
        if key not in self._map:
            next_index = len(self._map) + 1
            self._map[key] = f"{{User{next_index}}}"
            self._save()
        return self._map[key]

    def _anonymize_content(self, content: str) -> str:
        """Replace all @**mentions** in content with their aliases."""

        def replace_mention(match: re.Match) -> str:
            raw = match.group(1)
            # Leave group mentions (@**all**, @**here**, etc.) unchanged.
            base = raw.split("|")[0].strip()
            if _normalize_name(base).lower() in _GROUP_MENTIONS:
                return match.group(0)
            name = _mention_name(raw)
            alias = self._alias_for(name) if name else match.group(0)
            return f"@**{alias}**"

        return _MENTION_RE.sub(replace_mention, content)

    def _load(self) -> None:
        """Load the map from disk if the file exists."""
        if self._map_path.exists():
            with open(self._map_path) as f:
                self._map = json.load(f)

    def _save(self) -> None:
        """Persist the current map to disk (creates parent dirs as needed)."""
        self._map_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._map_path, "w") as f:
            json.dump(self._map, f, indent=2)
