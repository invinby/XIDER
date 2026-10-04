"""Role and permission filters used by Telegram handlers.

The owner comes only from ADMIN_ID in the protected environment file. No
runtime JSON file can demote, block or replace that owner.
"""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
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
                # Legacy notification/admin lists are not an authorization
                # source. The owner must grant new user permissions explicitly.
                return Role.USER
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
    """Owner full access; user gets only explicitly granted actions."""

    async def __call__(
        self,
        obj: Message | CallbackQuery,
        state: FSMContext | None = None,
    ) -> bool:
        if not obj.from_user:
            return False
        user_id = int(obj.from_user.id)
        role = get_user_role(user_id)
        if role == Role.OWNER:
            return True
        if role != Role.USER:
            if isinstance(obj, Message) and state is not None:
                await state.clear()
            return False
        if isinstance(obj, Message):
            # A callback can start an input flow, but the user may lose their
            # role/device/button grant before sending the next message. Recheck
            # the original action and device at the point the command is sent.
            # /cancel is intentionally always available to clear a pending form.
            text = (obj.text or "").strip()
            command = text.split(maxsplit=1)[0].split("@", 1)[0] if text else ""
            if command == "/cancel":
                return True
            if state is None:
                return False
            try:
                pending = await state.get_data()
                callback = str(pending.get("authorization_callback") or "")
                target = str(pending.get("authorization_target") or "")
                current_state = await state.get_state()
            except Exception:
                return False
            if (
                not current_state
                or not callback
                or not target
                or target == "all"
                or not access_store.can_use_callback(user_id, callback, ADMIN_ID, target)
            ):
                await state.clear()
                return False
            devices = _device_store()
            if not devices or devices.get(target) is None:
                await state.clear()
                return False
            return True
        callback = obj.data or ""
        try:
            from bot import SESSION, devices  # avoids bot/roles import cycle at import time
            selected_device = SESSION.get("target")
        except Exception:
            devices = None
            selected_device = None
        if not access_store.can_use_callback(user_id, callback, ADMIN_ID, selected_device):
            return False
        if callback in access_store.USER_NAVIGATION_CALLBACKS:
            return True
        if callback.startswith("dev:"):
            device_id = callback.split(":", 1)[1]
            return bool(device_id and devices and devices.get(device_id) is not None)
        # A grant must still refer to a device that exists. This closes stale
        # grants left behind by old databases or a concurrent remove/block.
        return bool(selected_device and devices and devices.get(selected_device) is not None)


def _device_store():
    """Load the live device registry without creating a module import cycle."""
    try:
        from bot import devices
    except Exception:
        return None
    return devices


class ReadOnlyFilter(BaseFilter):
    """Navigation and information for owner, user and guest."""

    async def __call__(self, obj: Message | CallbackQuery) -> bool:
        return bool(obj.from_user and get_user_role(int(obj.from_user.id)) != Role.BLOCKED)


class AnyAccessFilter(AdminFilter):
    """Compatibility name for legacy command handlers.

    Kept as an action filter: guests may read the new overview screens but
    cannot call a legacy remote command by guessing its callback data.
    """
