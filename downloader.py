import os
import re
import shutil
import subprocess
import threading
import time
import json
import base64
import tempfile
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

MAX_WORKERS = int(os.environ.get("MAX_WORKERS", 24))
AUDIO_MAX_ABR = os.environ.get("AUDIO_MAX_ABR", "160")
FRAGMENT_WORKERS = int(os.environ.get("FRAGMENT_WORKERS", 16))
HTTP_CHUNK_SIZE = int(os.environ.get("HTTP_CHUNK_SIZE", 9_000_000))
THROTTLED_RATE = int(os.environ.get("THROTTLED_RATE", 100_000))
DIRECT_FIRST = os.environ.get("DIRECT_FIRST", "false").lower() in ("1", "true", "yes")
POT_PROVIDER_URL = os.environ.get("POT_PROVIDER_URL", "https://po-production-93d7.up.railway.app/getPot")
AUDIO_STREAM_URL = os.environ.get("AUDIO_STREAM_URL", "https://po-production-93d7.up.railway.app/getAudioStream")

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


def _load_cookies() -> Optional[str]:
    """Load cookies from COOKIES_B64, COOKIE_FILE, or local cookie.txt. Returns path to Netscape cookies file."""
    cookies_b64 = os.environ.get("COOKIES_B64", "")
    cookie_file = os.environ.get("COOKIE_FILE", "")
    local_cookie = str(Path(__file__).parent / "cookie.txt")

    # Essential cookies for YouTube bot-detection bypass (avoid HTTP 413 from bloated headers)
    essential_cookies = {
        "VISITOR_PRIVACY_METADATA", "PREF", "YSC", "GPS", "NID", "SID", "HSID",
        "SSID", "APISID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID",
        "__Secure-1PAPISID", "__Secure-3PAPISID", "SIDCC", "LSID", "LoginInfo",
    }

    temp_path = None

    if cookies_b64:
        try:
            content = base64.b64decode(cookies_b64).decode("utf-8")
            temp_path = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, prefix="yt_cookies_")
            temp_path.write(content)
            temp_path.close()
            print(f"[INFO] Cookies loaded from COOKIES_B64", flush=True)
            return temp_path.name
        except Exception as e:
            print(f"[WARNING] Failed to decode COOKIES_B64: {e}", flush=True)

    if cookie_file and os.path.exists(cookie_file):
        print(f"[INFO] Using cookie file: {cookie_file}", flush=True)
        return cookie_file

    if os.path.exists(local_cookie):
        try:
            with open(local_cookie, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content.startswith("["):
                cookies_json_all = json.loads(content)
                cookies_json = [c for c in cookies_json_all if c.get("name", "") in essential_cookies]
                lines = ["# Netscape HTTP Cookie File", ""]
                for c in cookies_json:
                    name = c.get("name", "")
                    value = c.get("value", "")
                    path = c.get("path", "/")
                    secure = "TRUE" if c.get("secure", True) else "FALSE"
                    expires = c.get("expires", -1)
                    if expires == -1 or expires is None:
                        expiry = int(time.time()) + 86400 * 365
                    else:
                        expiry = int(expires)
                    host = c.get("domain", ".youtube.com")
                    lines.append(f"{host}\tTRUE\t{path}\t{secure}\t{expiry}\t{name}\t{value}")
                temp_path = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, prefix="yt_cookies_")
                temp_path.write("\n".join(lines))
                temp_path.close()
                print(f"[INFO] Cookies converted: {len(cookies_json)} essential (filtered from {len(cookies_json_all)})", flush=True)
                return temp_path.name
            else:
                print(f"[INFO] Using local cookie file: {local_cookie}", flush=True)
                return local_cookie
        except Exception as e:
            print(f"[WARNING] Failed to load local cookie.txt: {e}", flush=True)

    print("[INFO] No cookies configured", flush=True)
    return None


COOKIE_PATH = _load_cookies()


