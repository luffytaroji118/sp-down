import asyncio
import os
import time
import threading
from pathlib import Path
from typing import Optional

COOKIE_PATH = os.path.join(os.environ.get("COOKIE_DIR", "/tmp"), "yt_cookies.txt")

_lock = threading.Lock()
_last_fetch = 0.0
_fetch_interval = 1800  # 30 minutes


def _format_cookie_file(cookies: list[dict]) -> str:
    lines = ["# Netscape HTTP Cookie File", ""]
    for c in cookies:
        name = c.get("name", "")
        value = c.get("value", "")
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure", True) else "FALSE"
        expiry = int(c.get("expires", time.time() + 86400 * 365))
        host = c.get("domain", ".youtube.com")
        lines.append(f"{host}\tTRUE\t{path}\t{secure}\t{expiry}\t{name}\t{value}")
    return "\n".join(lines) + "\n"


async def _fetch_cookies_async() -> Optional[str]:
    from pydoll.browser.chromium import Chrome
    from pydoll.browser.options import ChromiumOptions

    print("[COOKIES] Launching Pydoll browser to fetch YouTube cookies...", flush=True)
    try:
        chrome_path = os.environ.get("CHROME_PATH", "/usr/bin/chromium")
        options = ChromiumOptions()
        options.binary_location = chrome_path
        options.headless = True
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.start_timeout = 15

        async with Chrome(options=options) as browser:
            tab = await browser.start()

            try:
                await asyncio.wait_for(
                    tab.go_to("https://www.youtube.com/"), timeout=15
                )
            except asyncio.TimeoutError:
                print("[COOKIES] YouTube homepage load timed out", flush=True)
            except Exception as e:
                print(f"[COOKIES] Homepage navigation error: {e}", flush=True)

            await asyncio.sleep(5)

            try:
                await tab.go_to("https://www.youtube.com/feed/trending")
                await asyncio.sleep(3)
            except Exception:
                pass

            try:
                await tab.go_to("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
                await asyncio.sleep(3)
            except Exception:
                pass

            cookies = await browser.get_cookies()
            await browser.close()

        if not cookies:
            print("[COOKIES] No cookies captured", flush=True)
            return None

        cookie_text = _format_cookie_file(cookies)
        Path(COOKIE_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(COOKIE_PATH, "w") as f:
            f.write(cookie_text)

        print(f"[COOKIES] Saved {len(cookies)} cookies to {COOKIE_PATH}", flush=True)
        return COOKIE_PATH

    except Exception as e:
        print(f"[COOKIES] Pydoll fetch failed: {e}", flush=True)
        return None


def fetch_cookies() -> Optional[str]:
    try:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(_fetch_cookies_async())
        loop.close()
        return result
    except Exception as e:
        print(f"[COOKIES] Async fetch failed: {e}", flush=True)
        return None


def get_cookie_file() -> Optional[str]:
    global _last_fetch

    with _lock:
        now = time.time()
        need_fetch = (
            not os.path.isfile(COOKIE_PATH)
            or now - _last_fetch > _fetch_interval
        )

        if need_fetch:
            result = fetch_cookies()
            _last_fetch = now
            return result

        return COOKIE_PATH if os.path.isfile(COOKIE_PATH) else None


def cookie_fetcher_loop():
    while True:
        try:
            get_cookie_file()
        except Exception as e:
            print(f"[COOKIES] Loop error: {e}", flush=True)
        time.sleep(_fetch_interval)


def start_cookie_fetcher():
    t = threading.Thread(target=cookie_fetcher_loop, daemon=True)
    t.start()
    print("[COOKIES] Background Pydoll fetcher started", flush=True)
