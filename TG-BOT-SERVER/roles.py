from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from config import ADMIN_ID, GUEST_IDS
import bot_settings

class Role:
    ADMIN = "admin"
    GUEST = "guest"
    NONE = "none"

def get_user_role(user_id: int) -> str:
    # 1. Проверяем хардкод админа
    if user_id == ADMIN_ID:
        return Role.ADMIN
        
    # 2. Проверяем динамических админов
    try:
        dyn_admins = [int(x) for x in (bot_settings.get("admins") or []) if str(x).strip().isdigit()]
        if user_id in dyn_admins:
            return Role.ADMIN
    except Exception:
        pass

    # 3. Проверяем хардкод гостей
    if user_id in GUEST_IDS:
        return Role.GUEST
        
    # 4. Проверяем динамических гостей
    try:
        dyn_guests = [int(x) for x in (bot_settings.get("guests") or []) if str(x).strip().isdigit()]
        if user_id in dyn_guests:
            return Role.GUEST
    except Exception:
        pass

    return Role.NONE

class AdminFilter(BaseFilter):
    """Фильтр для доступа только администратора."""
    async def __call__(self, obj: Message | CallbackQuery) -> bool:
        user_id = obj.from_user.id
        return get_user_role(user_id) == Role.ADMIN

class AnyAccessFilter(BaseFilter):
    """Фильтр для доступа администратора и гостей."""
    async def __call__(self, obj: Message | CallbackQuery) -> bool:
        user_id = obj.from_user.id
        role = get_user_role(user_id)
        return role in (Role.ADMIN, Role.GUEST)