def _extract_video_id(url: str) -> Optional[str]:
    m = re.search(r"(?:v=|youtu\.be/|/embed/|shorts/)([a-zA-Z0-9_-]{11})", url)
    return m.group(1) if m else None


def _get_pot_token(video_id: str, with_audio: bool = False) -> Optional[dict]:
    """Fetch PO token (and optionally audio URL) from the provider."""
    try:
        params = f"content_binding={video_id}"
        if with_audio:
            params += "&audio=1"
        api_url = f"{POT_PROVIDER_URL}?{params}"
        print(f"[INFO] Requesting PO token{' + audio' if with_audio else ''} for {video_id}…", flush=True)
        req = urllib.request.Request(api_url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        token = data.get("poToken")
        audio_url = data.get("audioUrl")
        if token:
            extras = []
            if audio_url:
                extras.append(f"audio URL ({len(audio_url)} chars)")
            print(f"[INFO] PO token received ({len(token)} chars)" + (f" + {' + '.join(extras)}" if extras else ""), flush=True)
            result = {"po_token": token}
            if audio_url:
                result["audio_url"] = audio_url
            return result
        print(f"[WARNING] PO token empty in response keys: {list(data.keys())}", flush=True)
    except Exception as e:
        print(f"[WARNING] PO token fetch failed: {e}", flush=True)
    return None

FORMAT_OPTIONS = {
    "mp3_320": {"codec": "mp3", "quality": "320", "ext": "mp3", "label": "MP3 320kbps"},
    "mp3_128": {"codec": "mp3", "quality": "128", "ext": "mp3", "label": "MP3 128kbps"},
    "flac": {"codec": "flac", "quality": "0", "ext": "flac", "label": "FLAC (Lossless)"},
    "m4a": {"codec": "m4a", "quality": "0", "ext": "m4a", "label": "M4A (AAC)"},
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
            "player_client": ["tv", "tv_downgraded", "android_vr", "visionos", "web_safari"],
        }
    }
    return opts


def _download_opts() -> dict:
    return {
        "http_chunk_size": HTTP_CHUNK_SIZE,
        "throttled_rate": THROTTLED_RATE,
        "concurrent_fragment_downloads": FRAGMENT_WORKERS,
        "buffersize": 1024 * 1024,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 5,
    }


def _download_audio_direct(
    video_id: str,
    output_path: Path,
    fmt: dict,
    progress_hook: Optional[Callable] = None,
) -> Optional[Path]:
    """Stream audio from PO token API + convert with ffmpeg. Skips yt-dlp extraction entirely."""
    try:
        stream_url = f"{AUDIO_STREAM_URL}?content_binding={video_id}"
        print(f"[INFO] Streaming audio from API for {video_id}…", flush=True)

        # Stream to temp file
        tmp_path = output_path.with_suffix(".m4a")
        req = urllib.request.Request(stream_url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_length = int(resp.headers.get("Content-Length", 0) or 0)
            total = 0
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
                    if progress_hook:
                        progress_hook({"status": "downloading", "downloaded_bytes": total, "total_bytes": content_length})
        if progress_hook:
            progress_hook({"status": "finished"})

        print(f"[INFO] Downloaded {total / 1048576:.1f} MB, converting to {fmt['ext']}…", flush=True)

        # Convert to target format with ffmpeg
        quality_args = []
        if fmt["codec"] == "mp3":
            quality_args = ["-b:a", f"{fmt['quality']}k"]
        elif fmt["codec"] == "flac":
            quality_args = ["-c:a", "flac"]
        elif fmt["codec"] == "m4a":
            quality_args = ["-c:a", "aac", "-b:a", "256k"]

        ffmpeg_cmd = ["ffmpeg", "-y", "-i", str(tmp_path)] + quality_args + [str(output_path)]
        result = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=60)
        tmp_path.unlink(missing_ok=True)

        if output_path.exists():
            print(f"[INFO] Done: {output_path.name} ({output_path.stat().st_size / 1048576:.1f} MB)", flush=True)
            return output_path
        print(f"[ERROR] ffmpeg failed: {result.stderr.decode()[-300:]}", flush=True)
        return None
    except Exception as e:
        print(f"[ERROR] Audio stream download failed: {e}", flush=True)
        return None


