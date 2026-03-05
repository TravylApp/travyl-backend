from fastapi import APIRouter, HTTPException

from app.models.schemas import ExtractionRequest, PromptExtract
from app.services.bedrock import BedrockExtractionError, get_bedrock_service

router = APIRouter(prefix="/api/trips", tags=["trips"])


@router.post("/extract", response_model=PromptExtract)
async def extract(request: ExtractionRequest):
    """Extract structured trip parameters from a natural language query."""
    try:
        return await get_bedrock_service().extract_trip_params(request)
    except BedrockExtractionError as e:
        raise HTTPException(status_code=502, detail=str(e))
