"""Configuration du projet, lue depuis l'environnement (et .env en local)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "openai"

    openai_model: str = "gpt-5.6-luna"
    # Vide = l'API d'OpenAI. Renseignée, elle vise une passerelle compatible.
    # Seules celles qui exposent `/responses` conviennent : c'est cette API que
    # le provider appelle, pas `/chat/completions`. Groq l'implémente ; Gemini
    # et OpenRouter s'arrêtent à `/chat/completions` et échoueraient en 404.
    openai_base_url: str | None = None
    # La famille GPT-5 refuse `temperature`. Ces deux leviers la remplacent ;
    # les épingler est ce qui rend l'éval du jalon 2 reproductible.
    openai_reasoning_effort: str = "low"
    openai_verbosity: str = "low"
    openai_timeout_s: float = 60.0
    openai_max_retries: int = 5

    database_url: str = "postgresql+psycopg://pokebuddy:pokebuddy@db:5432/pokebuddy"
    # Rôle distinct, sans aucun droit d'écriture, réservé à l'outil SQL du
    # jalon 3. C'est la seule protection qui tienne encore si la validation du
    # SQL généré se fait contourner — les autres couches sont des filtres, et un
    # filtre finit toujours par se contourner. Le rôle et son mot de passe sont
    # lus depuis cette URL, unique source de vérité (voir la migration f1a2c3).
    database_url_ro: str = "postgresql+psycopg://pokebuddy_ro:pokebuddy_ro@db:5432/pokebuddy"

    # Traçage. Absentes, le dépôt tourne à l'identique : voir src/obs/tracing.py,
    # où la garantie est portée par notre code et vérifiée par un test.
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    llm_cache_path: Path = Field(default=Path("~/.cache/pokebuddy/llm.sqlite"))
    pokeapi_cache_dir: Path = Field(default=Path("~/.cache/pokebuddy/pokeapi"))
    tcgdex_cache_dir: Path = Field(default=Path("~/.cache/pokebuddy/tcgdex"))
    # Le modèle ONNX de plongement, 220 Mo, téléchargé au premier usage.
    fastembed_cache_dir: Path = Field(default=Path("~/.cache/pokebuddy/fastembed"))

    @field_validator("openai_base_url", "langfuse_public_key", "langfuse_secret_key", mode="before")
    @classmethod
    def _blank_is_unset(cls, v: str | None) -> str | None:
        # `OPENAI_BASE_URL=` dans .env arrive comme chaîne vide, que le SDK
        # OpenAI n'accepte pas comme « valeur par défaut ». Une variable vide
        # veut dire non renseignée — c'est aussi ce qui fait que
        # `LANGFUSE_PUBLIC_KEY= make eval` éteint réellement le traçage.
        return v or None

    @field_validator(
        "llm_cache_path", "pokeapi_cache_dir", "tcgdex_cache_dir", "fastembed_cache_dir"
    )
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return v.expanduser()


@lru_cache
def get_settings() -> Settings:
    return Settings()
