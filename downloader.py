import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Callable, Optional

import yt_dlp

from spotify import Track

FFMPEG_DIR = os.environ.get("FFMPEG_DIR", "")
if FFMPEG_DIR and os.path.isdir(FFMPEG_DIR):
    os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")
    print(f"[INFO] FFmpeg found at: {FFMPEG_DIR}", flush=True)
else:
    import shutil as _sh
    _ff = _sh.which("ffmpeg")
    if _ff:
        print(f"[INFO] FFmpeg found in PATH: {_ff}", flush=True)
    else:
        print("[WARNING] FFmpeg not found! Downloads will fail.", flush=True)

FORMAT_OPTIONS = {
    "mp3_320": {"codec": "mp3", "quality": "320", "ext": "mp3", "label": "MP3 320kbps"},
    "mp3_128": {"codec": "mp3", "quality": "128", "ext": "mp3", "label": "MP3 128kbps"},
    "flac": {"codec": "flac", "quality": "0", "ext": "flac", "label": "FLAC (Lossless)"},
    "m4a": {"codec": "m4a", "quality": "0", "ext": "m4a", "label": "M4A (AAC)"},
}


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > 180:
        name = name[:180]
    return name


def _build_search_queries(track: Track) -> list[str]:
    """Build a list of search queries to try, from most to least specific."""
    title = track.title.strip()
    artists = track.artists.strip()
    primary_artist = artists.split(",")[0].strip()

    queries = [
        f"{title} {primary_artist} official audio",
        f"{title} {primary_artist} lyrics",
        f"{title} {primary_artist} topic",
        f"{title} {primary_artist}",
        f'{title} "{primary_artist}"',
        f"{title} {artists}",
    ]

    # Remove "(feat. ...)" from title for a cleaner fallback search
    title_clean = re.sub(r"\s*[\(\[](feat|ft|featuring)\.?\s*[^\)\]]*[\)\]]", "", title, flags=re.IGNORECASE).strip()
    if title_clean and title_clean != title:
        queries.append(f"{title_clean} {primary_artist} audio")

    # Final fallback: just the title
    queries.append(f"{title} audio")

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            unique.append(q)
    return unique


def _search_and_pick(track: Track) -> Optional[str]:
    """Search YouTube, return best matching video URL based on duration."""
    duration_s = track.duration_ms / 1000
    queries = _build_search_queries(track)

    best_url = None
    best_score = -1

    for query in queries:
        try:
            search_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": True,
                "default_search": "ytsearch5",
                "geo_bypass": True,
                "socket_timeout": 15,
            }
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                info = ydl.extract_info(f"ytsearch5:{query}", download=False)

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
                if vid_url.startswith("http"):
                    pass
                else:
                    vid_url = f"https://www.youtube.com/watch?v={vid_url}"

                # Score: prefer duration close to Spotify's, and official/official audio/lyrics titles
                title = (entry.get("title") or "").lower()

                # Duration score (0-100): perfect match = 100, 30s off = ~10
                if vid_duration and duration_s:
                    diff = abs(vid_duration - duration_s)
                    dur_score = max(0, 100 - (diff * 3))
                else:
                    dur_score = 30  # unknown duration, give low-mid score

                # Keyword bonus
                kw_bonus = 0
                for kw in ["official", "audio", "lyrics", "topic", "vevo", "mv", "music video"]:
                    if kw in title:
                        kw_bonus += 5
                kw_bonus = min(kw_bonus, 20)

                # Penalty for live/cover/remix if original doesn't have those
                penalty = 0
                title_lower = track.title.lower()
                if "remix" not in title_lower and "remix" in title:
                    penalty += 30
                if "live" not in title_lower and "live" in title:
                    penalty += 30
                if "cover" not in title_lower and "cover" in title:
                    penalty += 20
                if "instrumental" not in title_lower and "instrumental" in title:
                    penalty += 30
                if "slowed" not in title_lower and "slowed" in title:
                    penalty += 30
                if "sped up" not in title_lower and ("sped up" in title or "speed up" in title):
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

            # If we found a strong match (duration within 5s + keywords), stop early
            if best_score >= 90:
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

    # First: find the best YouTube video
    video_url = _search_and_pick(track)
    if not video_url:
        print(f"[ERROR] Track {track.index}: no YouTube match found for '{track.title}'", flush=True)
        return None

    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "no_progress": True,
        "outtmpl": output_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": fmt["codec"],
                "preferredquality": fmt["quality"],
            },
            {
                "key": "FFmpegMetadata",
            },
        ],
        "geo_bypass": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
    }

    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
    except Exception as e:
        print(f"[ERROR] Track {track.index} download failed: {e}", flush=True)
        return None

    expected = output_dir / f"{filename}.{fmt['ext']}"
    if expected.exists():
        return expected

    for f in output_dir.glob(f"{filename}.*"):
        return f
    return None


def download_playlist(
    tracks: list[Track],
    output_dir: Path,
    fmt_key: str,
    on_track_start: Callable[[int, Track], None] = lambda i, t: None,
    on_track_done: Callable[[int, Track, Optional[Path]], None] = lambda i, t, p: None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    fmt = FORMAT_OPTIONS.get(fmt_key, FORMAT_OPTIONS["mp3_320"])

    for track in tracks:
        on_track_start(track.index, track)
        result = download_track(track, output_dir, fmt_key)
        on_track_done(track.index, track, result)

    zip_path = output_dir.parent / f"{output_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(output_dir.iterdir()):
            if f.is_file():
                zf.write(f, f.name)

    shutil.rmtree(output_dir, ignore_errors=True)
    return zip_path
