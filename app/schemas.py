from typing import Literal

from pydantic import BaseModel, Field


class ExtractionRequest(BaseModel):
    prompt: str
    lat: float | None = None
    lng: float | None = None
    city: str | None = None
    country: str | None = None
    answers: dict[str, str] = {}


# --- Nested models ---
# All fields are structured types that downstream APIs can act on.
# No freeform strings — if it can't be parsed, it doesn't belong here.

class Destination(BaseModel):
    city: str
    country: str
    region: str | None = None
    lat: float = 0.0
    lng: float = 0.0


class TripDates(BaseModel):
    start: str | None = None
    end: str | None = None
    flexible: bool = False


class Travelers(BaseModel):
    count: int = 1
    composition: Literal["solo", "couple", "friends", "family", "group"] | None = None
    occasion: Literal[
        "honeymoon", "anniversary", "birthday", "bachelor_party",
        "graduation", "conference", "vacation",
    ] | None = None


class Meals(BaseModel):
    include_in_itinerary: bool | None = None
    cuisine_preferences: list[str] = []
    avoid_cuisines: list[str] = []


class Accommodation(BaseModel):
    type: Literal["hotel", "hostel", "airbnb", "resort", "boutique", "camping"] | None = None
    booked: bool = False


class Constraints(BaseModel):
    arrival_time: str | None = None
    departure_time: str | None = None
    daily_start_time: str | None = None
    daily_end_time: str | None = None
    must_visit: list[str] = []
    must_avoid: list[str] = []
    pre_booked: list[str] = []
    accessibility_needs: list[str] = []
    dietary_restrictions: list[str] = []
    mobility_level: Literal["full", "limited", "wheelchair"] | None = None
    avoid_categories: list[str] = []
    crowd_tolerance: Literal["low", "moderate", "high"] | None = None


class TripExtraction(BaseModel):
    destination: Destination
    dates: TripDates
    duration_days: int = 0
    travelers: Travelers = Field(default_factory=Travelers)
    interests: list[str] = []
    budget_level: Literal["budget", "moderate", "comfortable", "luxury"] | None = None
    daily_estimate_usd: int = 0
    pace: Literal["relaxed", "moderate", "packed"] | None = None
    meals: Meals = Field(default_factory=Meals)
    accommodation: Accommodation = Field(default_factory=Accommodation)
    constraints: Constraints = Field(default_factory=Constraints)
    travel_mode_preference: Literal["walking", "public_transit", "rental_car", "rideshare", "cycling"] | None = None
    evening_preference: Literal["nightlife", "dining", "relaxing", "early_sleep"] | None = None


class ExtractionResponse(BaseModel):
    status: Literal["complete", "needs_clarification"]
    extracted: TripExtraction | None = None
    questions: list[dict] = []
