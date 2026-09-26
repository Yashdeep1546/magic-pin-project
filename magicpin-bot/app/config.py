from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "Magicpin Vera Bot"
    APP_VERSION: str = "1.0.0"
    HOST: str = "0.0.0.0"
    PORT: int = 8080
    LOG_LEVEL: str = "INFO"

    # Team & Model Metadata for GET /v1/metadata
    TEAM_NAME: str = "Team Vera"
    TEAM_MEMBERS: List[str] = ["Yashdeep", "Vera Team"]
    MODEL: str = "gpt-4o"
    APPROACH: str = "deterministic trigger ranking + grounded LLM composition + output validation gate"
    CONTACT_EMAIL: str = "yashdeep.magicpin@gmail.com"
    LLM_TIMEOUT_SECONDS: float = 8.0


settings = Settings()
