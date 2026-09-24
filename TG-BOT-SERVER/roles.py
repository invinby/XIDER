"""Role and permission filters used by Telegram handlers.

The owner comes only from ADMIN_ID in the protected environment file. No
runtime JSON file can demote, block or replace that owner.
"""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

import access_store
import bot_settings
from config import ADMIN_ID, GUEST_IDS


Role = access_store.Role


def get_user_role(user_id: int) -> str:
    role = access_store.get_role(int(user_id), ADMIN_ID)
    if role == Role.GUEST:
        # Migration bridge: legacy settings retain their role, but can never
        # override an explicit block or the protected owner.
        try:
            legacy_admins = {int(item) for item in bot_settings.get("admins", []) if str(item).isdigit()}
            if int(user_id) in legacy_admins:
                return Role.COOWNER
            if int(user_id) in set(GUEST_IDS):
                return Role.GUEST
        except (TypeError, ValueError):
            pass
    return role


def is_owner(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID)


class OwnerFilter(BaseFilter):
    """Only the protected .env owner may open administration."""

    async def __call__(self, obj: Message | CallbackQuery) -> bool:
        return bool(obj.from_user and is_owner(obj.from_user.id))


class AdminFilter(BaseFilter):
    """Owner/co-owner full access; user gets explicitly granted actions."""

    async def __call__(self, obj: Message | CallbackQuery) -> bool:
        if not obj.from_user:
            return False
        user_id = int(obj.from_user.id)
        role = get_user_role(user_id)
        if role in (Role.OWNER, Role.COOWNER):
            return True
        if role != Role.USER:
            return False
        if isinstance(obj, Message):
            # A user can reach an FSM input state only through a callback that
            # was already permission-checked for this same Telegram account.
            return True
        callback = obj.data or ""
        try:
            from bot import SESSION  # avoids bot/roles import cycle at import time
            selected_device = SESSION.get("target")
        except Exception:
            selected_device = None
        return access_store.can_use_callback(user_id, callback, ADMIN_ID, selected_device)


class ReadOnlyFilter(BaseFilter):
    """Navigation and information for owner, co-owner, user and guest."""

    async def __call__(self, obj: Message | CallbackQuery) -> bool:
        return bool(obj.from_user and get_user_role(int(obj.from_user.id)) != Role.BLOCKED)


class AnyAccessFilter(AdminFilter):
    """Compatibility name for legacy command handlers.

    Kept as an action filter: guests may read the new overview screens but
    cannot call a legacy remote command by guessing its callback data.
    """
