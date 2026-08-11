import asyncio
import os
import threading
import uuid
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from spotify import fetch_tracks, fetch_single_track, is_spotify_track_url
from downloader import (
    download_playlist,
    download_cart,
    download_track_by_url,
    search_youtube,
    FORMAT_OPTIONS,
)

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
    limit = data.get("limit")
    if not url:
        raise HTTPException(400, "URL is required")
    try:
        if is_spotify_track_url(url):
            track = await asyncio.to_thread(fetch_single_track, url)
            tracks = [track]
            playlist_name = track.title
            cover_url = track.cover_url
        else:
            playlist_name, cover_url, tracks = await asyncio.to_thread(fetch_tracks, url)
        if limit and isinstance(limit, int) and limit > 0:
            tracks = tracks[:limit]
        return {
            "name": playlist_name,
            "cover_url": cover_url,
            "total": len(tracks),
            "tracks": [t.to_dict() for t in tracks],
        }
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/search")
async def search_tracks(data: dict):
    query = data.get("query", "").strip()
    if not query:
        raise HTTPException(400, "Search query is required")
    try:
        results = await asyncio.to_thread(search_youtube, query, 10)
        return {"query": query, "total": len(results), "results": results}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/download")
async def start_download(data: dict):
    url = data.get("url", "").strip()
    fmt_key = data.get("format", "mp3_320")
    mode = data.get("mode", "zip")
    limit = data.get("limit")
    if not url:
        raise HTTPException(400, "URL is required")
    if fmt_key not in FORMAT_OPTIONS:
        raise HTTPException(400, f"Invalid format: {fmt_key}")
    if mode not in ("zip", "individual"):
        raise HTTPException(400, "Invalid mode")

    try:
        playlist_name, _cover_url, tracks = await asyncio.to_thread(fetch_tracks, url)
    except Exception as e:
        raise HTTPException(400, str(e))

    if limit and isinstance(limit, int) and limit > 0:
        tracks = tracks[:limit]

    for i, t in enumerate(tracks, 1):
        t.index = i

    job_id = uuid.uuid4().hex[:12]
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in playlist_name)[:50]
    job_dir = TEMP_DIR / job_id
    song_dir = job_dir / safe_name

    stop_event = threading.Event()

    jobs[job_id] = {
        "status": "downloading",
        "mode": mode,
        "playlist_name": playlist_name,
        "total": len(tracks),
        "completed": 0,
        "failed": 0,
        "current_index": 0,
        "current_title": "",
        "current_titles": [],
        "track_status": [None] * len(tracks),
        "track_progress": {},
        "download_speed": 0,
        "files": [],
        "zip_path": None,
        "song_dir": None,
        "error": None,
        "created_at": time.time(),
        "stop_event": stop_event,
    }

    asyncio.create_task(_run_download(job_id, tracks, song_dir, fmt_key, stop_event, pack_zip=(mode == "zip")))
    return {"job_id": job_id, "total": len(tracks)}


async def _run_download(job_id, tracks, song_dir, fmt_key, stop_event, pack_zip=True):
    job = jobs[job_id]

    def on_start(idx, track):
        job["track_status"][idx - 1] = "downloading"

    def on_done(idx, track, path):
        if path:
            job["track_status"][idx - 1] = "done"
            job["completed"] += 1
            job["files"].append({"index": idx, "name": Path(path).name, "path": str(path)})
        else:
            job["track_status"][idx - 1] = "failed"
            job["failed"] += 1
        job["track_progress"].pop(idx, None)

    def on_progress(idx, info):
        status = info.get("status")
        if status == "downloading":
            downloaded = info.get("downloaded_bytes", 0) or 0
            total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
            pct = round(downloaded / total * 100, 1) if total > 0 else 0
            job["track_progress"][idx] = pct
            speed = info.get("speed") or 0
            if speed:
                job["download_speed"] = speed
        elif status == "finished":
            job["track_progress"][idx] = 100

    try:
        loop = asyncio.get_event_loop()
        result_path = await loop.run_in_executor(
            None,
            lambda: download_playlist(tracks, song_dir, fmt_key, on_start, on_done, stop_event, pack_zip=pack_zip, on_progress=on_progress),
        )
        if result_path:
            if pack_zip:
                job["zip_path"] = str(result_path)
            else:
                job["song_dir"] = str(result_path)
            job["status"] = "done"
        else:
            job["status"] = "stopped"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


