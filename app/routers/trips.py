from fastapi import APIRouter, HTTPException

from app.schemas import ExtractionRequest, ExtractionResponse
from app.services.bedrock import BedrockExtractionError, extract as bedrock_extract

router = APIRouter(prefix="/api/trips", tags=["trips"])


@router.post("/extract", response_model=ExtractionResponse)
async def extract(request: ExtractionRequest):
    try:
        return await bedrock_extract(request)
    except BedrockExtractionError as e:
        raise HTTPException(status_code=502, detail=str(e))
