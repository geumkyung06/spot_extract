import asyncio
import psutil
from playwright.async_api import async_playwright
from services.my_logger import get_my_logger

logger = get_my_logger(__name__)

class BrowserManager:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self._contexts = set()  # 열린 컨텍스트 추적
        self._sem = asyncio.Semaphore(1)  # 동시 컨텍스트 제한. 메모리 문제 해결되면 늘리기
        
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
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--disable-translate",
                "--metrics-recording-only",
                "--mute-audio",
                "--js-flags=--max-old-space-size=128",
            ]
        )
        logger.info("✅ 브라우저 준비 완료")

    async def new_context(self, **kwargs):
        await self._sem.acquire()
        try:
            if not self.browser or not self.browser.is_connected():
                logger.info("⚠️ 브라우저 연결 끊김 감지, 재시작")
                await self.restart()
            try:
                context = await asyncio.wait_for(
                    self.browser.new_context(
                        user_agent="Mozilla/5.0 (Linux; Android 10; SM-G981B)...",
                        **kwargs
                    ),
                    timeout=15
                )
            except asyncio.TimeoutError:
                logger.warning("⚠️ new_context 타임아웃 - 브라우저 응답 없음, 강제 재시작")
                await self.restart()
                raise
        except Exception:
            self._sem.release()  # restart 실패든 new_context 실패든 무조건 반환
            raise
        self._contexts.add(context)
        return context

    async def close_context(self, context):
        try:
            if context in self._contexts:
                self._contexts.discard(context)
                await asyncio.wait_for(context.close(), timeout=10)
        except Exception as e:
            logger.error(f"컨텍스트 닫기 실패: {e}")
        finally:
            self._sem.release()

    def _force_kill_chromium(self):
        killed = 0
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = ' '.join(proc.info.get('cmdline') or [])
                # Playwright driver(run-driver)는 제외하고 Chromium만 타겟팅
                if 'run-driver' in cmdline:
                    continue
                if 'headless_shell' in cmdline or ('chrome' in cmdline.lower() and 'node' not in cmdline.lower()):
                    logger.warning(f"강제 종료: PID {proc.info['pid']}")
                    proc.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        logger.info(f"강제 종료된 프로세스 수: {killed}")

    async def restart(self):
        """브라우저가 응답 없을 때 강제 재생성"""
        logger.info("⚠️ 브라우저 강제 재시작")

        try:
            if self.browser:
                await asyncio.wait_for(self.browser.close(), timeout=5)
        except Exception as e:
            logger.warning(f"정상 close 실패/타임아웃({e}), 프로세스 강제 종료 시도")
            self._force_kill_chromium()

        try:
            if self.playwright:
                await asyncio.wait_for(self.playwright.stop(), timeout=5)
        except Exception as e:
            logger.warning(f"playwright stop 실패/타임아웃: {e}")

        self.browser = None
        self.playwright = None
        self._contexts.clear()

        await self.start()

    async def stop(self):
        for ctx in list(self._contexts):
            await self.close_context(ctx)
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self.browser = None
        logger.info("🛑 브라우저 종료")

global_browser_manager = BrowserManager()