async def _run_cart_download(job_id, items, song_dir, fmt_key, stop_event, pack_zip=True):
    job = jobs[job_id]

    def on_start(idx, item):
        job["track_status"][idx - 1] = "downloading"

    def on_done(idx, item, path):
        if path:
            job["track_status"][idx - 1] = "done"
            job["completed"] += 1
            job["files"].append({"index": idx, "name": Path(path).name, "path": str(path)})
        else:
            job["track_status"][idx - 1] = "failed"
            job["failed"] += 1
        job["track_progress"].pop(idx, None)

    def on_progress(idx, info):
        status = info.get("status")
        if status == "downloading":
            downloaded = info.get("downloaded_bytes", 0) or 0
            total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
            pct = round(downloaded / total * 100, 1) if total > 0 else 0
            job["track_progress"][idx] = pct
            speed = info.get("speed") or 0
            if speed:
                job["download_speed"] = speed
        elif status == "finished":
            job["track_progress"][idx] = 100

    try:
        loop = asyncio.get_event_loop()
        result_path = await loop.run_in_executor(
            None,
            lambda: download_cart(items, song_dir, fmt_key, on_start, on_done, stop_event, pack_zip=pack_zip, on_progress=on_progress),
        )
        if result_path:
            if pack_zip:
                job["zip_path"] = str(result_path)
            else:
                job["song_dir"] = str(result_path)
            job["status"] = "done"
        else:
            job["status"] = "stopped"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.post("/api/download_cart")
async def download_cart_endpoint(data: dict):
    tracks = data.get("tracks", [])
    fmt_key = data.get("format", "mp3_320")
    mode = data.get("mode", "zip")
    name = (data.get("name") or "Cart").strip() or "Cart"

    if not isinstance(tracks, list) or not tracks:
        raise HTTPException(400, "tracks must be a non-empty list")
    if fmt_key not in FORMAT_OPTIONS:
        raise HTTPException(400, f"Invalid format: {fmt_key}")
    if mode not in ("zip", "individual"):
        raise HTTPException(400, "Invalid mode")

    items = []
    for i, t in enumerate(tracks, 1):
        video_url = (t.get("video_url") or "").strip()
        title = (t.get("title") or "Unknown").strip() or "Unknown"
        artists = (t.get("artists") or "Unknown").strip() or "Unknown"
        if not video_url:
            raise HTTPException(400, f"Track {i} is missing video_url")
        items.append({
            "index": i,
            "video_url": video_url,
            "title": title,
            "artists": artists,
            "duration_ms": t.get("duration_ms", 0),
        })

    job_id = uuid.uuid4().hex[:12]
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:50]
    job_dir = TEMP_DIR / job_id
    song_dir = job_dir / safe_name
    stop_event = threading.Event()

    jobs[job_id] = {
        "status": "downloading",
        "mode": mode,
        "playlist_name": name,
        "total": len(items),
        "completed": 0,
        "failed": 0,
        "current_index": 0,
        "current_title": "",
        "current_titles": [],
        "track_status": [None] * len(items),
        "track_progress": {},
        "download_speed": 0,
        "files": [],
        "zip_path": None,
        "song_dir": None,
        "error": None,
        "created_at": time.time(),
        "stop_event": stop_event,
    }

    asyncio.create_task(_run_cart_download(job_id, items, song_dir, fmt_key, stop_event, pack_zip=(mode == "zip")))
    return {"job_id": job_id, "total": len(items)}


@app.post("/api/download_track")
async def download_single(data: dict):
    video_url = data.get("video_url", "").strip()
    title = data.get("title", "Unknown").strip() or "Unknown"
    artists = data.get("artists", "Unknown").strip() or "Unknown"
    fmt_key = data.get("format", "mp3_320")
    if not video_url:
        raise HTTPException(400, "video_url is required")
    if fmt_key not in FORMAT_OPTIONS:
        raise HTTPException(400, f"Invalid format: {fmt_key}")

    job_id = uuid.uuid4().hex[:12]
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:50]
    job_dir = TEMP_DIR / job_id
    song_dir = job_dir / safe_name
    stop_event = threading.Event()

    jobs[job_id] = {
        "status": "downloading",
        "mode": "individual",
        "playlist_name": title,
        "total": 1,
        "completed": 0,
        "failed": 0,
        "current_index": 0,
        "current_title": title,
        "current_titles": [],
        "track_status": [None],
        "track_progress": {},
        "download_speed": 0,
        "files": [],
        "zip_path": None,
        "song_dir": None,
        "error": None,
        "created_at": time.time(),
        "stop_event": stop_event,
    }

    asyncio.create_task(_run_single_download(job_id, video_url, title, artists, song_dir, fmt_key, stop_event))
    return {"job_id": job_id, "total": 1}


