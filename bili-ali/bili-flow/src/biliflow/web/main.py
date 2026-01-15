from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from biliflow.web.api import config, status, files, tasks
from biliflow.web.log_stream import log_stream

app = FastAPI(title="Bili-Flow Control Center", version="0.1.0")

# CORS (Allow all for local dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(config.router, prefix="/api")
app.include_router(status.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")

@app.get("/api/logs/stream")
async def stream_logs():
    """
    SSE Endpoint for real-time logs.
    """
    return EventSourceResponse(log_stream.event_generator())

@app.on_event("startup")
async def startup_event():
    from biliflow.utils.logger import log
    log.info("Bili-Flow System Started - Version: Fixed-Copyright-And-Cover")

@app.get("/")
async def root():
    return {"message": "Bili-Flow API is running"}
