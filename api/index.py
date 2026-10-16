"""
CatalogFlow - FastAPI web application entry point.
Provides a REST API and serves the HTML UI for PDF upload and extraction.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.workflow.catalog_graph import build_catalog_graph

app = FastAPI(
    title="CatalogFlow",
    description=(
        "Upload product catalog and brochure PDFs to extract structured "
        "product, commercial, and packaging data."
    ),
    version="1.0.0",
)

_static_dir = os.path.join(os.path.dirname(__file__), "..", "static")

if os.path.isdir(_static_dir):
    app.mount(
        "/static",
        StaticFiles(directory=_static_dir),
        name="static",
    )


@app.get("/", include_in_schema=False)
async def serve_ui() -> FileResponse:
    index_path = os.path.join(_static_dir, "index.html")
    if not os.path.isfile(index_path):
        raise HTTPException(status_code=404, detail="UI not found")
    return FileResponse(index_path)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "CatalogFlow"}


@app.post("/api/extract")
async def extract(file: UploadFile = File(...)) -> JSONResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        graph = build_catalog_graph()
        initial_state: dict[str, Any] = {
            "event_type": "new_document",
            "run_id": str(uuid4()),
            "pdf_path": tmp_path,
            "processing_status": "received",
            "warnings": [],
        }
        final_state = graph.invoke(initial_state)
        return JSONResponse(
            content={
                "run_id": final_state.get("run_id"),
                "document_id": final_state.get("document_id"),
                "document_type": final_state.get("document_type", "unknown"),
                "classification_reason": final_state.get("classification_reason"),
                "classification_confidence": final_state.get("classification_confidence", 0.0),
                "page_count": final_state.get("page_count", 0),
                "processing_status": final_state.get("processing_status"),
                "warnings": final_state.get("warnings", []),
                "vendor": final_state.get("vendor_result"),
                "products": final_state.get("ppv_results", []),
                "ppv_valid": final_state.get("ppv_valid"),
                "ppv_validation_warnings": final_state.get("ppv_validation_warnings", []),
                "vlm_results": final_state.get("vlm_results"),
                "vlm_valid": final_state.get("vlm_valid"),
                "visual_review_required": final_state.get("visual_review_required"),
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        os.unlink(tmp_path)
