import asyncio
import os
import uuid
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from spotify import fetch_tracks
from downloader import download_playlist, FORMAT_OPTIONS

app = FastAPI(title="Spotify Playlist Downloader")

BASE_DIR = Path(__file__).parent.resolve()
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

TEMP_DIR = Path(__file__).parent.parent / "downloads_temp"
TEMP_DIR.mkdir(exist_ok=True)

jobs: dict[str, dict] = {}
JOB_TTL = 3600


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    formats = [{"key": k, "label": v["label"]} for k, v in FORMAT_OPTIONS.items()]
    return TEMPLATES.TemplateResponse(
        request, "index.html", {"formats": formats}
    )


@app.post("/api/playlist")
async def get_playlist(data: dict):
    url = data.get("url", "").strip()
    if not url:
        raise HTTPException(400, "URL is required")
    try:
        playlist_name, tracks = await asyncio.to_thread(fetch_tracks, url)
        return {
            "name": playlist_name,
            "total": len(tracks),
            "tracks": [t.to_dict() for t in tracks],
        }
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/download")
async def start_download(data: dict):
    url = data.get("url", "").strip()
    fmt_key = data.get("format", "mp3_320")
    if not url:
        raise HTTPException(400, "URL is required")
    if fmt_key not in FORMAT_OPTIONS:
        raise HTTPException(400, f"Invalid format: {fmt_key}")

    try:
        playlist_name, tracks = await asyncio.to_thread(fetch_tracks, url)
    except Exception as e:
        raise HTTPException(400, str(e))

    job_id = uuid.uuid4().hex[:12]
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in playlist_name)[:50]
    job_dir = TEMP_DIR / job_id
    song_dir = job_dir / safe_name

    jobs[job_id] = {
        "status": "downloading",
        "playlist_name": playlist_name,
        "total": len(tracks),
        "completed": 0,
        "failed": 0,
        "current_index": 0,
        "current_title": "",
        "track_status": [None] * len(tracks),
        "zip_path": None,
        "error": None,
        "created_at": time.time(),
    }

    asyncio.create_task(_run_download(job_id, tracks, song_dir, fmt_key))
    return {"job_id": job_id, "total": len(tracks)}


async def _run_download(job_id, tracks, song_dir, fmt_key):
    job = jobs[job_id]

    def on_start(idx, track):
        job["current_index"] = idx
        job["current_title"] = track.title

    def on_done(idx, track, path):
        if path:
            job["track_status"][idx - 1] = "done"
            job["completed"] += 1
        else:
            job["track_status"][idx - 1] = "failed"
            job["failed"] += 1

    try:
        loop = asyncio.get_event_loop()
        zip_path = await loop.run_in_executor(
            None,
            lambda: download_playlist(tracks, song_dir, fmt_key, on_start, on_done),
        )
        job["zip_path"] = str(zip_path)
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return JSONResponse({
        "status": job["status"],
        "playlist_name": job["playlist_name"],
        "total": job["total"],
        "completed": job["completed"],
        "failed": job["failed"],
        "current_index": job["current_index"],
        "current_title": job["current_title"],
        "track_status": job["track_status"],
        "error": job["error"],
    })


@app.get("/api/file/{job_id}")
async def download_file(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "done" or not job["zip_path"]:
        raise HTTPException(400, "Download not ready")
    zip_path = Path(job["zip_path"])
    if not zip_path.exists():
        raise HTTPException(404, "File not found")
    filename = f"{job['playlist_name']}.zip"
    return FileResponse(
        str(zip_path),
        media_type="application/zip",
        filename=filename,
    )


@app.on_event("startup")
async def cleanup_loop():
    async def _cleanup():
        while True:
            await asyncio.sleep(300)
            now = time.time()
            to_remove = [
                jid for jid, j in jobs.items()
                if now - j.get("created_at", 0) > JOB_TTL and j["status"] in ("done", "error")
            ]
            for jid in to_remove:
                job = jobs.pop(jid, None)
                if job and job.get("zip_path"):
                    p = Path(job["zip_path"])
                    if p.exists():
                        p.unlink(missing_ok=True)
                    parent = p.parent
                    if parent != TEMP_DIR:
                        shutil.rmtree(parent, ignore_errors=True)

    asyncio.create_task(_cleanup())


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
