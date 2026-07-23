# Spotify Playlist Downloader

Paste a Spotify playlist URL and download all songs in high quality.

## How it works

1. Extracts track list from Spotify's public embed page (no API key needed)
2. Searches YouTube for each track using yt-dlp with smart matching (duration + keyword scoring)
3. Downloads and converts audio with FFmpeg
4. Zips all songs into a single download

## Format options

- MP3 320kbps
- MP3 128kbps
- FLAC (Lossless)
- M4A (AAC)

## Important: YouTube Bot Detection on Datacenter IPs

YouTube blocks automated requests from datacenter IPs (like Railway, AWS, etc.)
with a "Sign in to confirm you're not a bot" error. To fix this, you need to
provide YouTube cookies.

### Getting cookies

1. Install the [Get cookies.txt](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) browser extension
2. Log into YouTube in your browser
3. Click the extension and export cookies as `cookies.txt`
4. Base64 encode the file:
   ```bash
   base64 -w 0 cookies.txt
   ```
5. Set the `COOKIES_B64` environment variable on Railway with the base64 string

Alternatively, set `COOKIE_FILE` to a path where a cookies.txt file exists in the container.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8000` | Server port (set by Railway automatically) |
| `MAX_WORKERS` | `8` | Number of parallel downloads |
| `COOKIE_FILE` | _(empty)_ | Path to cookies.txt file |
| `COOKIES_B64` | _(empty)_ | Base64-encoded cookies.txt content |

## Local development

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Requires FFmpeg installed on your system.

## Docker

```bash
docker build -t spotify-downloader .
docker run -p 8000:8000 spotify-downloader
```

## Deploy to Railway

1. Push this repo to GitHub
2. Create a new project on [Railway](https://railway.app)
3. Deploy from GitHub repo
4. Set `COOKIES_B64` env var with your YouTube cookies (see above)
5. Railway auto-detects the Dockerfile and sets the PORT
