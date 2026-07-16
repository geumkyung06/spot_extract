import asyncio
import psutil
from playwright.async_api import async_playwright
from services.my_logger import get_my_logger

logger = get_my_logger(__name__)

class BrowserManager:
    def __init__(self):
        self._sem = asyncio.Semaphore(1)  # 동시 실행 1개로 제한. 메모리 문제 해결되면 늘리기

    async def get_context(self, **kwargs):
        """요청마다 완전히 독립된 브라우저+컨텍스트를 새로 띄움"""
        await self._sem.acquire()
        try:
            playwright_obj = await async_playwright().start()
            browser = await asyncio.wait_for(
                playwright_obj.chromium.launch(
                    channel="chromium",
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-extensions",
                        "--disable-background-networking",
                        "--disable-default-apps",
                        "--disable-sync",
                        "--disable-translate",
                        "--metrics-recording-only",
                        "--mute-audio",
                        "--js-flags=--max-old-space-size=128",
                    ]
                ),
                timeout=15
            )
            context = await asyncio.wait_for(
                browser.new_context(
                    user_agent="Mozilla/5.0 (Linux; Android 10; SM-G981B)...",
                    **kwargs
                ),
                timeout=15
            )
            return playwright_obj, context
        except Exception:
            self._sem.release()
            raise

    async def release(self, playwright_obj, context):
        """요청 끝나면 브라우저 전체를 통째로 폐기"""
        try:
            if context:
                await asyncio.wait_for(context.close(), timeout=5)
        except Exception as e:
            logger.warning(f"context close 실패(무시): {e}")

        try:
            if playwright_obj:
                await asyncio.wait_for(playwright_obj.stop(), timeout=3)
        except Exception as e:
            logger.warning(f"playwright stop 실패(무시): {e}")

        self._sem.release()

    def _force_kill(self, browser):
        try:
            pid = browser.process.pid
            psutil.Process(pid).kill()
            logger.warning(f"강제 종료: PID {pid}")
        except Exception as e:
            logger.warning(f"강제 종료 실패: {e}")

global_browser_manager = BrowserManager()