def _extract_and_download(
    video_url: str,
    output_template: str,
    fmt: dict,
    progress_hook: Optional[Callable] = None,
) -> bool:
    """Extract + download. Direct with PO token + cookies first, proxy fallback."""
    postprocessors = [
        {"key": "FFmpegExtractAudio", "preferredcodec": fmt["codec"], "preferredquality": fmt["quality"]},
        {"key": "FFmpegMetadata"},
    ]

    def _build_direct_opts(po_token: str = "") -> dict:
        """Direct download: web client + cookies + PO token, no proxy."""
        opts = {
            "quiet": True,
            "no_warnings": True,
            "geo_bypass": True,
            "socket_timeout": 10,
        }
        if COOKIE_PATH:
            opts["cookiefile"] = COOKIE_PATH
        extractor_args = {"youtube": {"player_client": ["web"]}}
        if po_token:
            extractor_args["youtube"]["po_token"] = [f"gvs:{po_token}"]
        opts["extractor_args"] = extractor_args
        opts.update(_download_opts())
        opts.update({
            "format": f"ba[abr<={AUDIO_MAX_ABR}]/bestaudio/best",
            "noplaylist": True,
            "no_progress": True,
            "outtmpl": output_template,
            "postprocessors": postprocessors,
        })
        if progress_hook:
            opts["progress_hooks"] = [progress_hook]
        return opts

    def _build_proxy_opts() -> dict:
        """Proxy download: tv/android_vr clients, no PO token needed."""
        opts = {
            "quiet": True,
            "no_warnings": True,
            "geo_bypass": True,
            "socket_timeout": 10,
            "proxy": PROXY_URL,
        }
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["tv", "android_vr"],
            }
        }
        opts.update(_download_opts())
        opts.update({
            "format": f"ba[abr<={AUDIO_MAX_ABR}]/bestaudio/best",
            "noplaylist": True,
            "no_progress": True,
            "outtmpl": output_template,
            "postprocessors": postprocessors,
        })
        if progress_hook:
            opts["progress_hooks"] = [progress_hook]
        return opts

    # Attempt 1: Stream audio directly from API (fastest — skips yt-dlp extraction entirely)
    video_id = _extract_video_id(video_url)
    if video_id and AUDIO_STREAM_URL:
        base = output_template.replace(".%(ext)s", "").replace("%(ext)s", "")
        if base.endswith("."):
            base = base[:-1]
        output_path = Path(base + "." + fmt["ext"])
        result = _download_audio_direct(video_id, output_path, fmt, progress_hook)
        if result and result.exists():
            return True
        print("[INFO] Audio stream failed, trying PO token + yt-dlp…", flush=True)

    # Attempt 2: PO token + yt-dlp web client extraction
    if video_id and POT_PROVIDER_URL and COOKIE_PATH:
        pot = _get_pot_token(video_id)
        if pot and pot.get("po_token"):
            print("[INFO] Trying direct download with PO token…", flush=True)
            try:
                with yt_dlp.YoutubeDL(_build_direct_opts(pot["po_token"])) as ydl:
                    ydl.download([video_url])
                return True
            except Exception as e:
                print(f"[INFO] PO token direct failed ({e}), falling back to proxy…", flush=True)

    # Attempt 3: Proxy download (no PO token, tv/android_vr clients)
    if not PROXY_URL:
        print("[INFO] No proxy configured — downloading directly…", flush=True)
        try:
            with yt_dlp.YoutubeDL(_build_direct_opts()) as ydl:
                ydl.download([video_url])
            return True
        except Exception as e:
            print(f"[ERROR] Download failed: {e}", flush=True)
            return False

    try:
        print("[INFO] Downloading via proxy…", flush=True)
        with yt_dlp.YoutubeDL(_build_proxy_opts()) as ydl:
            ydl.download([video_url])
        return True
    except Exception as e:
        print(f"[ERROR] Proxy download failed: {e}", flush=True)
        return False


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
            search_opts = {
                "quiet": True,
                "no_warnings": True,
                "geo_bypass": True,
                "socket_timeout": 10,
                "extractor_args": {"youtube": {"player_client": ["tv"]}},
                "skip_download": True,
                "extract_flat": True,
                "default_search": "ytsearch2",
            }
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                info = ydl.extract_info(f"ytsearch2:{query}", download=False)

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
    output_template = str(output_dir / f"{filename}.%(ext)s")

    video_url = _search_and_pick(track)
    if not video_url:
        print(f"[ERROR] Track {track.index}: no YouTube match found for '{track.title}'", flush=True)
        return None

    success = _extract_and_download(video_url, output_template, fmt, progress_hook)
    if not success:
        return None

    expected = output_dir / f"{filename}.{fmt['ext']}"
    if expected.exists():
        return expected
    for f in output_dir.glob(f"{filename}.*"):
        return f
    return None


