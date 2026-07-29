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
            try:
                await self._cleanup(playwright_obj, browser, context)
            finally:
                await asyncio.to_thread(self._sweep_orphan_chromium)
                self._sem.release()
            raise

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
                logger.warning(f"browser close 실패 (release의 스윕이 정리함): {e}")

        if playwright_obj:
            try:
                await asyncio.wait_for(playwright_obj.stop(), timeout=5)
            except Exception as e:
                logger.warning(f"playwright stop 실패: {e}")
                
    def _sweep_orphan_chromium(self):
        """release 시점에 남아있는 chromium/드라이버는 전부 고아 → 강제 정리"""
        killed = 0
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmd = ' '.join(proc.info['cmdline'] or [])
                if 'chrome-headless-shell' in cmd or 'headless_shell' in cmd:
                    proc.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if killed:
            logger.warning(f"고아 chromium {killed}개 강제 정리")

    async def release(self, playwright_obj, browser, context):
        try:
            # 기존 cleanup (context.close → browser.close → playwright.stop)
            await self._cleanup(playwright_obj, browser, context)
        finally:
            # cleanup 성공 여부와 무관하게 잔존 프로세스 스윕
            await asyncio.to_thread(self._sweep_orphan_chromium)
            self._sem.release()
global_browser_manager = BrowserManager()