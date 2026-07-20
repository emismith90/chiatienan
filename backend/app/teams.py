"""Bot Framework wiring: inbound ``/api/messages`` + proactive replies.

Flow (design §3): ``/api/messages`` authenticates the activity, parses it,
downloads any inline bill photos, enqueues a :class:`~app.worker.TurnJob`, and
returns immediately. The worker later replies **proactively** using the saved
``ConversationReference`` (``continue_conversation``), because a full agent run
exceeds the Bot Connector's ~15 s inbound timeout.

Single-tenant auth (design §7): configured via ``channel_auth_tenant`` so
inbound/outbound calls carry the Niteco tenant. The pure parsing lives in
:mod:`app.teams_parse`; this module holds only the botbuilder-coupled glue and is
guarded so the app still boots (health, admin) when bot creds are absent.
"""
from __future__ import annotations

import base64
import logging
from typing import Any

import aiohttp
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, CardFactory, MessageFactory, TurnContext
from botbuilder.schema import Activity, ConversationReference
from botframework.connector.auth import MicrosoftAppCredentials

from app.config import settings
from app.images import sanitize_images
from app.reply import Reply
from app.teams_parse import parse_activity
from app.worker import TurnJob, Worker

logger = logging.getLogger("chiatienan")


def build_adapter() -> BotFrameworkAdapter:
    """Single-tenant Bot Framework adapter from env config."""
    tenant = settings.microsoft_app_tenant_id if settings.microsoft_app_type.lower() == "singletenant" else None
    adapter_settings = BotFrameworkAdapterSettings(
        app_id=settings.microsoft_app_id,
        app_password=settings.microsoft_app_password,
        channel_auth_tenant=tenant,
    )
    return BotFrameworkAdapter(adapter_settings)


class TeamsBot:
    """Ties the adapter to the worker: inbound → enqueue, worker → proactive send."""

    def __init__(self, adapter: BotFrameworkAdapter, worker: Worker) -> None:
        self.adapter = adapter
        self.worker = worker
        self.app_id = settings.microsoft_app_id
        self.bot_handle = settings.bot_handle

    async def process_request(self, body: dict, auth_header: str) -> None:
        """Authenticate + run the (fast) inbound logic that enqueues a turn."""
        activity = Activity().deserialize(body)
        await self.adapter.process_activity(activity, auth_header, self._on_turn)

    async def _on_turn(self, turn_context: TurnContext) -> None:
        activity = turn_context.activity
        if (activity.type or "").lower() != "message":
            return  # ignore membership/typing/etc.

        raw = activity.serialize() if hasattr(activity, "serialize") else {}
        parsed = parse_activity(raw, bot_app_id=self.app_id, bot_handle=self.bot_handle)

        # In a group chat the bot only gets @mentioned messages; ignore anything
        # that somehow arrives without the bot mention (e.g. 1:1 echoes).
        if not parsed.bot_mentioned and (activity.conversation and activity.conversation.is_group):
            return

        images = await self._download_inline_images(turn_context, parsed.image_attachments)

        reference = TurnContext.get_conversation_reference(activity)
        await self.worker.enqueue(
            TurnJob(
                activity_id=parsed.activity_id,
                user_text=parsed.text,
                sender_id=parsed.sender_id,
                sender_name=parsed.sender_name,
                sender_aad=parsed.sender_aad,
                people_mentions=parsed.people_mentions,
                images=images,
                has_file_attachment=parsed.has_file_attachment,
                conversation_reference=reference,
            )
        )

    async def _download_inline_images(self, turn_context: TurnContext, attachments: list[dict]):
        """Fetch inline images with the bot bearer token → sanitized base64 list."""
        if not attachments:
            return None
        raw: list[dict] = []
        token = None
        for att in attachments:
            url = att.get("content_url")
            mime = att.get("content_type")
            if not url:
                continue
            try:
                if url.startswith("data:"):
                    _, _, b64 = url.partition(",")
                    raw.append({"data": b64, "mimeType": mime})
                    continue
                if token is None:
                    token = await self._bot_token()
                headers = {"Authorization": f"Bearer {token}"} if token else {}
                async with aiohttp.ClientSession() as http:
                    async with http.get(url, headers=headers) as resp:
                        resp.raise_for_status()
                        data = await resp.read()
                raw.append({"data": base64.b64encode(data).decode(), "mimeType": mime})
            except Exception:  # noqa: BLE001 — a broken image must not break the turn
                logger.warning("[teams] failed to download inline image", exc_info=True)
        return sanitize_images(raw)

    async def _bot_token(self) -> str | None:
        try:
            tenant = (
                settings.microsoft_app_tenant_id
                if settings.microsoft_app_type.lower() == "singletenant"
                else None
            )
            creds = MicrosoftAppCredentials(
                self.app_id, settings.microsoft_app_password, channel_auth_tenant=tenant
            )
            return creds.get_access_token()
        except Exception:  # noqa: BLE001
            logger.warning("[teams] could not obtain bot token for image download", exc_info=True)
            return None

    async def send_reply(self, reference: ConversationReference, reply: Reply) -> None:
        """Proactive reply via the saved conversation reference."""

        async def _logic(turn_context: TurnContext) -> None:
            activity = MessageFactory.text(reply.text)
            if reply.card:
                activity.attachments = [CardFactory.adaptive_card(reply.card)]
            await turn_context.send_activity(activity)

        await self.adapter.continue_conversation(reference, _logic, self.app_id)
