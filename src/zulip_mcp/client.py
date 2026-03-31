from datetime import datetime, timezone
from typing import Any

import zulip


class ZulipMCPClient:
    def __init__(self, zuliprc_path: str):
        self._client = zulip.Client(config_file=zuliprc_path)

    def get_subscribed_channels(self) -> list[str]:
        result = self._client.get_subscriptions()
        if result["result"] != "success":
            raise RuntimeError(f"Failed to fetch subscriptions: {result.get('msg')}")
        return sorted(s["name"] for s in result["subscriptions"])

    def get_full_message(self, message_id: int) -> dict[str, Any]:
        result = self._client.call_endpoint(
            url=f"messages/{message_id}",
            method="GET",
        )
        if result["result"] != "success":
            raise RuntimeError(f"Failed to fetch message {message_id}: {result.get('msg')}")
        return result["message"]

    def get_messages(self, channel: str, hours_back: int) -> list[dict[str, Any]]:
        since_timestamp = _hours_ago_timestamp(hours_back)

        request = {
            "anchor": "newest",
            "num_before": 1000,
            "num_after": 0,
            "narrow": [{"operator": "channel", "operand": channel}],
            "apply_markdown": False,
            "client_gravatar": True,
        }

        result = self._client.get_messages(request)
        if result["result"] != "success":
            raise RuntimeError(
                f"Failed to fetch messages for '{channel}': {result.get('msg')}"
            )

        messages = [
            m for m in result["messages"] if m["timestamp"] >= since_timestamp
        ]
        return messages


def _hours_ago_timestamp(hours: int) -> int:
    now = datetime.now(timezone.utc)
    delta_seconds = hours * 3600
    return int(now.timestamp()) - delta_seconds


def format_messages_for_context(channel: str, messages: list[dict], truncation_length: int = 500) -> str:
    if not messages:
        return f"## #{channel}\n\nNo messages in this time period.\n"

    # Group by topic
    by_topic: dict[str, list[dict]] = {}
    for msg in messages:
        topic = msg.get("subject") or "(no topic)"
        by_topic.setdefault(topic, []).append(msg)

    lines = [f"## #{channel}\n"]
    for topic, msgs in by_topic.items():
        lines.append(f"### {topic}")
        for msg in msgs:
            ts = datetime.fromtimestamp(msg["timestamp"], tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            sender = msg.get("sender_full_name", msg.get("sender_email", "unknown"))
            content = msg.get("content", "").strip()
            msg_id = msg.get("id")
            if len(content) > truncation_length:
                content = content[:truncation_length] + f" [truncated — full content available via get_full_message(id={msg_id})]"
            lines.append(f"**{sender}** ({ts}) [id={msg_id}]:\n{content}\n")

    return "\n".join(lines)
