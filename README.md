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

### 4. Install the package

```bash
pip install -e .
```

Or with `uv`:

```bash
uv pip install -e .
```

### 5. Add to Claude Code (or Claude Desktop)

**Claude Code** — add to your MCP settings:

```bash
claude mcp add zulip-mcp -- zulip-mcp
```

Or manually in `~/.claude/mcp.json` (or `.mcp.json` in the project root):

```json
{
  "mcpServers": {
    "zulip-mcp": {
      "command": "zulip-mcp"
    }
  }
}
```

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "zulip-mcp": {
      "command": "zulip-mcp"
    }
  }
}
```

If you installed with `uv` or into a virtualenv, use the full path to the binary, e.g.:

```json
{
  "mcpServers": {
    "zulip-mcp": {
      "command": "/path/to/venv/bin/zulip-mcp"
    }
  }
}
```

> **Note:** The server reads `config.yaml` from the directory where the package is installed. If you move the project, update the MCP config to set `ZULIP_MCP_CONFIG` to the absolute path of your `config.yaml`:
> ```json
> {
>   "mcpServers": {
>     "zulip-mcp": {
>       "command": "zulip-mcp",
>       "env": {
>         "ZULIP_MCP_CONFIG": "/absolute/path/to/zulip/config.yaml"
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

### Available tools

| Tool | Description |
|------|-------------|
| `list_channels` | List all channels you're subscribed to |
| `get_channel_messages` | Fetch recent messages from one channel |
| `get_digest` | Fetch messages from all configured channels at once |
| `get_full_message` | Fetch the complete content of a single message by ID |

You can override defaults inline — e.g. "summarize #checkins for the last 3 days" will pass `hours_back: 72` to the tool.

## Notes

- Messages longer than 500 characters are truncated in digest/channel views. Truncated messages include their ID and a note, so Claude can call `get_full_message` to retrieve the complete content when it seems relevant.
- The server fetches up to 1000 messages per channel per request.
- `.zuliprc` contains your API key — don't commit it. It's in `.gitignore` by default.
