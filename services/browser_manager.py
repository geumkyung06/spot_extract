# services/browser_manager.py (새 파일)
import asyncio
from playwright.async_api import async_playwright

browser_sem = asyncio.Semaphore(2)

class BrowserManager:
    def __init__(self):
        self.playwright = None
        self.browser = None

    async def start(self):
        if self.browser is not None:
            return
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ]
        )
        print("✅ 브라우저 준비 완료")

    async def new_context(self, **kwargs):
        return await self.browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 10; SM-G981B)...",
            **kwargs
        )

    async def stop(self):
        if self.browser: await self.browser.close()
        if self.playwright: await self.playwright.stop()
        self.browser = None
        print("🛑 브라우저 종료")

# 전역 인스턴스
global_browser_manager = BrowserManager()