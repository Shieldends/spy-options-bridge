from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from spy_options_bridge.models.signal import ExecutionMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    execution_mode: ExecutionMode = ExecutionMode.SANDBOX
    webhook_secret: str = Field(default="change-me", alias="WEBHOOK_SECRET")

    tastytrade_username: str = Field(default="", alias="TASTYTRADE_USERNAME")
    tastytrade_password: str = Field(default="", alias="TASTYTRADE_PASSWORD")
    tastytrade_account_number: str = Field(default="", alias="TASTYTRADE_ACCOUNT_NUMBER")
    tastytrade_is_test: bool = Field(default=False, alias="TASTYTRADE_IS_TEST")

    traderspost_webhook_url: str = Field(default="", alias="TRADERSPOST_WEBHOOK_URL")
    traderspost_enabled: bool = Field(default=False, alias="TRADERSPOST_ENABLED")

    enforce_market_hours: bool = Field(default=True, alias="ENFORCE_MARKET_HOURS")
    allow_extended_hours: bool = Field(default=False, alias="ALLOW_EXTENDED_HOURS")

    default_underlying: str = Field(default="SPY", alias="DEFAULT_UNDERLYING")
    default_quantity: int = Field(default=1, alias="DEFAULT_QUANTITY")
    default_strike_offset_short: int = Field(default=-2, alias="DEFAULT_STRIKE_OFFSET_SHORT")
    default_strike_offset_long: int = Field(default=-3, alias="DEFAULT_STRIKE_OFFSET_LONG")
    default_limit_credit: float = Field(default=0.50, alias="DEFAULT_LIMIT_CREDIT")

    @property
    def is_production(self) -> bool:
        return self.execution_mode == ExecutionMode.PRODUCTION

    @property
    def tastytrade_configured(self) -> bool:
        return bool(
            self.tastytrade_username
            and self.tastytrade_password
            and self.tastytrade_account_number
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
