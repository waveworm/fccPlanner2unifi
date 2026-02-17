from __future__ import annotations

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    port: int = Field(default=3000, alias="PORT")

    pco_base_url: AnyHttpUrl = Field(default="https://api.planningcenteronline.com", alias="PCO_BASE_URL")
    pco_auth_type: str = Field(default="personal_access_token", alias="PCO_AUTH_TYPE")
    pco_app_id: str = Field(default="", alias="PCO_APP_ID")
    pco_secret: str = Field(default="", alias="PCO_SECRET")
    pco_access_token: str = Field(default="", alias="PCO_ACCESS_TOKEN")
    pco_calendar_id: str = Field(default="", alias="PCO_CALENDAR_ID")
    pco_location_must_contain: str = Field(default="", alias="PCO_LOCATION_MUST_CONTAIN")
    pco_events_cache_seconds: int = Field(default=60, alias="PCO_EVENTS_CACHE_SECONDS")
    pco_min_fetch_interval_seconds: int = Field(default=60, alias="PCO_MIN_FETCH_INTERVAL_SECONDS")
    pco_max_pages: int = Field(default=40, alias="PCO_MAX_PAGES")
    pco_per_page: int = Field(default=100, alias="PCO_PER_PAGE")

    unifi_access_base_url: AnyHttpUrl = Field(alias="UNIFI_ACCESS_BASE_URL")
    unifi_access_verify_tls: bool = Field(default=False, alias="UNIFI_ACCESS_VERIFY_TLS")
    unifi_access_auth_type: str = Field(default="none", alias="UNIFI_ACCESS_AUTH_TYPE")
    unifi_access_username: str = Field(default="", alias="UNIFI_ACCESS_USERNAME")
    unifi_access_password: str = Field(default="", alias="UNIFI_ACCESS_PASSWORD")
    unifi_access_api_token: str = Field(default="", alias="UNIFI_ACCESS_API_TOKEN")
    unifi_access_api_key_header: str = Field(default="X-API-Key", alias="UNIFI_ACCESS_API_KEY_HEADER")

    apply_to_unifi: bool = Field(default=False, alias="APPLY_TO_UNIFI")

    sync_cron: str = Field(default="*/5 * * * *", alias="SYNC_CRON")
    sync_interval_seconds: int = Field(default=300, alias="SYNC_INTERVAL_SECONDS")
    sync_lookahead_hours: int = Field(default=168, alias="SYNC_LOOKAHEAD_HOURS")
    sync_lookbehind_hours: int = Field(default=24, alias="SYNC_LOOKBEHIND_HOURS")

    room_door_mapping_file: str = Field(default="./config/room-door-mapping.json", alias="ROOM_DOOR_MAPPING_FILE")
