from fastapi import APIRouter, HTTPException, Query

from app.pipeline.assembly import assemble
from app.pipeline.data_acquisition import acquire, compute_travel_matrix
from app.pipeline.scoring import score_and_filter
from app.pipeline.scheduler import schedule
from app.schemas import ExtractionRequest, ExtractionResponse, PlanResponse
from app.services.bedrock import BedrockExtractionError, extract as bedrock_extract
from app.services.images import get_place_image

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
    itinerary = schedule(scored_pois, ext, travel_matrix, data.weather)

    # Stage 5: response assembly
    return assemble(ext, data, itinerary)


@router.get("/places/image")
async def place_image(
    name: str = Query(..., description="Place name (e.g. 'Colosseum')"),
    city: str | None = Query(None, description="City for context (e.g. 'Rome')"),
):
    """Fetch the best image for a place using SerpAPI + AI selection."""
    result = await get_place_image(name, city)
    if not result:
        raise HTTPException(status_code=404, detail="No images found")
    return result
