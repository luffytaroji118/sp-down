import asyncio
import time
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from playwright.async_api import async_playwright

app = FastAPI(title="Cookie Service")

_last_cookies: list[dict] = []
_last_fetch: float = 0
_FETCH_INTERVAL = 1800  # 30 min
_lock = asyncio.Lock()


def _to_netscape(cookies: list[dict]) -> str:
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


async def _fetch() -> Optional[list[dict]]:
    print("[COOKIE-SERVICE] Launching browser...", flush=True)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            page = await context.new_page()

            await page.goto("https://www.youtube.com/", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(4)

            try:
                btn = page.locator('button[aria-label="Accept all"]')
                if await btn.count() > 0:
                    await btn.click()
                    await asyncio.sleep(2)
            except Exception:
                pass

            try:
                await page.goto("https://www.youtube.com/watch?v=dQw4w9WgXcQ", wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(3)
            except Exception:
                pass

            cookies = await context.cookies(["https://www.youtube.com"])
            await browser.close()

            print(f"[COOKIE-SERVICE] Captured {len(cookies)} cookies", flush=True)
            return cookies if cookies else None
    except Exception as e:
        print(f"[COOKIE-SERVICE] Error: {e}", flush=True)
        return None


async def _get_cookies() -> Optional[list[dict]]:
    global _last_cookies, _last_fetch
    async with _lock:
        now = time.time()
        if _last_cookies and now - _last_fetch < _FETCH_INTERVAL:
            return _last_cookies
        result = await _fetch()
        if result:
            _last_cookies = result
            _last_fetch = now
        return result or _last_cookies


@app.get("/")
async def health():
    return {"status": "ok", "cookies_cached": len(_last_cookies)}


@app.get("/cookies")
async def get_cookies():
    cookies = await _get_cookies()
    if not cookies:
        return JSONResponse({"error": "no cookies"}, status_code=500)
    return JSONResponse({"cookies": cookies, "count": len(cookies)})


@app.get("/cookies.txt", response_class=PlainTextResponse)
async def get_cookies_txt():
    cookies = await _get_cookies()
    if not cookies:
        return PlainTextResponse("# no cookies", status_code=500)
    return _to_netscape(cookies)


@app.post("/refresh")
async def refresh():
    global _last_fetch
    _last_fetch = 0
    cookies = await _get_cookies()
    if cookies:
        return {"status": "ok", "count": len(cookies)}
    return JSONResponse({"error": "no cookies"}, status_code=500)


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
