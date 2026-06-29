from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CALBAR_", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://calbar:calbar@localhost:5432/calbar_tutor",
        validation_alias="DATABASE_URL",
    )
    data_dir: Path = Path("data")
    user_agent: str = "CalBarExamTutor/0.1 (+local research; contact: local)"
    past_exams_url: str = "https://www.calbar.ca.gov/admissions/applicant-resources/past-exams"
    downloader_timeout_seconds: float = 30.0
    downloader_rate_limit_seconds: float = 0.5
    parser_version: str = "deterministic-v1"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:31b-cloud"
    analysis_provider: str = "ollama"  # "ollama" or "mock"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def extracted_dir(self) -> Path:
        return self.data_dir / "extracted"

    @property
    def parsed_dir(self) -> Path:
        return self.data_dir / "parsed"

    @property
    def manifest_dir(self) -> Path:
        return self.data_dir / "manifests"


@lru_cache
def get_settings() -> Settings:
    return Settings()