async def _run_single_download(job_id, video_url, title, artists, song_dir, fmt_key, stop_event):
    job = jobs[job_id]
    job["track_status"][0] = "downloading"

    def on_progress(info):
        status = info.get("status")
        if status == "downloading":
            downloaded = info.get("downloaded_bytes", 0) or 0
            total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
            pct = round(downloaded / total * 100, 1) if total > 0 else 0
            job["track_progress"][1] = pct
            speed = info.get("speed") or 0
            if speed:
                job["download_speed"] = speed
        elif status == "finished":
            job["track_progress"][1] = 100

    try:
        loop = asyncio.get_event_loop()
        path = await loop.run_in_executor(
            None,
            lambda: download_track_by_url(video_url, title, artists, song_dir, fmt_key, 1, progress_hook=on_progress),
        )
        if path:
            job["track_status"][0] = "done"
            job["completed"] = 1
            job["files"].append({"index": 1, "name": Path(path).name, "path": str(path)})
            job["song_dir"] = str(song_dir)
            job["status"] = "done"
        elif stop_event.is_set():
            job["status"] = "stopped"
        else:
            job["track_status"][0] = "failed"
            job["failed"] = 1
            job["status"] = "error"
            job["error"] = "Download failed. Try another result or format."
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    current_titles = []
    for i, status in enumerate(job["track_status"]):
        if status == "downloading":
            current_titles.append(f"Track {i + 1}")

    return JSONResponse({
        "status": job["status"],
        "mode": job["mode"],
        "playlist_name": job["playlist_name"],
        "total": job["total"],
        "completed": job["completed"],
        "failed": job["failed"],
        "current_index": job["current_index"],
        "current_title": job["current_title"],
        "current_downloading": current_titles,
        "track_status": job["track_status"],
        "track_progress": job.get("track_progress", {}),
        "download_speed": job.get("download_speed", 0),
        "files": [{"index": f["index"], "name": f["name"]} for f in job["files"]],
        "error": job["error"],
    })


@app.post("/api/stop/{job_id}")
async def stop_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "downloading":
        raise HTTPException(400, "Job is not running")
    job["stop_event"].set()
    return {"status": "stopping"}


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


@app.get("/api/track_file/{job_id}/{track_index}")
async def download_track_file(job_id: str, track_index: int):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "done":
        raise HTTPException(400, "Download not ready")
    if job["mode"] != "individual":
        raise HTTPException(400, "Job is not in individual mode")
    file_info = next((f for f in job["files"] if f["index"] == track_index), None)
    if not file_info:
        raise HTTPException(404, "Track file not found")
    path = Path(file_info["path"])
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(str(path), filename=file_info["name"])


@app.on_event("startup")
async def cleanup_loop():
    async def _cleanup():
        while True:
            await asyncio.sleep(300)
            now = time.time()
            to_remove = [
                jid for jid, j in jobs.items()
                if now - j.get("created_at", 0) > JOB_TTL and j["status"] in ("done", "error", "stopped")
            ]
            for jid in to_remove:
                job = jobs.pop(jid, None)
                if job:
                    if job.get("zip_path"):
                        p = Path(job["zip_path"])
                        if p.exists():
                            p.unlink(missing_ok=True)
                        parent = p.parent
                        if parent != TEMP_DIR:
                            shutil.rmtree(parent, ignore_errors=True)
                    if job.get("song_dir"):
                        p = Path(job["song_dir"])
                        if p.exists():
                            shutil.rmtree(p, ignore_errors=True)
                        parent = p.parent
                        if parent != TEMP_DIR:
                            shutil.rmtree(parent, ignore_errors=True)

    asyncio.create_task(_cleanup())


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
