# zulip-mcp

An MCP server that gives Claude access to your Zulip channels, so you can ask for summaries of recent activity.

## Setup

### 1. Get your Zulip API key

1. In Zulip, go to **Settings → Account & privacy → API key**
2. Click **Generate API key** and copy it

### 2. Create a `.zuliprc` file

Copy the example file and fill in your credentials:

```bash
cp .zuliprc.example .zuliprc
```

Then edit `.zuliprc` with your email and API key:

```ini
[api]
key=your_api_key_here
email=your_email@recurse.com
site=https://recurse.zulipchat.com
```

Alternatively, Zulip can generate a pre-filled `.zuliprc` for you: **Settings → Account & privacy → API key → Show/change your API key → Download .zuliprc**. Place the downloaded file in the project directory.

### 3. Configure your channels

Edit `config.yaml` to set the channels you want in your default digest and how far back to look:

```yaml
zuliprc: .zuliprc

defaults:
  hours_back: 24
  channels:
    - general
    - announcements
    - your-interests
```

Use exact stream names as they appear in Zulip.

### 4. Claude Install

If you're using Claude Code, you can have Claude handle steps 4 and 5 for you. Open Claude Code in the project directory and paste this prompt:

> "Install dependencies for this project and register it as an MCP server in my Claude Code settings."

Claude knows the absolute path of the project and will fill it in correctly.

### 5. Install dependencies (manual)

```bash
uv sync
```

### 6. Add to Claude Code (or Claude Desktop) (manual)

**Claude Code** — run from the project directory:

```bash
claude mcp add zulip-mcp -- uv run --project /absolute/path/to/zulip zulip-mcp
```

This writes the server into `~/.claude/settings.json`. To scope it to this project only, add it manually to `.claude/settings.json` (committed) or `.claude/settings.local.json` (local only) in the project root:

```json
{
  "mcpServers": {
    "zulip-mcp": {
      "command": "uv",
      "args": ["run", "--project", "/absolute/path/to/zulip", "zulip-mcp"]
    }
  }
}
```

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "zulip-mcp": {
      "command": "uv",
      "args": ["run", "--project", "/absolute/path/to/zulip", "zulip-mcp"]
    }
  }
}
```

> **Note:** The server reads `config.yaml` from the project directory by default. You can override this with the `ZULIP_MCP_CONFIG` environment variable:
> ```json
> {
>   "mcpServers": {
>     "zulip-mcp": {
>       "command": "uv",
>       "args": ["run", "--project", "/absolute/path/to/zulip", "zulip-mcp"],
>       "env": {
>         "ZULIP_MCP_CONFIG": "/absolute/path/to/config.yaml"
>       }
>     }
>   }
> }
> ```

## Usage

Once the MCP is connected, you can ask Claude things like:

- *"Summarize what's been discussed in #general today"*
- *"Give me a digest of all my configured channels"*
- *"What topics came up in #announcements in the last 48 hours?"*
- *"List my subscribed channels"*

To protect user privacy, pass `anonymize=true` to any tool that returns message content. Real names will be replaced with stable aliases (`{User1}`, `{User2}`, etc.) before anything is sent to Claude:

- *"Summarize the checkins channel from the last 24 hours. Use anonymize=true for all tool calls."*

The alias mapping is saved locally at `~/.config/zulip_mcp/anonymizer_map.json` for de-anonymization after the fact.

### Privacy-first CLI: `prompt_with_privacy.py`

For a fully automated, privacy-preserving workflow, use the included script. It sends your query to Claude with anonymization enforced, then automatically de-anonymizes the response using the local alias map before writing the result to a file.

```bash
python prompt_with_privacy.py "your query here"
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--output`, `-o` | `output.md` | Path to write the de-anonymized response |

Example:

```bash
python prompt_with_privacy.py "summarize checkins from the last 24 hours. return a one sentence summary for every person." --output checkins.md
```

The script:
1. Appends *"Use anonymize=true for all tool calls."* to your prompt
2. Invokes `claude -p` with the Zulip MCP and no other tools
3. Replaces all `{UserN}` aliases in the response with real names
4. Writes the result to the output file

> **Note:** Requires `claude` to be available in your `PATH` and the `zulip-mcp` server registered in `~/.claude.json` for this project directory (see setup steps above).

### Available tools

| Tool | Description |
|------|-------------|
| `list_channels` | List all channels you're subscribed to |
| `get_channel_messages` | Fetch recent messages from one channel |
| `get_digest` | Fetch messages from all configured channels at once |
| `get_full_message` | Fetch the complete content of a single message by ID |
| `set_interests` | Save your interests to config.yaml to filter digests |

All tools that return message content accept an optional `anonymize` parameter (default: `false`).

You can override defaults inline — e.g. "summarize #checkins for the last 3 days" will pass `hours_back: 72` to the tool.

## Notes

- Messages longer than 500 characters are truncated in digest/channel views. Truncated messages include their ID and a note, so Claude can call `get_full_message` to retrieve the complete content when it seems relevant.
- The server fetches up to 1000 messages per channel per request.
- `.zuliprc` contains your API key — don't commit it. It's in `.gitignore` by default.
