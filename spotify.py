import os
import urllib.request
import urllib.parse
import urllib.error
import base64
import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

try:
    import pyotp
    _HAS_PYOTP = True
except ImportError:
    _HAS_PYOTP = False

try:
    from curl_cffi import requests as _creq
    _HAS_CURLCFFI = True
except ImportError:
    _HAS_CURLCFFI = False


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


def extract_track_id(url: str) -> str:
    patterns = [
        r"track/([a-zA-Z0-9]+)",
        r"track:([a-zA-Z0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    if re.match(r"^[a-zA-Z0-9]{22}$", url):
        return url
    raise ValueError(f"Could not extract track ID from: {url}")


def is_spotify_track_url(url: str) -> bool:
    return bool(re.search(r"track/([a-zA-Z0-9]+)", url) or url.startswith("spotify:track:"))


def is_spotify_url(url: str) -> bool:
    return bool(re.search(r"spotify\.com|spotify:", url, re.IGNORECASE))


# ---- GraphQL pathfinder (fast, no rate-limit, gets ALL tracks) ----

_GRAPHQL_CACHE = Path.home() / ".cache" / "spotapi-fast" / "cache.json"
_FPH = "e4b2953f160e58e38ac025d79b5a9b3aceee5c4c716598e9830bfceb69faff5f"
_CVER = "1.2.97.113.gb2fcd25e-development"
_TOTP_V61 = [44, 55, 47, 42, 70, 40, 34, 114, 76, 74, 50, 111, 120, 97, 75, 76, 94, 102, 43, 69, 49, 120, 118, 80, 64, 78]
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def _totp():
    tr = [e ^ ((t % 33) + 9) for t, e in enumerate(_TOTP_V61)]
    hs = "".join(str(n) for n in tr).encode().hex()
    return pyotp.TOTP(base64.b32encode(bytes.fromhex(hs)).decode().rstrip("=")).now()


def _graphql_bootstrap(s):
    totp = _totp()
    r = s.get("https://open.spotify.com/api/token",
              params={"reason": "init", "productType": "web-player",
                      "totp": totp, "totpVer": 61, "totpServer": totp})
    j = r.json()
    ct = s.post("https://clienttoken.spotify.com/v1/clienttoken",
        json={"client_data": {"client_version": _CVER, "client_id": j["clientId"],
              "js_sdk_data": {"device_brand": "unknown", "device_model": "unknown",
              "os": "windows", "os_version": "NT 10.0",
              "device_id": "0" * 32, "device_type": "computer"}}},
        headers={"Accept": "application/json",
                 "Content-Type": "application/json"}).json()["granted_token"]["token"]
    return {"access_token": j["accessToken"], "client_token": ct,
            "client_id": j["clientId"],
            "expires_at_ms": int(j["accessTokenExpirationTimestampMs"]),
            "client_version": _CVER, "fph": _FPH}


def _graphql_session():
    if _HAS_CURLCFFI:
        return _creq.Session(impersonate="chrome131")
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": _UA})
    return s


def _graphql_cached_token(s):
    c = None
    if _GRAPHQL_CACHE.exists():
        try:
            c = json.loads(_GRAPHQL_CACHE.read_text())
        except Exception:
            c = None
    if not c or time.time() * 1000 > c["expires_at_ms"] - 30000:
        c = _graphql_bootstrap(s)
        _GRAPHQL_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _GRAPHQL_CACHE.write_text(json.dumps(c))
    return c


def _graphql_request(s, c, operation, variables):
    ext = {"persistedQuery": {"version": 1, "sha256Hash": c["fph"]}}
    return s.post("https://api-partner.spotify.com/pathfinder/v1/query",
        params={"operationName": operation,
                "variables": json.dumps(variables),
                "extensions": json.dumps(ext)},
        headers={"Authorization": f"Bearer {c['access_token']}",
                 "Client-Token": c["client_token"],
                 "Spotify-App-Version": c["client_version"],
                 "Accept-Language": "en"})


def _parse_track_data(d: dict, index: int) -> Track:
    artists = ", ".join(
        a.get("profile", {}).get("name", "")
        for a in d.get("artists", {}).get("items", [])
    ) or "Unknown"
    return Track(
        index=index,
        title=d.get("name", "Unknown"),
        artists=artists,
        duration_ms=d.get("trackDuration", {}).get("totalMilliseconds", 0),
        spotify_uri=d.get("uri", ""),
        is_playable=d.get("playability", {}).get("playable", True),
    )


def _fetch_playlist_via_graphql(playlist_id: str) -> tuple[str, list[Track]]:
    """Fetch all tracks via Spotify's internal GraphQL API (single request, no rate limit)."""
    s = _graphql_session()
    c = _graphql_cached_token(s)
    variables = {"uri": f"spotify:playlist:{playlist_id}",
                 "offset": 0, "limit": 343,
                 "enableWatchFeedEntrypoint": False}
    r = _graphql_request(s, c, "fetchPlaylist", variables)
    data = r.json()
    playlist = data["data"]["playlistV2"]
    playlist_name = playlist.get("name", "Unknown Playlist")
    items = playlist.get("content", {}).get("items", [])

    tracks = []
    for item in items:
        item_data = item.get("itemV2", {}).get("data")
        if not item_data:
            continue
        tracks.append(_parse_track_data(item_data, len(tracks) + 1))

    print(f"[SPOTIFY] GraphQL fetched {len(tracks)} tracks", flush=True)
    return playlist_name, tracks


def _graphql_available() -> bool:
    return _HAS_PYOTP


# ---- Fallback: embed page scraping (original method) ----

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

    token = (
        data.get("props", {})
        .get("pageProps", {})
        .get("state", {})
        .get("settings", {})
        .get("session", {})
        .get("accessToken", "")
    )

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

    if token and len(tracks) > 0:
        headers = {"Authorization": f"Bearer {token}"}

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


def _fetch_single_track_via_embed(track_id: str) -> Track:
    embed_url = f"https://open.spotify.com/embed/track/{track_id}"
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
    return Track(
        index=1,
        title=entity.get("title", "Unknown"),
        artists=entity.get("subtitle", "Unknown"),
        duration_ms=entity.get("duration", 0),
        spotify_uri=entity.get("uri", ""),
        is_playable=entity.get("isPlayable", True),
    )


# ---- Public API (GraphQL first, embed fallback) ----

def fetch_tracks(playlist_url: str) -> tuple[str, list[Track]]:
    playlist_id = extract_playlist_id(playlist_url)
    if _graphql_available():
        try:
            return _fetch_playlist_via_graphql(playlist_id)
        except Exception as e:
            print(f"[SPOTIFY] GraphQL failed ({e}), falling back to embed", flush=True)
    return _fetch_via_embed_token(playlist_id)


def fetch_single_track(track_url: str) -> Track:
    track_id = extract_track_id(track_url)
    return _fetch_single_track_via_embed(track_id)
