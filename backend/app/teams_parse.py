"""Pure parsing of an inbound Teams activity (no botbuilder dependency).

Kept separate from ``teams.py`` (the botbuilder adapter + HTTP wiring) so the
fiddly bits — stripping the bot's ``<at>`` mention, classifying people mentions,
telling inline images from file attachments — are unit-testable against plain
dicts.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Teams file-attachment content type (goes to SharePoint/OneDrive; not
# retrievable with the bot token — design §7).
_FILE_ATTACHMENT_TYPE = "application/vnd.microsoft.teams.file.download.info"


@dataclass
class ParsedTurn:
    activity_id: str
    text: str
    bot_mentioned: bool
    sender_id: str | None
    sender_aad: str | None
    sender_name: str | None
    people_mentions: list[dict] = field(default_factory=list)
    image_attachments: list[dict] = field(default_factory=list)
    has_file_attachment: bool = False


def _is_bot_mention(mentioned: dict, *, bot_app_id: str, bot_handle: str) -> bool:
    mid = str(mentioned.get("id") or "")
    name = str(mentioned.get("name") or "").strip().lower()
    if bot_app_id and (mid == bot_app_id or mid.endswith(bot_app_id)):
        return True
    return bool(bot_handle) and name == bot_handle.strip().lower()


def strip_mentions(
    text: str, entities: list[dict], *, bot_app_id: str, bot_handle: str
) -> tuple[str, bool, list[dict]]:
    """Remove mention markup and classify mentions.

    Returns ``(clean_text, bot_mentioned, people_mentions)`` where
    ``people_mentions`` is ``[{teams_user_id, name}]`` for every non-bot mention.
    """
    clean = text or ""
    bot_mentioned = False
    people: list[dict] = []

    for ent in entities or []:
        if (ent.get("type") or "").lower() != "mention":
            continue
        mentioned = ent.get("mentioned") or {}
        at_text = ent.get("text")
        if at_text:
            clean = clean.replace(at_text, " ")
        if _is_bot_mention(mentioned, bot_app_id=bot_app_id, bot_handle=bot_handle):
            bot_mentioned = True
        else:
            people.append(
                {"teams_user_id": mentioned.get("id"), "name": mentioned.get("name")}
            )

    # collapse whitespace left by removed <at> spans
    clean = " ".join(clean.split()).strip()
    return clean, bot_mentioned, people


def parse_activity(activity: dict, *, bot_app_id: str, bot_handle: str) -> ParsedTurn:
    """Normalise a Teams activity dict into a :class:`ParsedTurn`."""
    frm = activity.get("from") or {}
    clean_text, bot_mentioned, people = strip_mentions(
        activity.get("text") or "",
        activity.get("entities") or [],
        bot_app_id=bot_app_id,
        bot_handle=bot_handle,
    )

    image_attachments: list[dict] = []
    has_file = False
    for att in activity.get("attachments") or []:
        ctype = str(att.get("contentType") or "").lower()
        if ctype == _FILE_ATTACHMENT_TYPE:
            has_file = True
        elif ctype.startswith("image/"):
            image_attachments.append(
                {
                    "content_url": att.get("contentUrl"),
                    "content_type": ctype,
                    "name": att.get("name"),
                }
            )

    return ParsedTurn(
        activity_id=str(activity.get("id") or ""),
        text=clean_text,
        bot_mentioned=bot_mentioned,
        sender_id=frm.get("id"),
        sender_aad=frm.get("aadObjectId"),
        sender_name=frm.get("name"),
        people_mentions=people,
        image_attachments=image_attachments,
        has_file_attachment=has_file,
    )
