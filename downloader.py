import os
import re
import shutil
import subprocess
import threading
import urllib.parse
import urllib.request
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from spotify import Track

FFMPEG_DIR = os.environ.get("FFMPEG_DIR", "")
if FFMPEG_DIR and os.path.isdir(FFMPEG_DIR):
    os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")
    print(f"[INFO] FFmpeg found at: {FFMPEG_DIR}", flush=True)
else:
    _ff = shutil.which("ffmpeg")
    if _ff:
        print(f"[INFO] FFmpeg found in PATH: {_ff}", flush=True)
    else:
        print("[WARNING] FFmpeg not found! Downloads will fail.", flush=True)

MAX_WORKERS = int(os.environ.get("MAX_WORKERS", 8))

PIPED_INSTANCES = [
    "https://api.piped.private.coffee",
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.reallyaweso.me",
    "https://pipedapi.drgns.space",
    "https://pipedapi.owo.si",
    "https://pipedapi.ducks.party",
    "https://pipedapi.darkness.services",
    "https://pipedapi.orangenet.cc",
    "https://pipedapi.nosebs.ru",
]

FORMAT_OPTIONS = {
    "mp3_320": {"codec": "libmp3lame", "quality": "320k", "ext": "mp3", "label": "MP3 320kbps"},
    "mp3_128": {"codec": "libmp3lame", "quality": "128k", "ext": "mp3", "label": "MP3 128kbps"},
    "flac": {"codec": "flac", "quality": "0", "ext": "flac", "label": "FLAC (Lossless)"},
    "m4a": {"codec": "aac", "quality": "256k", "ext": "m4a", "label": "M4A (AAC)"},
}

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _piped_get(path: str, timeout: int = 15) -> Optional[dict]:
    for instance in PIPED_INSTANCES:
        try:
            url = f"{instance}{path}"
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data and "error" not in data:
                    return data
                else:
                    err_msg = data.get("error", "") if data else ""
                    if "bot" in err_msg.lower() or "blocked" in err_msg.lower():
                        continue
                    return data
        except Exception:
            continue
    return None


def _piped_get_stream_urls(video_id: str) -> list[str]:
    """Try all Piped instances, collect all audio+video stream URLs."""
    urls = []
    for instance in PIPED_INSTANCES:
        try:
            url = f"{instance}/streams/{video_id}"
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data or "error" in data:
                    continue

                for s in data.get("audioStreams", []):
                    u = s.get("url")
                    if u:
                        urls.append(u)

                if not urls:
                    for s in data.get("videoStreams", []):
                        if not s.get("videoOnly", True):
                            u = s.get("url")
                            if u:
                                urls.append(u)

                if urls:
                    return urls
        except Exception:
            continue
    return urls


def _piped_search(query: str, limit: int = 5) -> list[dict]:
    encoded = urllib.parse.quote(query)
    data = _piped_get(f"/search?q={encoded}&filter=videos")
    if not data or "items" not in data:
        return []
    results = []
    for item in data["items"][:limit]:
        if item.get("type") != "stream":
            continue
        url = item.get("url", "")
        vid_id = ""
        if "/watch?v=" in url:
            vid_id = url.split("/watch?v=")[-1].split("&")[0]
        if not vid_id:
            continue
        results.append({
            "id": vid_id,
            "title": item.get("title", ""),
            "duration": item.get("duration", 0),
            "uploader": item.get("uploaderName", ""),
        })
    return results


def _build_search_queries(track: Track) -> list[str]:
    title = track.title.strip()
    artists = track.artists.strip()
    primary = artists.split(",")[0].strip()
    queries = [
        f"{title} {primary} official audio",
        f"{title} {primary} lyrics",
        f"{title} {primary} topic",
        f"{title} {primary}",
        f"{title} audio",
    ]
    seen = set()
    unique = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            unique.append(q)
    return unique


