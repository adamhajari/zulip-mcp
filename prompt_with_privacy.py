#!/usr/bin/env python3
"""
prompt_with_privacy.py — Send a query to Claude using the Zulip MCP with
anonymization enabled, then de-anonymize the response before writing to a file.

Usage:
    python prompt_with_privacy.py "summarize checkins from the last 24 hours" [--output output.md]
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ANONYMIZER_MAP_PATH = Path.home() / ".config" / "zulip_mcp" / "anonymizer_map.json"
CLAUDE_JSON_PATH = Path.home() / ".claude.json"

ANONYMIZE_SUFFIX = " Use anonymize=true for all tool calls."

MCP_TOOLS = [
    "mcp__zulip-mcp__get_channel_messages",
    "mcp__zulip-mcp__get_digest",
    "mcp__zulip-mcp__get_full_message",
    "mcp__zulip-mcp__list_channels",
    "mcp__zulip-mcp__set_interests",
]


def get_mcp_config(script_dir: Path) -> dict:
    """Read the zulip-mcp server config from ~/.claude.json for this project."""
    if not CLAUDE_JSON_PATH.exists():
        raise FileNotFoundError(f"Claude config not found at {CLAUDE_JSON_PATH}")

    with CLAUDE_JSON_PATH.open() as f:
        claude_config = json.load(f)

    projects = claude_config.get("projects", {})
    project_key = str(script_dir)
    project = projects.get(project_key, {})
    mcp_servers = project.get("mcpServers", {})

    if "zulip-mcp" not in mcp_servers:
        raise KeyError(
            f"No 'zulip-mcp' MCP server found for project '{project_key}' in {CLAUDE_JSON_PATH}. "
            "Make sure the MCP server is registered for this project directory."
        )

    return {"mcpServers": mcp_servers}


def load_alias_map() -> dict[str, str]:
    """Load the anonymizer map and invert it: {User1} -> Real Name."""
    if not ANONYMIZER_MAP_PATH.exists():
        return {}

    with ANONYMIZER_MAP_PATH.open() as f:
        name_to_alias: dict[str, str] = json.load(f)

    return {alias: name for name, alias in name_to_alias.items()}


def deanonymize(text: str, alias_to_name: dict[str, str]) -> str:
    """Replace all {UserN} aliases with real names."""
    for alias, name in alias_to_name.items():
        text = text.replace(alias, name)
    return text


def run_claude(prompt: str, mcp_config: dict) -> str:
    """Run claude -p with the given prompt and MCP config, return stdout."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as tmp:
        json.dump(mcp_config, tmp)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [
                "claude",
                "-p", prompt,
                "--mcp-config", tmp_path,
                "--strict-mcp-config",
                "--tools", "",
                "--allowedTools", ",".join(MCP_TOOLS),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print("Claude exited with an error:", file=sys.stderr)
        print(e.stderr, file=sys.stderr)
        sys.exit(1)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query Zulip via Claude with privacy-preserving anonymization."
    )
    parser.add_argument("prompt", help="The query to send to Claude")
    parser.add_argument(
        "--output", "-o",
        default="output.md",
        help="Output file path (default: output.md)",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent

    # 1. Build the anonymized prompt
    full_prompt = args.prompt + ANONYMIZE_SUFFIX

    # 2. Get MCP config for this project
    try:
        mcp_config = get_mcp_config(script_dir)
    except (FileNotFoundError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Run Claude
    print("Querying Claude with anonymization enabled...")
    raw_response = run_claude(full_prompt, mcp_config)

    # 4. De-anonymize
    alias_to_name = load_alias_map()
    if not alias_to_name:
        print(
            "Warning: anonymizer map is empty or missing — response will not be de-anonymized.",
            file=sys.stderr,
        )
    response = deanonymize(raw_response, alias_to_name)

    # 5. Write output
    output_path = Path(args.output)
    output_path.write_text(response, encoding="utf-8")
    print(f"Output written to {output_path}")


if __name__ == "__main__":
    main()
