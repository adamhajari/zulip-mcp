from datetime import datetime, timezone
from typing import Any

import zulip

from .anonymizer import Anonymizer


class ZulipMCPClient:
    def __init__(self, zuliprc_path: str):
        self._client = zulip.Client(config_file=zuliprc_path)
        # Single Anonymizer instance shared across all calls on this client.
        # Aliases are stable: the same person always gets the same alias for
        # the lifetime of the map file (~/.config/zulip_mcp/anonymizer_map.json).
        self.anonymizer = Anonymizer()

    def get_subscribed_channels(self) -> list[str]:
        result = self._client.get_subscriptions()
        if result["result"] != "success":
            raise RuntimeError(f"Failed to fetch subscriptions: {result.get('msg')}")
        return sorted(s["name"] for s in result["subscriptions"])

    def get_full_message(self, message_id: int, anonymize: bool = False) -> dict[str, Any]:
        """
        Fetch the complete content of a single message by ID.

        Parameters
        ----------
        message_id:
            Zulip message ID.
        anonymize:
            When True, replace sender_full_name and @-mentions in content with
            stable aliases before returning. Real names never appear in the
            returned dict when this is True.
        """
        result = self._client.call_endpoint(
            url=f"messages/{message_id}",
            method="GET",
        )
        if result["result"] != "success":
            raise RuntimeError(f"Failed to fetch message {message_id}: {result.get('msg')}")
        msg = result["message"]
        if anonymize:
            msg = _anonymize_msg(msg, self.anonymizer)
        return msg

    def get_messages(self, channel: str, hours_back: int, anonymize: bool = False) -> list[dict[str, Any]]:
        """
        Fetch recent messages from a channel.

        Parameters
        ----------
        channel:
            Exact Zulip channel/stream name.
        hours_back:
            How many hours of history to retrieve.
        anonymize:
            When True, replace sender_full_name and @-mentions in content with
            stable aliases before returning. Real names never appear in the
            returned list when this is True.
        """
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

        if anonymize:
            messages = [_anonymize_msg(m, self.anonymizer) for m in messages]

        return messages


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _anonymize_msg(msg: dict[str, Any], anonymizer: Anonymizer) -> dict[str, Any]:
    """
    Return a shallow copy of msg with sender_full_name and content anonymized.

    We make a shallow copy so that the original dict from the Zulip API is
    not mutated in place.
    """
    sender = msg.get("sender_full_name", msg.get("sender_email", "unknown"))
    content = msg.get("content", "")
    topic = msg.get("subject", "")
    anon_sender, anon_content = anonymizer.anonymize_message(sender, content)
    # Topic is anonymized after sender so the sender's name is already in the map.
    anon_topic = anonymizer.anonymize_topic(topic)
    return {**msg, "sender_full_name": anon_sender, "content": anon_content, "subject": anon_topic}


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
