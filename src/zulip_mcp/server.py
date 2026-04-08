from datetime import datetime, timezone

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

from .client import ZulipMCPClient, format_messages_for_context, get_topics_from_messages, filter_messages_by_topic
from .config import load_config, save_interests

config = load_config()
zulip_client = ZulipMCPClient(config["zuliprc"])
defaults = config["defaults"]

app = Server(
    "zulip-mcp",
    instructions=(
        "You are a helpful assistant with access to a Recurse Center Zulip workspace. "
        "You can list channels, fetch messages, and generate digests. "
        "\n\n"
        "IMPORTANT — the anonymize parameter is opt-in and must ONLY be used when the "
        "user explicitly requests it (e.g. 'use anonymize=true'). Never pass anonymize=true "
        "on your own initiative.\n\n"
        "When anonymize=true is requested, tool responses will contain aliases like "
        "{User1}, {User2}, etc. in place of real names. In that case you MUST:\n"
        "  1. Preserve every alias exactly as-is in your response (e.g. {User12}, not 'User12', "
        "'user 12', 'they', 'this person', or any other substitution).\n"
        "  2. NEVER attempt to infer, guess, reconstruct, or look up real names from aliases — "
        "not from context, not from any file, not from any other source. Aliases are "
        "intentionally opaque and must remain so.\n"
        "  3. NEVER read, request, or access the anonymizer mapping file "
        "(~/.config/zulip_mcp/anonymizer_map.json or any similarly named file). "
        "This file is off-limits entirely.\n"
        "  4. Never drop an alias — if a person did something noteworthy, refer to them "
        "by their alias.\n"
        "De-anonymization is handled externally after you respond. Your job is only to "
        "preserve aliases faithfully — never to resolve them."
    ),
)

