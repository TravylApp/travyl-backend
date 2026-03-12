from fastapi import APIRouter, HTTPException

from app.pipeline.data_acquisition import acquire
from app.schemas import ExtractionRequest, ExtractionResponse, PlanResponse
from app.services.bedrock import BedrockExtractionError, extract as bedrock_extract

router = APIRouter(prefix="/api/trips", tags=["trips"])


@router.post("/extract", response_model=ExtractionResponse)
async def extract(request: ExtractionRequest):
    try:
        return await bedrock_extract(request)
    except BedrockExtractionError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/plan", response_model=PlanResponse)
async def plan(request: ExtractionRequest):
    """Stage 1 (extract) → Stage 2 (acquire) pipeline."""
    try:
        extraction_result = await bedrock_extract(request)
    except BedrockExtractionError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if extraction_result.status == "needs_clarification":
        return PlanResponse(
            status="needs_clarification",
            extracted=extraction_result.extracted,
            questions=extraction_result.questions,
        )

    ext = extraction_result.extracted

    # Stage 2: data acquisition (parallel API fan-out)
    data = await acquire(ext, origin_city=request.city, origin_country=request.country)

    return PlanResponse(
        status="complete",
        extracted=ext,
        data=data,
    )
