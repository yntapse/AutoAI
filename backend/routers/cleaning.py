import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from etl_copilot.copilot_service import CopilotError
from routers.etl_copilot import (
    EtlCopilotConnection,
    _connection_string,
    _jsonable,
    _raise_for,
    _resolved_schema,
    get_copilot,
)
from services.data_cleaner import (
    CleaningValidationError,
    apply_cleaning_steps,
    validate_cleaning_steps,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cleaning", tags=["data-cleaning"])


class CleaningPreviewRequest(EtlCopilotConnection):
    user_request: str = Field(..., min_length=1)
    project_name: str = Field(default="Cleaning preview", min_length=1, max_length=255)
    target_column: Optional[str] = None
    cleaning_steps: List[Dict[str, Any]] = Field(default_factory=list)


class CleaningPreviewResponse(BaseModel):
    original_row_count: int
    new_row_count: int
    rows_removed: int
    columns_changed: List[str]
    applied_steps: List[Dict[str, Any]]
    sample_rows: List[Dict[str, Any]]


@router.post("/preview", response_model=CleaningPreviewResponse)
def preview_cleaning(request: CleaningPreviewRequest):
    """Preview cleaning operations without creating or modifying a dataset."""
    try:
        validate_cleaning_steps(request.cleaning_steps)
        dataset = get_copilot().create_dataset(
            user_request=request.user_request,
            connection_string=_connection_string(request),
            schema=_resolved_schema(request),
            dataset_name=request.project_name,
            persist=False,
        )
        frame = dataset.pop("frame", None)
        if frame is None:
            raise HTTPException(
                status_code=500,
                detail="The ETL preview did not return rows for cleaning.",
            )
        cleaned, summary = apply_cleaning_steps(frame, request.cleaning_steps)
        return _jsonable(
            {
                **summary,
                "sample_rows": cleaned.head(20).to_dict(orient="records"),
            }
        )
    except CleaningValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except CopilotError as exc:
        _raise_for(exc)
    except Exception as exc:
        logger.exception("Failed to preview cleaning steps")
        raise HTTPException(
            status_code=500,
            detail="Could not preview the cleaning pipeline.",
        ) from exc
