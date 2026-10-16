import json
import os
import tempfile
from pathlib import Path

import fitz
import opendataloader_pdf
import pymupdf4llm

from src.extraction.file_importer import ImportedPDF


class DocumentParsingError(Exception):
    """Raised when document parsing fails."""


def _parse_with_pymupdf(
    imported_pdf: ImportedPDF,
    output_directory: Path,
) -> None:
    """Pure Python PDF parsing fallback using PyMuPDF and PyMuPDF4LLM."""
    pdf_path = str(imported_pdf.staged_path)
    doc = fitz.open(pdf_path)
    num_pages = len(doc)

    try:
        markdown_text = pymupdf4llm.to_markdown(pdf_path)
    except Exception:
        markdown_text = "\n\n".join(page.get_text() for page in doc)

    md_file = output_directory / f"{imported_pdf.document_id}.md"
    md_file.write_text(markdown_text, encoding="utf-8")

    kids: list[dict] = []
    element_id = 1

    for page_idx, page in enumerate(doc):
        page_num = page_idx + 1

        # Extract tables if available
        try:
            tabs = page.find_tables()
            for tab in tabs.tables:
                rows_data = []
                for row in tab.extract():
                    cells_data = []
                    for col_idx, cell_value in enumerate(row):
                        cells_data.append({
                            "column number": col_idx,
                            "content": str(cell_value or "").strip(),
                        })
                    rows_data.append({"cells": cells_data})

                kids.append({
                    "id": element_id,
                    "type": "table",
                    "page number": page_num,
                    "bounding box": [float(x) for x in tab.bbox],
                    "content": None,
                    "rows": rows_data,
                })
                element_id += 1
        except Exception:
            pass

        # Extract text blocks
        blocks = page.get_text("blocks")
        for b in blocks:
            x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
            block_type = b[6] if len(b) > 6 else 0
            bbox = [float(x0), float(y0), float(x1), float(y1)]

            if block_type == 1:
                kids.append({
                    "id": element_id,
                    "type": "image",
                    "page number": page_num,
                    "bounding box": bbox,
                    "content": None,
                })
                element_id += 1
            else:
                stripped = text.strip()
                if not stripped:
                    continue

                lines = stripped.splitlines()
                is_heading = len(lines) == 1 and len(stripped) < 80 and (
                    stripped.isupper() or stripped.endswith(":") or stripped.startswith("#")
                )
                kids.append({
                    "id": element_id,
                    "type": "heading" if is_heading else "paragraph",
                    "page number": page_num,
                    "content": stripped,
                    "bounding box": bbox,
                })
                element_id += 1

    meta = doc.metadata or {}
    doc.close()

    json_data = {
        "number of pages": num_pages,
        "title": meta.get("title") or imported_pdf.original_filename,
        "author": meta.get("author") or "",
        "kids": kids,
    }

    json_file = output_directory / f"{imported_pdf.document_id}.json"
    json_file.write_text(
        json.dumps(json_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def parse_with_opendataloader(
    imported_pdf: ImportedPDF,
    output_root: str | Path | None = None,
) -> Path:
    if output_root is None:
        is_serverless = bool(
            os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME")
        )
        output_root = (
            os.path.join(tempfile.gettempdir(), "data", "output")
            if is_serverless
            else "data/output"
        )
    output_directory = (
        Path(output_root)
        / imported_pdf.document_id
        / "opendataloader"
    )
    try:
        output_directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        output_directory = (
            Path(tempfile.gettempdir())
            / "data"
            / "output"
            / imported_pdf.document_id
            / "opendataloader"
        )
        output_directory.mkdir(parents=True, exist_ok=True)

    try:
        opendataloader_pdf.convert(
            input_path=str(imported_pdf.staged_path),
            output_dir=str(output_directory),
            format="markdown,json",
        )
    except Exception as od_error:
        # OpenDataLoader requires Java which is not available in serverless runtimes.
        # Clean any partial artifacts and fall back to PyMuPDF.
        for existing in output_directory.glob("*"):
            try:
                if existing.is_file():
                    existing.unlink()
            except OSError:
                pass

        try:
            _parse_with_pymupdf(imported_pdf, output_directory)
        except Exception as fallback_error:
            raise DocumentParsingError(
                f"PDF parsing failed for {imported_pdf.original_filename}: "
                f"{od_error}; fallback: {fallback_error}"
            ) from fallback_error

    return output_directory.resolve()