# Appended to tool responses when anonymize=True so Claude preserves aliases
# exactly as-is, enabling reliable de-anonymization of its responses afterward.
_ANONYMIZE_NOTICE = (
    "\n\n---\n"
    "Note: User identities in this content have been replaced with anonymous aliases "
    "({User1}, {User2}, etc.). "
    "You MUST preserve these aliases exactly as-is in your response — "
    "do not paraphrase, omit, or alter them. "
    "De-anonymization will be applied to your response after the fact."
)


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
                    "anonymize": {
                        "type": "boolean",
                        "description": (
                            "When true, replace real user names with stable aliases "
                            "(e.g. {User1}, {User2}) in all sender fields and @-mentions. "
                            "Defaults to false."
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
                    },
                    "anonymize": {
                        "type": "boolean",
                        "description": (
                            "When true, replace real user names with stable aliases "
                            "(e.g. {User1}, {User2}) in sender and @-mentions. "
                            "Defaults to false."
                        ),
                    },
                },
                "required": ["message_id"],
            },
        ),
        types.Tool(
            name="set_interests",
            description=(
                "Save the user's interests to config.yaml. "
                "Call this after asking the user what topics they care about. "
                "These interests are used to filter and prioritize digests."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "interests": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of interest topics, e.g. ['AI coding agents', 'machine learning'].",
                    }
                },
                "required": ["interests"],
            },
        ),
        types.Tool(
            name="get_checkins",
            description=(
                "Fetch check-in posts from the 'checkins' channel, one topic at a time. "
                "Each call returns one topic's messages plus a `next_topic` value. "
                "REQUIRED workflow:\n"
                "1. Call with no arguments to start — gets the first topic's messages.\n"
                "2. Write a summary of that topic.\n"
                "3. If the response includes 'next_topic', call get_checkins(topic=<next_topic>).\n"
                "4. Repeat steps 2-3 until the response says 'No more topics'.\n"
                "You MUST keep calling until there are no more topics."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": (
                            "Topic to fetch. Omit on first call — returns the first topic automatically. "
                            "On subsequent calls, pass the exact `next_topic` value from the previous response."
                        ),
                    },
                    "hours_back": {
                        "type": "integer",
                        "description": (
                            f"How many hours of history to fetch. "
                            f"Defaults to {defaults['hours_back']}."
                        ),
                    },
                    "anonymize": {
                        "type": "boolean",
                        "description": (
                            "When true, replace real user names with stable aliases. "
                            "Defaults to false."
                        ),
                    },
                },
                "required": [],
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
                    "anonymize": {
                        "type": "boolean",
                        "description": (
                            "When true, replace real user names with stable aliases "
                            "(e.g. {User1}, {User2}) in all sender fields and @-mentions. "
                            "Defaults to false."
                        ),
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
        anonymize = arguments.get("anonymize", False)
        messages = zulip_client.get_messages(channel, hours_back, anonymize=anonymize)
        text = format_messages_for_context(channel, messages, defaults["truncation_length"])
        if anonymize:
            text += _ANONYMIZE_NOTICE
        return [types.TextContent(type="text", text=text)]

    if name == "get_full_message":
        anonymize = arguments.get("anonymize", False)
        msg = zulip_client.get_full_message(arguments["message_id"], anonymize=anonymize)
        sender = msg.get("sender_full_name", msg.get("sender_email", "unknown"))
        ts = datetime.fromtimestamp(msg["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        topic = msg.get("subject") or "(no topic)"
        stream = msg.get("display_recipient", "unknown")
        content = msg.get("content", "").strip()
        text = f"**#{stream} > {topic}**\n**{sender}** ({ts}):\n\n{content}"
        if anonymize:
            text += _ANONYMIZE_NOTICE
        return [types.TextContent(type="text", text=text)]

    if name == "set_interests":
        interests = arguments["interests"]
        save_interests(interests)
        config["interests"] = interests
        return [types.TextContent(
            type="text",
            text=f"Interests saved: {', '.join(interests)}",
        )]

    if name == "get_checkins":
        checkins_channel = "checkins"
        hours_back = arguments.get("hours_back", defaults["hours_back"])
        anonymize = arguments.get("anonymize", False)
        topic = arguments.get("topic")

        messages = zulip_client.get_messages(checkins_channel, hours_back, anonymize=anonymize)
        all_topics = get_topics_from_messages(messages)

        if not all_topics:
            return [types.TextContent(
                type="text",
                text=f"No check-in messages in the last {hours_back} hours.",
            )]

        # Determine which topic to return and what comes next
        if topic is None:
            current_topic = all_topics[0]
        else:
            current_topic = topic

        try:
            idx = all_topics.index(current_topic)
        except ValueError:
            return [types.TextContent(
                type="text",
                text=f"Topic '{current_topic}' not found in checkins. Available topics: {all_topics}",
            )]

        next_topic = all_topics[idx + 1] if idx + 1 < len(all_topics) else None
        topics_remaining = len(all_topics) - idx - 1

        topic_messages = filter_messages_by_topic(messages, current_topic)
        if not topic_messages:
            text = f"No messages for topic '{current_topic}'."
        else:
            text = format_messages_for_context(checkins_channel, topic_messages, defaults["truncation_length"])

        text += f"\n\n---\nTopic {idx + 1} of {len(all_topics)}. "
        if next_topic:
            text += (
                f"**next_topic: {next_topic}**\n"
                f"({topics_remaining} topic(s) remaining)\n"
                f"Summarize the above, then call get_checkins(topic='{next_topic}')."
            )
        else:
            text += "No more topics. All check-ins have been fetched."

        if anonymize:
            text += _ANONYMIZE_NOTICE
        return [types.TextContent(type="text", text=text)]

    if name == "get_digest":
        if not config.get("interests"):
            return [types.TextContent(
                type="text",
                text=(
                    "No interests are configured yet. "
                    "Please ask the user what topics they'd like to focus on "
                    "(e.g. AI, machine learning, programming languages, systems), "
                    "then call set_interests with their response before fetching the digest."
                ),
            )]

        channels = arguments.get("channels") or defaults["channels"]
        hours_back = arguments.get("hours_back", defaults["hours_back"])
        anonymize = arguments.get("anonymize", False)

        if not channels:
            return [types.TextContent(
                type="text",
                text="No channels configured. Set defaults.channels in config.yaml or pass channels explicitly.",
            )]

        parts = []
        for channel in channels:
            try:
                messages = zulip_client.get_messages(channel, hours_back, anonymize=anonymize)
                parts.append(format_messages_for_context(channel, messages, defaults["truncation_length"]))
            except RuntimeError as e:
                parts.append(f"## #{channel}\n\nError: {e}\n")

        text = "\n---\n".join(parts)
        if anonymize:
            text += _ANONYMIZE_NOTICE
        return [types.TextContent(type="text", text=text)]

    raise ValueError(f"Unknown tool: {name}")


def main():
    import asyncio
    import mcp.server.stdio

    async def _run():
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
