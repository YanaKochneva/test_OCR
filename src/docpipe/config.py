from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_file_mb: int = Field(default=200, gt=0)
    max_pages: int = Field(default=300, gt=0)
    max_pixels_per_page: int = Field(default=40_000_000, gt=0)
    max_dimension_px: int = Field(default=12_000, gt=0)


class TextLayerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_scripts: list[str] = ["cyrillic", "latin"]
    min_text_length: int = Field(default=20, ge=0)
    min_expected_script_ratio: float = Field(default=0.35, ge=0, le=1)
    max_control_ratio: float = Field(default=0.05, ge=0, le=1)
    max_replacement_ratio: float = Field(default=0.01, ge=0, le=1)
    max_mixed_script_word_ratio: float = Field(default=0.25, ge=0, le=1)


class FigureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    padding_px: int = Field(default=8, ge=0)
    merge_iou: float = Field(default=0.2, ge=0, le=1)
    min_width_px: int = Field(default=32, ge=1)
    min_height_px: int = Field(default=32, ge=1)
    min_area_ratio: float = Field(default=0.001, ge=0, le=1)
    table_fallback_to_figure: bool = True


class RenderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_page_markers: bool = True
    headers_footers: Literal["keep", "drop"] = "drop"
    positional: bool = False


class ApiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workers: int = Field(default=1, ge=1, le=64)
    max_queue: int = Field(default=16, ge=1)
    max_jobs: int = Field(default=1000, ge=1)
    result_ttl_seconds: int = Field(default=3600, ge=60)
    work_dir: Path = Path("data/jobs")
    default_engine: str = "docling"
    sync_max_pages: int = Field(default=10, ge=1)
    sync_timeout_seconds: int = Field(default=600, ge=1)
    shutdown_timeout_seconds: int = Field(default=300, ge=1)
    require_api_key: bool = False
    api_key: str | None = Field(default=None, min_length=32)


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOCPIPE_", env_nested_delimiter="__", extra="ignore")
    force_ocr: Literal["auto", "always", "never"] = "auto"
    default_dpi: int = Field(default=300, gt=0)
    profile: Literal["fast", "balanced", "accurate"] = "balanced"
    gpu: bool | None = None
    offline: bool = True
    limits: LimitsConfig = LimitsConfig()
    text_layer: TextLayerConfig = TextLayerConfig()
    figures: FigureConfig = FigureConfig()
    render: RenderConfig = RenderConfig()
    debug: bool = False
    api: ApiConfig = ApiConfig()


def load_config(path: Path | None = None) -> AppConfig:
    """Загрузить YAML и наложить переменные окружения через pydantic-settings."""
    if path is None:
        return AppConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Корень YAML-конфига должен быть объектом")
    return AppConfig(**raw)
