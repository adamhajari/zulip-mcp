from datetime import datetime, timezone

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

from .client import ZulipMCPClient, format_messages_for_context
from .config import load_config

config = load_config()
zulip_client = ZulipMCPClient(config["zuliprc"])
defaults = config["defaults"]

app = Server("zulip-mcp")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_channels",
            description="List all Zulip channels the user is subscribed to.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="get_channel_messages",
            description=(
                "Fetch recent messages from a single Zulip channel, grouped by topic. "
                "Use this to summarize activity in a specific channel."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Exact name of the Zulip channel/stream.",
                    },
                    "hours_back": {
                        "type": "integer",
                        "description": (
                            f"How many hours of history to fetch. "
                            f"Defaults to {defaults['hours_back']}."
                        ),
                    },
                },
                "required": ["channel"],
            },
        ),
        types.Tool(
            name="get_full_message",
            description=(
                "Fetch the complete content of a single Zulip message by ID. "
                "Use this when a message was truncated in get_channel_messages or get_digest "
                "and the full content seems relevant."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "integer",
                        "description": "The Zulip message ID.",
                    }
                },
                "required": ["message_id"],
            },
        ),
        types.Tool(
            name="get_digest",
            description=(
                "Fetch recent messages from multiple Zulip channels at once. "
                "Returns per-channel message history ready for summarization. "
                f"Defaults to channels: {defaults['channels']} "
                f"and {defaults['hours_back']}h lookback."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "channels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Override the default channel list.",
                    },
                    "hours_back": {
                        "type": "integer",
                        "description": "Override the default hours lookback.",
                    },
                },
                "required": [],
            },
        ),
    ]


@app.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:
    if name == "list_channels":
        channels = zulip_client.get_subscribed_channels()
        text = "Subscribed channels:\n" + "\n".join(f"- {c}" for c in channels)
        return [types.TextContent(type="text", text=text)]

    if name == "get_channel_messages":
        channel = arguments["channel"]
        hours_back = arguments.get("hours_back", defaults["hours_back"])
        messages = zulip_client.get_messages(channel, hours_back)
        text = format_messages_for_context(channel, messages, defaults["truncation_length"])
        return [types.TextContent(type="text", text=text)]

    if name == "get_full_message":
        msg = zulip_client.get_full_message(arguments["message_id"])
        sender = msg.get("sender_full_name", msg.get("sender_email", "unknown"))
        ts = datetime.fromtimestamp(msg["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        topic = msg.get("subject") or "(no topic)"
        stream = msg.get("display_recipient", "unknown")
        content = msg.get("content", "").strip()
        text = f"**#{stream} > {topic}**\n**{sender}** ({ts}):\n\n{content}"
        return [types.TextContent(type="text", text=text)]

    if name == "get_digest":
        channels = arguments.get("channels") or defaults["channels"]
        hours_back = arguments.get("hours_back", defaults["hours_back"])

        if not channels:
            return [types.TextContent(
                type="text",
                text="No channels configured. Set defaults.channels in config.yaml or pass channels explicitly.",
            )]

        parts = []
        for channel in channels:
            try:
                messages = zulip_client.get_messages(channel, hours_back)
                parts.append(format_messages_for_context(channel, messages, defaults["truncation_length"]))
            except RuntimeError as e:
                parts.append(f"## #{channel}\n\nError: {e}\n")

        return [types.TextContent(type="text", text="\n---\n".join(parts))]

    raise ValueError(f"Unknown tool: {name}")


def main():
    import asyncio
    asyncio.run(mcp.server.stdio.run(app))


if __name__ == "__main__":
    main()
