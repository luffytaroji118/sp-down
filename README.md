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
4. Railway auto-detects the Dockerfile and sets the PORT
