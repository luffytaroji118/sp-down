import os
import re
import shutil
import subprocess
import threading
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

import yt_dlp

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

PROXY_RAW = os.environ.get("PROXY", "")
PROXY_URL = ""
if PROXY_RAW:
    parts = PROXY_RAW.split(":")
    if len(parts) == 4:
        host, port, user, pwd = parts
        PROXY_URL = f"http://{user}:{pwd}@{host}:{port}"
    elif len(parts) == 2:
        PROXY_URL = f"http://{parts[0]}:{parts[1]}"
    elif PROXY_RAW.startswith("http"):
        PROXY_URL = PROXY_RAW
    print(f"[INFO] Proxy configured: {PROXY_URL.split('@')[-1] if '@' in PROXY_URL else PROXY_URL}", flush=True)
else:
    print("[WARNING] No proxy configured. YouTube bot detection may block downloads.", flush=True)

FORMAT_OPTIONS = {
    "mp3_320": {"codec": "libmp3lame", "quality": "320k", "ext": "mp3", "label": "MP3 320kbps"},
    "mp3_128": {"codec": "libmp3lame", "quality": "128k", "ext": "mp3", "label": "MP3 128kbps"},
    "flac": {"codec": "flac", "quality": "0", "ext": "flac", "label": "FLAC (Lossless)"},
    "m4a": {"codec": "aac", "quality": "256k", "ext": "m4a", "label": "M4A (AAC)"},
}

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def _base_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "geo_bypass": True,
        "socket_timeout": 15,
    }
    if PROXY_URL:
        opts["proxy"] = PROXY_URL
    return opts


def _player_opts() -> dict:
    opts = _base_opts()
    opts["extractor_args"] = {
        "youtube": {
            "player_client": ["android", "ios", "web"],
        }
    }
    return opts


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > 180:
        name = name[:180]
    return name


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
    best_url = None
    best_score = -1

    for query in queries:
        try:
            search_opts = _player_opts()
            search_opts.update({
                "skip_download": True,
                "extract_flat": True,
                "default_search": "ytsearch3",
            })
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                info = ydl.extract_info(f"ytsearch3:{query}", download=False)

            entries = info.get("entries", []) if info else []
            if not entries:
                continue

            for entry in entries:
                if not entry:
                    continue
                vid_duration = entry.get("duration") or 0
                vid_url = entry.get("url") or entry.get("id")
                if not vid_url:
                    continue
                if not vid_url.startswith("http"):
                    vid_url = f"https://www.youtube.com/watch?v={vid_url}"

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
                    best_url = vid_url

            if best_score >= 80:
                break

        except Exception as e:
            print(f"[SEARCH] Query '{query}' failed: {e}", flush=True)
            continue

    return best_url


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

    video_url = _search_and_pick(track)
    if not video_url:
        print(f"[ERROR] Track {track.index}: no YouTube match found for '{track.title}'", flush=True)
        return None

    # Step 1: Extract direct stream URL via proxy (bypasses bot detection)
    extract_opts = _player_opts()
    extract_opts.update({
        "format": "bestaudio/best",
        "noplaylist": True,
        "skip_download": True,
        "no_progress": True,
    })

    stream_url = None
    try:
        with yt_dlp.YoutubeDL(extract_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            stream_url = info.get("url")
            if not stream_url:
                for f in info.get("formats", []):
                    if f.get("acodec") and f["acodec"] != "none":
                        stream_url = f.get("url")
                        break
    except Exception as e:
        print(f"[ERROR] Track {track.index} extraction failed: {e}", flush=True)
        return None

    if not stream_url:
        print(f"[ERROR] Track {track.index}: no stream URL extracted", flush=True)
        return None

    # Step 2: Download directly from googlevideo CDN (no proxy needed, not blocked)
    try:
        req = urllib.request.Request(stream_url, headers={
            "User-Agent": _UA,
            "Referer": "https://www.youtube.com/",
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(temp_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
    except Exception as e:
        print(f"[ERROR] Track {track.index} download failed: {e}", flush=True)
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        return None

    # Step 3: Convert with FFmpeg
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    args = [ffmpeg, "-y", "-i", temp_path, "-codec:a", fmt["codec"]]
    if fmt["ext"] != "flac":
        args.extend(["-b:a", fmt["quality"]])
    args.extend([
        "-metadata", f"title={track.title}",
        "-metadata", f"artist={track.artists}",
        "-metadata", f"track={track.index}",
    ])
    args.append(str(output_path))

    try:
        result = subprocess.run(args, capture_output=True, timeout=120)
        if result.returncode != 0:
            print(f"[ERROR] Track {track.index}: FFmpeg failed: {result.stderr.decode()[:200]}", flush=True)
            return None
    except Exception as e:
        print(f"[ERROR] Track {track.index}: FFmpeg error: {e}", flush=True)
        return None
    finally:
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
