import urllib.request
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


def fetch_tracks(playlist_url: str) -> tuple[str, list[Track]]:
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
