from pydantic import BaseModel


class ExtractionRequest(BaseModel):
    """Request body for the trip extraction endpoint."""
    query: str
    city: str | None = None
    country: str | None = None


class PromptExtract(BaseModel):
    """Structured trip parameters extracted from a natural language query."""
    from_location: str
    to_location: str
    from_date: str
    to_date: str
    num_travelers: int

    # Budget & Preferences
    budget_amount: str | None = None

    # Activity preferences & themes
    themes: list[str] | None = None
    interests: list[str] | None = None

    # Dining preferences
    dietary_restrictions: list[str] | None = None
    cuisine_preferences: list[str] | None = None

    # Transportation
    transportation_mode: str | None = None
    needs_rental_car: bool | None = None

    # Accommodation
    accommodation_type: str | None = None

    # Constraints & requirements
    pace_preference: str | None = None
    must_see_locations: list[str] | None = None
    avoid_locations: list[str] | None = None

    # Special occasions
    occasion: str | None = None
