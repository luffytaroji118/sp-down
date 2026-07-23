import os
import urllib.request
import urllib.parse
import urllib.error
import base64
import json
import re
import time
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Track:
    index: int
    title: str
    artists: str
    duration_ms: int
    spotify_uri: str
    is_playable: bool

    @property
    def duration_str(self) -> str:
        total_s = self.duration_ms // 1000
        return f"{total_s // 60}:{total_s % 60:02d}"

    @property
    def search_query(self) -> str:
        return f"{self.title} {self.artists}"

    def to_dict(self) -> dict:
        return asdict(self)


def extract_playlist_id(url: str) -> str:
    patterns = [
        r"playlist/([a-zA-Z0-9]+)",
        r"playlist:([a-zA-Z0-9]+)",
        r"playlist\?id=([a-zA-Z0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    if re.match(r"^[a-zA-Z0-9]{22}$", url):
        return url
    raise ValueError(f"Could not extract playlist ID from: {url}")


def _fetch_via_embed_token(playlist_id: str) -> tuple[str, list[Track]]:
    """Fetch all tracks using the access token from Spotify's embed page."""
    embed_url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
    req = urllib.request.Request(
        embed_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8")

    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise ValueError("Could not find track data in Spotify embed page")

    data = json.loads(match.group(1))

    # Get the access token from the embed page's session data
    token = (
        data.get("props", {})
        .get("pageProps", {})
        .get("state", {})
        .get("settings", {})
        .get("session", {})
        .get("accessToken", "")
    )

    # Get initial tracks from embed page
    entity = data["props"]["pageProps"]["state"]["data"]["entity"]
    playlist_name = entity.get("title", "Unknown Playlist")
    track_list = entity.get("trackList", [])

    tracks = []
    for t in track_list:
        tracks.append(
            Track(
                index=len(tracks) + 1,
                title=t.get("title", "Unknown"),
                artists=t.get("subtitle", "Unknown"),
                duration_ms=t.get("duration", 0),
                spotify_uri=t.get("uri", ""),
                is_playable=t.get("isPlayable", True),
            )
        )

    print(f"[SPOTIFY] Embed page returned {len(tracks)} tracks", flush=True)

    # If we have a token, paginate to get remaining tracks
    if token and len(tracks) > 0:
        headers = {"Authorization": f"Bearer {token}"}

        # Get total track count
        try:
            time.sleep(1)
            pl_req = urllib.request.Request(
                f"https://api.spotify.com/v1/playlists/{playlist_id}?fields=name,tracks(total)",
                headers=headers,
            )
            with urllib.request.urlopen(pl_req, timeout=15) as resp:
                pl_data = json.loads(resp.read().decode("utf-8"))
            total = pl_data.get("tracks", {}).get("total", 0)
            print(f"[SPOTIFY] API says total tracks: {total}", flush=True)

            if total > len(tracks):
                offset = len(tracks)
                while offset < total:
                    time.sleep(2)
                    api_url = (
                        f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
                        f"?offset={offset}&limit=50"
                        f"&fields=items(track(name,artists(name),duration_ms,uri,is_playable))"
                    )
                    req = urllib.request.Request(api_url, headers=headers)
                    try:
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            page_data = json.loads(resp.read().decode("utf-8"))
                    except urllib.error.HTTPError as e:
                        if e.code == 429:
                            retry_after = int(e.headers.get("Retry-After", "10"))
                            print(f"[SPOTIFY] Rate limited, waiting {retry_after}s...", flush=True)
                            time.sleep(retry_after)
                            continue
                        else:
                            print(f"[SPOTIFY] API error {e.code} at offset {offset}", flush=True)
                            break

                    items = page_data.get("items", [])
                    if not items:
                        break

                    for item in items:
                        t = item.get("track")
                        if not t:
                            continue
                        artists = ", ".join(a.get("name", "") for a in t.get("artists", []))
                        tracks.append(
                            Track(
                                index=len(tracks) + 1,
                                title=t.get("name", "Unknown"),
                                artists=artists or "Unknown",
                                duration_ms=t.get("duration_ms", 0),
                                spotify_uri=t.get("uri", ""),
                                is_playable=t.get("is_playable", True),
                            )
                        )

                    offset += len(items)
                    print(f"[SPOTIFY] Fetched {len(tracks)}/{total} tracks", flush=True)

        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"[SPOTIFY] Rate limited on initial request, using {len(tracks)} embed tracks", flush=True)
            else:
                print(f"[SPOTIFY] API error {e.code}, using {len(tracks)} embed tracks", flush=True)
        except Exception as e:
            print(f"[SPOTIFY] Pagination error: {e}, using {len(tracks)} embed tracks", flush=True)

    return playlist_name, tracks


def fetch_tracks(playlist_url: str) -> tuple[str, list[Track]]:
    playlist_id = extract_playlist_id(playlist_url)
    return _fetch_via_embed_token(playlist_id)
