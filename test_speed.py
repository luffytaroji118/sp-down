import urllib.request, json, subprocess, time
from pathlib import Path

# 1. Get PO token + audio URL
t0 = time.time()
url = 'https://po-production-93d7.up.railway.app/getPot?content_binding=dQw4w9WgXcQ&audio=1'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=25) as resp:
    data = json.loads(resp.read().decode('utf-8'))
print(f'API call: {time.time()-t0:.2f}s')
au = data.get('audioUrl', '')
print(f'  audioUrl: {len(au)} chars')
print(f'  audioMime: {data.get("audioMime")}')
print(f'  audioBitrate: {data.get("audioBitrate")}')

# 2. Download audio directly
t1 = time.time()
tmp_path = Path('test_download.m4a')
req2 = urllib.request.Request(au, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req2, timeout=30) as resp2, open(tmp_path, 'wb') as f:
    total = 0
    while True:
        chunk = resp2.read(1024*1024)
        if not chunk:
            break
        f.write(chunk)
        total += len(chunk)
print(f'Download: {time.time()-t1:.2f}s ({total/1048576:.1f} MB)')

# 3. Convert with ffmpeg
t2 = time.time()
out_path = Path('test_output.mp3')
result = subprocess.run(['ffmpeg', '-y', '-i', str(tmp_path), '-codec:a', 'mp3', '-b:a', '320k', str(out_path)], capture_output=True, timeout=30)
print(f'ffmpeg: {time.time()-t2:.2f}s (rc={result.returncode})')

if out_path.exists():
    print(f'Output: {out_path.stat().st_size/1048576:.1f} MB')
    out_path.unlink()
tmp_path.unlink(missing_ok=True)

print(f'TOTAL: {time.time()-t0:.2f}s')
