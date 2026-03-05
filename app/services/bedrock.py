import asyncio
from datetime import date
from functools import lru_cache

import boto3

from app.config import settings
from app.models.schemas import ExtractionRequest, PromptExtract

_TOOL_NAME = "extract_trip_params"

_SYSTEM_PROMPT = """\
You are a part of Travyl, an AI-powered travel itinerary planner. You will receive \
a natural language itinerary request from a user, and you need to extract meaningful, \
structured information from their query. DO NOT HALLUCINATE.

Your goal is to parse the user's natural language input and extract the following information:

REQUIRED FIELDS:
* from_location: Starting location/city OR THE USERS CURRENT LOCATION (CURRENT LOCATION: {city}, {country}) <-- IMPORTANT! ***
* to_location: Destination city/location
* from_date: Trip start date (extract as YYYY-MM-DD, or THE USERS CURRENT DATE (TODAY: {today})) <-- IMPORTANT! ***
* to_date: Trip end date (extract as YYYY-MM-DD, or "Not specified")
* num_travelers: Number of people traveling (default to 1 if not mentioned)

OPTIONAL FIELDS (extract if mentioned, otherwise return null):
* budget_amount: Explicit dollar/currency amount for the entire trip
* themes: Travel themes like ["history", "food", "adventure", "nightlife", "culture", "relaxation"]
* interests: Specific interests like ["museums", "hiking", "beaches", "shopping", "wine tasting"]
* dietary_restrictions: Any food restrictions ["vegetarian", "vegan", "gluten-free", "halal", "kosher"]
* cuisine_preferences: Preferred food types ["italian", "local", "seafood", "fine dining"]
* transportation_mode: Preferred travel method ("flight", "car", "train", "bus", "any")
* needs_rental_car: Whether they need a rental car (true/false)
* accommodation_type: Lodging preference ("hotel", "airbnb", "hostel", "resort", "any")
* pace_preference: Trip intensity ("relaxed", "moderate", "packed")
* must_see_locations: Specific places/attractions they mentioned wanting to visit
* avoid_locations: Places or types of places they want to avoid
* occasion: Special reason for travel ("honeymoon", "business", "anniversary", "birthday", "graduation"). YOU CAN ASSUME VACATION AS DEFAULT.

IMPORTANT INSTRUCTIONS:
- Be intelligent about inference: if someone says "romantic getaway", infer occasion="honeymoon" or "anniversary"
- Identify themes from activity mentions: "museums" and "historical sites" -> themes include "history" and "culture"
- For dates, handle relative expressions
- Default num_travelers to 1 unless explicitly stated or implied (e.g., "we", "us", "couple" -> 2)

Use the extract_trip_params tool to return the structured data."""


class BedrockExtractionError(Exception):
    """Raised when Bedrock fails to return a valid extraction."""


class BedrockService:
    """Wraps AWS Bedrock calls for trip parameter extraction."""

    def __init__(self) -> None:
        self._client = None
        self._tool_config = {
            "tools": [
                {
                    "toolSpec": {
                        "name": _TOOL_NAME,
                        "description": "Extract structured trip parameters from the user's natural language query.",
                        "inputSchema": {
                            "json": PromptExtract.model_json_schema(),
                        },
                    }
                }
            ],
            "toolChoice": {"tool": {"name": _TOOL_NAME}},
        }

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client(
                "bedrock-runtime", region_name=settings.aws_region
            )
        return self._client

    def _converse(self, system_text: str, query: str) -> dict:
        """Synchronous Bedrock Converse call (runs in a thread)."""
        return self.client.converse(
            modelId=settings.bedrock_model_id,
            system=[{"text": system_text}],
            messages=[{"role": "user", "content": [{"text": query}]}],
            toolConfig=self._tool_config,
            inferenceConfig={"temperature": 0.1},
        )

    async def extract_trip_params(self, request: ExtractionRequest) -> PromptExtract:
        """Extract structured trip parameters from a natural language query."""
        city = request.city or "Unknown"
        country = request.country or "Unknown"
        today = date.today().isoformat()
        system_text = _SYSTEM_PROMPT.format(city=city, country=country, today=today)

        try:
            response = await asyncio.to_thread(
                self._converse, system_text, request.query
            )
        except Exception as e:
            raise BedrockExtractionError(f"Bedrock request failed: {e}") from e

        for block in response.get("output", {}).get("message", {}).get("content", []):
            if "toolUse" in block:
                return PromptExtract.model_validate(block["toolUse"]["input"])

        raise BedrockExtractionError(
            "Bedrock response did not contain a tool use block"
        )


@lru_cache(maxsize=1)
def get_bedrock_service() -> BedrockService:
    return BedrockService()