def download_track_by_url(
    video_url: str,
    title: str,
    artists: str,
    output_dir: Path,
    fmt_key: str,
    index: int = 1,
    progress_hook: Optional[Callable] = None,
) -> Optional[Path]:
    fmt = FORMAT_OPTIONS.get(fmt_key, FORMAT_OPTIONS["mp3_320"])
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = sanitize_filename(f"{index:02d}. {title} - {artists}")
    output_template = str(output_dir / f"{filename}.%(ext)s")

    success = _extract_and_download(video_url, output_template, fmt, progress_hook)
    if not success:
        return None

    expected = output_dir / f"{filename}.{fmt['ext']}"
    if expected.exists():
        return expected
    for f in output_dir.glob(f"{filename}.*"):
        return f
    return None


def search_youtube(query: str, limit: int = 10) -> list[dict]:
    search_opts = {
        "quiet": True,
        "no_warnings": True,
        "geo_bypass": True,
        "socket_timeout": 10,
        "extractor_args": {"youtube": {"player_client": ["tv"]}},
        "skip_download": True,
        "extract_flat": True,
        "default_search": f"ytsearch{limit}",
    }
    try:
        with yt_dlp.YoutubeDL(search_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        entries = info.get("entries", []) if info else []
        results = []
        for e in entries:
            if not e:
                continue
            vid_url = e.get("url") or e.get("id")
            if not vid_url:
                continue
            if not vid_url.startswith("http"):
                vid_url = f"https://www.youtube.com/watch?v={vid_url}"
            results.append({
                "title": e.get("title", "Unknown"),
                "artists": e.get("uploader") or e.get("channel") or "Unknown",
                "duration_ms": (e.get("duration") or 0) * 1000,
                "video_url": vid_url,
            })
        return results
    except Exception as e:
        print(f"[SEARCH] query '{query}' failed: {e}", flush=True)
        return []


def download_playlist(
    tracks: list[Track],
    output_dir: Path,
    fmt_key: str,
    on_track_start: Callable[[int, Track], None] = lambda i, t: None,
    on_track_done: Callable[[int, Track, Optional[Path]], None] = lambda i, t, p: None,
    stop_event: Optional[threading.Event] = None,
    max_workers: int = MAX_WORKERS,
    pack_zip: bool = True,
    on_progress: Optional[Callable[[int, dict], None]] = None,
) -> Optional[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    def _worker(track):
        if stop_event and stop_event.is_set():
            return track.index, None
        on_track_start(track.index, track)
        hook = (lambda d: on_progress(track.index, d)) if on_progress else None
        result = download_track(track, output_dir, fmt_key, progress_hook=hook)
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

    if not pack_zip:
        return output_dir

    zip_path = output_dir.parent / f"{output_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(output_dir.iterdir()):
            if f.is_file():
                zf.write(f, f.name)

    shutil.rmtree(output_dir, ignore_errors=True)
    return zip_path
