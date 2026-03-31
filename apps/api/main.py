"""FastAPI app for the crossword blog API."""

import sys
from pathlib import Path

# Allow running from repo root: python -m apps.api.main
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI

from apps.api.routers import puzzles, posts
from shared.clients.postgres import get_conn

app = FastAPI(title="Clue Gun API", version="0.1.0")

app.include_router(puzzles.router)
app.include_router(posts.router)


@app.get("/health")
def health():
    """Liveness check — also verifies DB connectivity."""
    conn = get_conn()
    conn.close()
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("apps.api.main:app", host="0.0.0.0", port=8000, reload=True)
