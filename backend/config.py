import os
from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    # Database
    db_host: str = Field(default="127.0.0.1", alias="DB_HOST")
    db_port: int = Field(default=3306, alias="DB_PORT")
    db_user: str = Field(default="root", alias="DB_USER")
    db_password: str = Field(default="", alias="DB_PASSWORD")
    db_name: str = Field(default="nusuk_key", alias="DB_NAME")

    # ADB & Device
    adb_path: str = Field(default="platform-tools/adb.exe", alias="ADB_PATH")
    # RETIRED in v2: only read once by the DB migration to seed the Legacy Primary Instance.
    adb_target: str = Field(default="192.168.100.8:5555", alias="ADB_TARGET")
    nusuk_package: str = Field(default="com.moh.nusukapp", alias="NUSUK_PACKAGE")
    nusuk_activity: str = Field(default="com.moh.nusukapp.activity.MainActivity", alias="NUSUK_ACTIVITY")

    # AI Provider (OpenAI-Compatible: OpenAI, OpenRouter, Groq, DeepSeek, Ollama, etc.)
    ai_base_url: str = Field(default="https://api.openai.com/v1", alias="AI_BASE_URL")
    ai_api_key: str = Field(default="", alias="AI_API_KEY")
    ai_model: str = Field(default="gpt-4o-mini", alias="AI_MODEL")

    # Timing & Tolerances
    loading_timeout_seconds: int = Field(default=25, alias="LOADING_TIMEOUT_SECONDS")
    circuit_breaker_limit: int = Field(default=5, alias="CIRCUIT_BREAKER_LIMIT")
    action_delay_seconds: float = Field(default=0.0, alias="ACTION_DELAY_SECONDS")
    watchdog_interval_seconds: int = Field(default=7, alias="WATCHDOG_INTERVAL_SECONDS")
    preview_refresh_seconds: int = Field(default=4, alias="PREVIEW_REFRESH_SECONDS")

    # Backend Server
    server_host: str = Field(default="0.0.0.0", alias="SERVER_HOST")
    server_port: int = Field(default=8000, alias="SERVER_PORT")

    @property
    def database_url(self) -> str:
        pwd = f":{self.db_password}" if self.db_password else ""
        return f"mysql+pymysql://{self.db_user}{pwd}@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"

    @property
    def absolute_adb_path(self) -> str:
        return os.path.abspath(self.adb_path)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()

# Backward-compatibility fallback for legacy env variables if present
if not settings.ai_api_key and os.getenv("OPENAI_API_KEY"):
    settings.ai_api_key = os.getenv("OPENAI_API_KEY", "")
if settings.ai_model == "gpt-4o-mini" and os.getenv("OPENAI_MODEL"):
    settings.ai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
