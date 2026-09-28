"""/api/settings."""
from __future__ import annotations

from fastapi import APIRouter

from ..models import SettingPatch, SettingItem, SettingsResponse
from ..services import settings_service

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings", response_model=SettingsResponse)
def list_settings() -> SettingsResponse:
    items = [SettingItem(key=x["key"], value=x["value"]) for x in settings_service.list_settings()]
    return SettingsResponse(items=items)


@router.patch("/settings/{key}")
def patch_setting(key: str, body: SettingPatch) -> dict:
    return settings_service.update_setting(key, body.value)