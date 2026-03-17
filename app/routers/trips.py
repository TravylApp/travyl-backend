from fastapi import APIRouter, HTTPException

from app.pipeline.assembly import assemble
from app.pipeline.data_acquisition import acquire, compute_travel_matrix
from app.pipeline.scoring import score_and_filter
from app.pipeline.scheduler import schedule
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
    """Full 5-stage pipeline: extract → acquire → score → schedule → assemble."""
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

    # Stage 3: filter & score
    scored_pois = score_and_filter(ext, data)

    # travel matrix for scored POIs (needed by scheduler)
    pois_for_matrix = [poi for poi, _ in scored_pois]
    travel_matrix = await compute_travel_matrix(
        pois_for_matrix, mode=ext.travel_mode_preference,
    )

    # Stage 4: CP-SAT scheduler
    itinerary = schedule(scored_pois, ext, travel_matrix, data.weather, travel_mode=ext.travel_mode_preference)

    # Stage 5: response assembly
    return assemble(ext, data, itinerary)
