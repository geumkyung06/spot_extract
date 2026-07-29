import asyncio
import threading
import psutil
from playwright.async_api import async_playwright
from services.my_logger import get_my_logger

logger = get_my_logger(__name__)

class BrowserManager:
    def __init__(self):
        # threading.Semaphore: 이벤트 루프와 무관하게 프로세스 전체에서 동작
        self._sem = threading.Semaphore(1)

    async def get_context(self, **kwargs):
        # 30초 안에 세마포어 못 잡으면 포기 (요청 무한 대기 방지)
        acquired = await asyncio.to_thread(self._sem.acquire, True, 30)
        if not acquired:
            raise RuntimeError("browser busy: acquire timeout")

        playwright_obj = browser = context = None
        try:
            playwright_obj = await async_playwright().start()
            browser = await asyncio.wait_for(
                playwright_obj.chromium.launch(
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
                    ],
                ),
                timeout=15,
            )
            context = await asyncio.wait_for(browser.new_context(**kwargs), timeout=15)
            return playwright_obj, browser, context
        except Exception:
            # 실패 시 여기서 반드시 전부 정리하고 세마포어 반환
            await self._cleanup(playwright_obj, browser, context)
            self._sem.release()
            raise

    async def release(self, playwright_obj, browser, context):
        try:
            await self._cleanup(playwright_obj, browser, context)
        finally:
            self._sem.release()

    async def _cleanup(self, playwright_obj, browser, context):
        if context:
            try:
                await asyncio.wait_for(context.close(), timeout=5)
            except Exception as e:
                logger.warning(f"context close 실패: {e}")

        if browser:
            try:
                await asyncio.wait_for(browser.close(), timeout=8)
            except Exception as e:
                logger.warning(f"browser close 실패, 강제 kill 시도: {e}")
                self._force_kill(browser)

        if playwright_obj:
            try:
                await asyncio.wait_for(playwright_obj.stop(), timeout=5)
            except Exception as e:
                logger.warning(f"playwright stop 실패: {e}")

    def _force_kill(self, browser):
        """close 실패 시 프로세스 트리째 강제 종료"""
        try:
            pid = browser.process.pid
            parent = psutil.Process(pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            parent.kill()
            logger.warning(f"chromium 강제 종료: PID {pid} + children")
        except Exception as e:
            logger.warning(f"강제 종료 실패: {e}")

global_browser_manager = BrowserManager()