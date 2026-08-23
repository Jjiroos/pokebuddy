"""Configuration du projet, lue depuis l'environnement (et .env en local)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "openai"

    openai_model: str = "gpt-5.6-luna"
    # La famille GPT-5 refuse `temperature`. Ces deux leviers la remplacent ;
    # les épingler est ce qui rend l'éval du jalon 2 reproductible.
    openai_reasoning_effort: str = "low"
    openai_verbosity: str = "low"
    openai_timeout_s: float = 60.0
    openai_max_retries: int = 5

    database_url: str = "postgresql+psycopg://pokebuddy:pokebuddy@db:5432/pokebuddy"

    llm_cache_path: Path = Field(default=Path("~/.cache/pokebuddy/llm.sqlite"))
    pokeapi_cache_dir: Path = Field(default=Path("~/.cache/pokebuddy/pokeapi"))

    @field_validator("llm_cache_path", "pokeapi_cache_dir")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return v.expanduser()


@lru_cache
def get_settings() -> Settings:
    return Settings()