def _search_and_pick(track: Track) -> Optional[str]:
    duration_s = track.duration_ms / 1000
    queries = _build_search_queries(track)
    best_id = None
    best_score = -1

    for query in queries:
        results = _piped_search(query, limit=5)
        if not results:
            continue

        for entry in results:
            vid_duration = entry.get("duration", 0)
            vid_id = entry["id"]
            title = (entry.get("title") or "").lower()

            if vid_duration and duration_s:
                diff = abs(vid_duration - duration_s)
                dur_score = max(0, 100 - (diff * 3))
            else:
                dur_score = 30

            kw_bonus = 0
            for kw in ["official", "audio", "lyrics", "topic", "vevo", "mv", "music video"]:
                if kw in title:
                    kw_bonus += 5
            kw_bonus = min(kw_bonus, 20)

            penalty = 0
            tl = track.title.lower()
            if "remix" not in tl and "remix" in title:
                penalty += 30
            if "live" not in tl and "live" in title:
                penalty += 30
            if "cover" not in tl and "cover" in title:
                penalty += 20
            if "instrumental" not in tl and "instrumental" in title:
                penalty += 30
            if "slowed" not in tl and "slowed" in title:
                penalty += 30
            if "sped up" not in tl and ("sped up" in title or "speed up" in title):
                penalty += 30
            if "karaoke" in title:
                penalty += 30
            if "reaction" in title:
                penalty += 40
            if "tutorial" in title:
                penalty += 40

            score = dur_score + kw_bonus - penalty
            if score > best_score:
                best_score = score
                best_id = vid_id

        if best_score >= 80:
            break

    return best_id


def _get_audio_stream(video_id: str) -> Optional[str]:
    urls = _piped_get_stream_urls(video_id)
    return urls[0] if urls else None


def _download_file(url: str, dest: str, timeout: int = 60) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Referer": "https://www.youtube.com/"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        return True
    except Exception as e:
        print(f"[DOWNLOAD] HTTP error: {e}", flush=True)
        return False


def _convert_audio(src: str, dest: str, fmt_key: str, track: Track) -> bool:
    fmt = FORMAT_OPTIONS.get(fmt_key, FORMAT_OPTIONS["mp3_320"])
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"

    args = [ffmpeg, "-y", "-i", src, "-codec:a", fmt["codec"]]
    if fmt["ext"] != "flac":
        args.extend(["-b:a", fmt["quality"]])
    args.extend([
        "-metadata", f"title={track.title}",
        "-metadata", f"artist={track.artists}",
        "-metadata", f"track={track.index}",
    ])
    args.append(dest)

    try:
        result = subprocess.run(args, capture_output=True, timeout=120)
        return result.returncode == 0
    except Exception as e:
        print(f"[FFMPEG] Error: {e}", flush=True)
        return False


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > 180:
        name = name[:180]
    return name


def download_track(
    track: Track,
    output_dir: Path,
    fmt_key: str,
    progress_hook: Optional[Callable] = None,
) -> Optional[Path]:
    fmt = FORMAT_OPTIONS.get(fmt_key, FORMAT_OPTIONS["mp3_320"])
    filename = sanitize_filename(f"{track.index:02d}. {track.title} - {track.artists}")
    output_path = output_dir / f"{filename}.{fmt['ext']}"
    temp_path = str(output_path) + ".raw"

    video_id = _search_and_pick(track)
    if not video_id:
        print(f"[ERROR] Track {track.index}: no match found for '{track.title}'", flush=True)
        return None

    audio_url = _get_audio_stream(video_id)
    if not audio_url:
        print(f"[ERROR] Track {track.index}: no stream URL for video {video_id}", flush=True)
        return None

    if not _download_file(audio_url, temp_path):
        return None

    if not _convert_audio(temp_path, str(output_path), fmt_key, track):
        print(f"[ERROR] Track {track.index}: FFmpeg conversion failed", flush=True)
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        return None

    if os.path.exists(temp_path):
        os.unlink(temp_path)

    if output_path.exists():
        return output_path
    return None


def download_playlist(
    tracks: list[Track],
    output_dir: Path,
    fmt_key: str,
    on_track_start: Callable[[int, Track], None] = lambda i, t: None,
    on_track_done: Callable[[int, Track, Optional[Path]], None] = lambda i, t, p: None,
    stop_event: Optional[threading.Event] = None,
    max_workers: int = MAX_WORKERS,
) -> Optional[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    def _worker(track):
        if stop_event and stop_event.is_set():
            return track.index, None
        on_track_start(track.index, track)
        result = download_track(track, output_dir, fmt_key)
        return track.index, result

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, t): t for t in tracks}

        for future in as_completed(futures):
            track = futures[future]
            if stop_event and stop_event.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                break
            try:
                idx, result = future.result()
                on_track_done(idx, tracks[idx - 1], result)
            except Exception as e:
                print(f"[ERROR] Worker error for track {track.index}: {e}", flush=True)
                on_track_done(track.index, track, None)

    if stop_event and stop_event.is_set():
        return None

    zip_path = output_dir.parent / f"{output_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(output_dir.iterdir()):
            if f.is_file():
                zf.write(f, f.name)

    shutil.rmtree(output_dir, ignore_errors=True)
    return zip_path
