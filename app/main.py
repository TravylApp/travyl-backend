from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import events, flights, hotels, places, trips
from app.services.bedrock import warm_caches


@asynccontextmanager
async def lifespan(app: FastAPI):
    warm_caches()
    yield


app = FastAPI(title="Travyl Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(events.router)
app.include_router(flights.router)
app.include_router(hotels.router)
app.include_router(places.router)
app.include_router(trips.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
