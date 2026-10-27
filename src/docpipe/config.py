from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_file_mb: int = Field(default=200, gt=0)
    max_pages: int = Field(default=300, gt=0)
    max_pixels_per_page: int = Field(default=40_000_000, gt=0)
    max_dimension_px: int = Field(default=12_000, gt=0)


class TextLayerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_scripts: list[str] = Field(default_factory=lambda: ["cyrillic", "latin"])
    min_text_length: int = Field(default=20, ge=0)
    min_expected_script_ratio: float = Field(default=0.35, ge=0, le=1)
    max_control_ratio: float = Field(default=0.05, ge=0, le=1)
    max_replacement_ratio: float = Field(default=0.01, ge=0, le=1)
    max_mixed_script_word_ratio: float = Field(default=0.25, ge=0, le=1)


class RenderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_page_markers: bool = True
    headers_footers: Literal["keep", "drop"] = "keep"
    correct_page_orientation: bool = True
    docx_enabled: bool = False
    unreadable_as_image: bool = True
    unreadable_min_confidence: float = Field(default=0.7, ge=0, le=1)


class ReviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_domains: list[str] = Field(default_factory=lambda: ["polyus.com"])
    llm_enabled: bool = False
    provider: Literal["aitunnel", "nemotron"] = "nemotron"
    nemotron_model: Path = Path("models/nemotron-3.5-parse")
    nemotron_device: Literal["auto", "cpu", "cuda"] = "auto"
    reread_dpi: int = Field(default=220, ge=150, le=400)
    reread_min_chars: int = Field(default=20, ge=1, le=500)
    api_model: str = "gpt-4.1-mini"
    timeout_seconds: int = Field(default=45, ge=1, le=300)
    max_calls: int = Field(default=40, ge=2, le=600)
    batch_chars: int = Field(default=10000, ge=200, le=12000)
    photo_max_pages: int = Field(default=20, ge=1, le=300)
    photo_max_side: int = Field(default=1600, ge=800, le=2400)
    stage_timeout_seconds: int = Field(default=180, ge=1, le=900)


class ApiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workers: int = Field(default=1, ge=1, le=64)
    max_queue: int = Field(default=16, ge=1)
    max_jobs: int = Field(default=1000, ge=1)
    result_ttl_seconds: int = Field(default=3600, ge=60)
    work_dir: Path = Path("data/jobs")
    default_engine: str = "docling"
    max_batch_files: int = Field(default=16, ge=1, le=100)
    max_batch_mb: int = Field(default=200, ge=1)
    sync_max_pages: int = Field(default=300, ge=1)
    sync_timeout_seconds: int = Field(default=600, ge=1)
    shutdown_timeout_seconds: int = Field(default=300, ge=1)
    require_api_key: bool = False
    api_key: str | None = Field(default=None, min_length=32)

    @model_validator(mode="after")
    def validate_authentication(self) -> "ApiConfig":
        if self.require_api_key and not self.api_key:
            raise ValueError("api_key is required when require_api_key is enabled")
        return self


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOCPIPE_", env_nested_delimiter="__", extra="forbid"
    )
    image_ocr: Literal["direct", "docling"] = "direct"
    default_dpi: int = Field(default=300, gt=0)
    offline: bool = True
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    text_layer: TextLayerConfig = Field(default_factory=TextLayerConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)


class _EnvironmentFirstConfig(AppConfig):
    """Validate YAML and environment together, with environment taking priority."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return env_settings, init_settings, dotenv_settings, file_secret_settings


def load_config(path: Path | None = None) -> AppConfig:
    """Load YAML and environment, then validate the combined configuration once."""
    if path is None:
        return AppConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Корень YAML-конфига должен быть объектом")
    return _EnvironmentFirstConfig(**raw)
