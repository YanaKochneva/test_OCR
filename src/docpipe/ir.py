from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SourceType(StrEnum):
    PDF = "pdf"
    IMAGE = "image"


class PageStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


class TextLayerStatus(StrEnum):
    VALID = "valid"
    ABSENT = "absent"
    GARBAGE = "garbage"


class BlockType(StrEnum):
    TEXT = "text"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FIGURE = "figure"
    CAPTION = "caption"
    FORMULA = "formula"
    HEADER = "header"
    FOOTER = "footer"
    PAGE_NUMBER = "page_number"
    FOOTNOTE = "footnote"
    OTHER = "other"


class BBox(StrictModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def valid_geometry(self) -> "BBox":
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("Некорректная рамка: x1/y1 должны быть не меньше x0/y0")
        return self


class Line(StrictModel):
    text: str
    bbox: BBox


class TableCell(StrictModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    rowspan: int = Field(default=1, ge=1)
    colspan: int = Field(default=1, ge=1)
    text: str
    bbox: BBox


class TableData(StrictModel):
    html: str | None = None
    cells: list[TableCell] = Field(default_factory=list)
    n_rows: int = Field(default=0, ge=0)
    n_cols: int = Field(default=0, ge=0)


class FigureData(StrictModel):
    file: str
    format: str
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    caption_id: str | None = None


class Block(StrictModel):
    id: str
    type: BlockType
    bbox: BBox
    order: int = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    text: str | None = None
    lines: list[Line] | None = None
    level: int | None = Field(default=None, ge=1)
    table: TableData | None = None
    figure: FigureData | None = None
    source_label: str | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "Block":
        if self.type == BlockType.FIGURE:
            if self.figure is None:
                raise ValueError("figure-блок обязан содержать figure")
            if self.text is not None:
                raise ValueError("Поле text у figure всегда должно быть None")
        if self.type == BlockType.TABLE and self.table is None:
            raise ValueError("table-блок обязан содержать table")
        return self


class ModelInfo(StrictModel):
    role: str
    name: str
    weights_sha256: str | None = None


class EngineInfo(StrictModel):
    name: str
    version: str
    models: list[ModelInfo] = Field(default_factory=list)


class Page(StrictModel):
    index: int = Field(ge=0)
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)
    rotation: int = Field(default=0)
    raster_dpi: int = Field(gt=0)
    size_px: tuple[int, int]
    status: PageStatus
    text_layer: TextLayerStatus
    blocks: list[Block] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_blocks(self) -> "Page":
        orders = [b.order for b in self.blocks]
        if len(orders) != len(set(orders)):
            raise ValueError("order блоков страницы должен быть уникальным")
        for block in self.blocks:
            if not (0 <= block.bbox.x0 <= block.bbox.x1 <= self.width_pt):
                raise ValueError(f"bbox блока {block.id} выходит за ширину страницы")
            if not (0 <= block.bbox.y0 <= block.bbox.y1 <= self.height_pt):
                raise ValueError(f"bbox блока {block.id} выходит за высоту страницы")
        return self


class SourceInfo(StrictModel):
    file: str
    sha256: str
    type: SourceType


class Document(StrictModel):
    schema_version: str = "1.0"
    source: SourceInfo
    engine: EngineInfo
    pages: list[Page] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)
