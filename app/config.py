from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    webhook_secret: str = Field(default="", alias="WEBHOOK_SECRET")
    broker: str = Field(default="tastytrade", alias="BROKER")
    execution_mode: str = Field(default="sandbox", alias="EXECUTION_MODE")

    tastytrade_api_base_url: str = Field(
        default="https://api.cert.tastyworks.com",
        alias="TASTYTRADE_API_BASE_URL",
    )
    tastytrade_username: str = Field(default="", alias="TASTYTRADE_USERNAME")
    tastytrade_password: str = Field(default="", alias="TASTYTRADE_PASSWORD")
    tastytrade_account_number: str = Field(default="", alias="TASTYTRADE_ACCOUNT_NUMBER")
    tastytrade_sandbox_username: str = Field(default="", alias="TASTYTRADE_SANDBOX_USERNAME")
    tastytrade_sandbox_password: str = Field(default="", alias="TASTYTRADE_SANDBOX_PASSWORD")

    tt_secret: str = Field(default="", alias="TT_SECRET")
    tt_refresh: str = Field(default="", alias="TT_REFRESH")

    # Risk management
    auto_take_profit: bool = Field(default=True, alias="AUTO_TAKE_PROFIT")
    take_profit_pct: float = Field(default=0.50, alias="TAKE_PROFIT_PCT")
    danger_zone_pct: float = Field(default=0.01, alias="DANGER_ZONE_PCT")

    # Notifications (Discord webhook and/or Telegram bot)
    discord_webhook_url: str = Field(default="", alias="DISCORD_WEBHOOK_URL")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    apca_api_key_id: str = Field(default="", alias="APCA_API_KEY_ID")
    apca_api_secret_key: str = Field(default="", alias="APCA_API_SECRET_KEY")
    apca_api_base_url: str = Field(default="https://paper-api.alpaca.markets", alias="APCA_API_BASE_URL")
    alpaca_api_key: str = Field(default="", alias="ALPACA_API_KEY")
    alpaca_secret_key: str = Field(default="", alias="ALPACA_SECRET_KEY")

    default_underlying: str = Field(default="SPY", alias="DEFAULT_UNDERLYING")
    default_quantity: int = Field(default=1, alias="DEFAULT_QUANTITY")
    default_strike_offset_short: int = Field(default=-2, alias="DEFAULT_STRIKE_OFFSET_SHORT")
    default_strike_offset_long: int = Field(default=-3, alias="DEFAULT_STRIKE_OFFSET_LONG")
    default_limit_credit: float = Field(default=0.50, alias="DEFAULT_LIMIT_CREDIT")

    @property
    def is_live(self) -> bool:
        return self.execution_mode.lower() == "production"

    @property
    def tastytrade_username_resolved(self) -> str:
        return self.tastytrade_username or self.tastytrade_sandbox_username

    @property
    def tastytrade_password_resolved(self) -> str:
        return self.tastytrade_password or self.tastytrade_sandbox_password

    @property
    def tastytrade_configured(self) -> bool:
        has_login = bool(self.tastytrade_username_resolved and self.tastytrade_password_resolved)
        has_oauth = bool(self.tt_secret and self.tt_refresh)
        return (has_login or has_oauth) and bool(self.tastytrade_account_number)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def alpaca_key_id(self) -> str:
        return self.apca_api_key_id or self.alpaca_api_key

    @property
    def alpaca_secret(self) -> str:
        return self.apca_api_secret_key or self.alpaca_secret_key

    @property
    def alpaca_configured(self) -> bool:
        return bool(self.alpaca_key_id and self.alpaca_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
