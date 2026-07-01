"""Helpers for parsing Telegram Bot API webhook updates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ChannelPost:
    chat_id: str
    message_id: int
    text: str
    reply_to_message_id: int | None = None
    sender_chat_id: str | None = None
    date: int | None = None


def parse_channel_post(update: dict[str, Any]) -> ChannelPost | None:
    """Extract a channel post from a Telegram Update object."""
    channel_post = update.get("channel_post")
    if not channel_post:
        return None

    chat = channel_post.get("chat", {})
    chat_id = str(chat.get("id", ""))
    if not chat_id:
        return None

    text = channel_post.get("text", "")
    if not text:
        caption = channel_post.get("caption", "")
        text = caption

    reply_to = channel_post.get("reply_to_message")
    reply_to_message_id = reply_to.get("message_id") if reply_to else None

    sender_chat = channel_post.get("sender_chat", {})
    sender_chat_id = str(sender_chat.get("id")) if sender_chat else None

    return ChannelPost(
        chat_id=chat_id,
        message_id=int(channel_post.get("message_id", 0)),
        text=text,
        reply_to_message_id=reply_to_message_id,
        sender_chat_id=sender_chat_id,
        date=channel_post.get("date"),
    )
