"""Normalize scan PDF units to A4 without changing stored OCR coordinates."""

from docpipe.ir import BBox

A4_WIDTH = 210 * 72 / 25.4
A4_HEIGHT = 297 * 72 / 25.4


def a4_document(document):
    pages = []
    for page in document.pages:
        width, height = (
            (A4_HEIGHT, A4_WIDTH) if page.width_pt > page.height_pt else (A4_WIDTH, A4_HEIGHT)
        )
        scale = min(width / page.width_pt, height / page.height_pt)
        dx, dy = (width - page.width_pt * scale) / 2, (height - page.height_pt * scale) / 2

        def box(b):
            return BBox(
                x0=b.x0 * scale + dx,
                y0=b.y0 * scale + dy,
                x1=b.x1 * scale + dx,
                y1=b.y1 * scale + dy,
            )

        def lines(items):
            return (
                [
                    line.model_copy(update={"bbox": box(line.bbox), "runs": lines(line.runs)})
                    for line in items
                ]
                if items
                else None
            )

        blocks = []
        for block in page.blocks:
            update = {"bbox": box(block.bbox), "lines": lines(block.lines)}
            if block.table:
                update["table"] = block.table.model_copy(
                    update={
                        "cells": [
                            cell.model_copy(
                                update={"bbox": box(cell.bbox), "lines": lines(cell.lines)}
                            )
                            for cell in block.table.cells
                        ]
                    }
                )
            blocks.append(block.model_copy(update=update))
        pages.append(
            page.model_copy(update={"width_pt": width, "height_pt": height, "blocks": blocks})
        )
    return document.model_copy(update={"pages": pages})
