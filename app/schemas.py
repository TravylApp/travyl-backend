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




# --- Stage 2: Data Acquisition output models ---

class POI(BaseModel):
    id: str
    name: str
    lat: float
    lng: float
    category: str
    subcategory: str
    rating: float | None = None
    review_count: int | None = None
    price_level: int | None = None
    opening_hours: dict[str, str] | None = None
    description: str | None = None
    photo_url: str | None = None
    website: str | None = None
    visit_duration_min: int = 60
    cuisine: str | None = None
    tags: list[str] = []
    source: Literal["osm", "serpapi"] = "osm"


class Event(BaseModel):
    id: str
    name: str
    date: str
    time: str | None = None
    venue: str | None = None
    lat: float | None = None
    lng: float | None = None
    description: str | None = None
    price: str | None = None
    category: str | None = None
    photo_url: str | None = None
    link: str | None = None


class HotelOption(BaseModel):
    name: str
    price_per_night: float | None = None
    total_price: float | None = None
    currency: str = "USD"
    rating: float | None = None
    review_count: int | None = None
    stars: int | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    photo_url: str | None = None
    amenities: list[str] = []
    link: str | None = None


class FlightOption(BaseModel):
    airline: str
    flight_number: str | None = None
    departure_airport: str
    arrival_airport: str
    departure_time: str
    arrival_time: str
    duration_min: int
    price: float | None = None
    currency: str = "USD"
    stops: int = 0
    layovers: list[str] = []


class DayWeather(BaseModel):
    date: str
    temp_high_c: float
    temp_low_c: float
    precipitation_mm: float = 0.0
    precipitation_prob: float = 0.0
    condition: str = "clear"
    wind_speed_kmh: float = 0.0


class AcquisitionResult(BaseModel):
    pois: list[POI] = []
    events: list[Event] = []
    hotels: list[HotelOption] = []
    flights: list[FlightOption] = []
    weather: list[DayWeather] = []
    destination_photo_url: str | None = None


# --- Stage 4: Scheduler output models ---

class ScheduleSlot(BaseModel):
    poi: POI
    start_time: str  # "09:00"
    end_time: str  # "10:30"
    start_time_12h: str = ""  # "9:00 AM"
    end_time_12h: str = ""  # "10:30 AM"
    travel_from_prev_min: int = 0


class DayPlan(BaseModel):
    day: int
    date: str
    slots: list[ScheduleSlot] = []
    weather: DayWeather | None = None


# --- Final pipeline response ---

class PlanResponse(BaseModel):
    status: Literal["complete", "needs_clarification"]
    extracted: TripExtraction | None = None
    questions: list[dict] = []
    data: AcquisitionResult | None = None
    itinerary: list[DayPlan] = []
    hotels: list[HotelOption] = []
    flights: list[FlightOption] = []
    destination_photo_url: str | None = None
