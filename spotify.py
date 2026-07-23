import os
import urllib.request
import urllib.parse
import base64
import json
import re
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


def _get_spotify_token() -> Optional[str]:
    client_id = os.environ.get("SPOTIPY_CLIENT_ID", "")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None
    try:
        auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        req = urllib.request.Request(
            "https://accounts.spotify.com/api/token",
            data=data,
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            token_data = json.loads(resp.read().decode("utf-8"))
            return token_data.get("access_token")
    except Exception as e:
        print(f"[SPOTIFY] Token error: {e}", flush=True)
        return None


def _fetch_via_api(playlist_id: str) -> tuple[str, list[Track]]:
    token = _get_spotify_token()
    if not token:
        raise ValueError("No Spotify API credentials configured")

    headers = {"Authorization": f"Bearer {token}"}

    playlist_url = f"https://api.spotify.com/v1/playlists/{playlist_id}?fields=name,tracks(total)"
    req = urllib.request.Request(playlist_url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        pl_data = json.loads(resp.read().decode("utf-8"))

    playlist_name = pl_data.get("name", "Unknown Playlist")
    total = pl_data.get("tracks", {}).get("total", 0)
    print(f"[SPOTIFY] Playlist '{playlist_name}' has {total} tracks", flush=True)

    tracks = []
    offset = 0
    limit = 100

    while offset < total:
        api_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks?offset={offset}&limit={limit}&fields=items(track(name,artists(name),duration_ms,uri,is_playable))"
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            t = item.get("track")
            if not t:
                continue
            idx = len(tracks) + 1
            title = t.get("name", "Unknown")
            artists = ", ".join(a.get("name", "") for a in t.get("artists", []))
            tracks.append(
                Track(
                    index=idx,
                    title=title,
                    artists=artists or "Unknown",
                    duration_ms=t.get("duration_ms", 0),
                    spotify_uri=t.get("uri", ""),
                    is_playable=t.get("is_playable", True),
                )
            )

        offset += limit
        print(f"[SPOTIFY] Fetched {len(tracks)}/{total} tracks...", flush=True)

    return playlist_name, tracks


def _fetch_via_embed(playlist_url: str) -> tuple[str, list[Track]]:
    playlist_id = extract_playlist_id(playlist_url)
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
    entity = data["props"]["pageProps"]["state"]["data"]["entity"]
    playlist_name = entity.get("title", "Unknown Playlist")
    track_list = entity.get("trackList", [])

    tracks = []
    for i, t in enumerate(track_list, 1):
        tracks.append(
            Track(
                index=i,
                title=t.get("title", "Unknown"),
                artists=t.get("subtitle", "Unknown"),
                duration_ms=t.get("duration", 0),
                spotify_uri=t.get("uri", ""),
                is_playable=t.get("isPlayable", True),
            )
        )

    return playlist_name, tracks


def fetch_tracks(playlist_url: str) -> tuple[str, list[Track]]:
    playlist_id = extract_playlist_id(playlist_url)

    if os.environ.get("SPOTIPY_CLIENT_ID") and os.environ.get("SPOTIPY_CLIENT_SECRET"):
        try:
            return _fetch_via_api(playlist_id)
        except Exception as e:
            print(f"[SPOTIFY] API failed, falling back to embed: {e}", flush=True)

    return _fetch_via_embed(playlist_url)
