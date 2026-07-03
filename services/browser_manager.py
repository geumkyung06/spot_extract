import asyncio
from playwright.async_api import async_playwright
from services.my_logger import get_my_logger

logger = get_my_logger(__name__)

class BrowserManager:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self._contexts = set()  # 열린 컨텍스트 추적
        self._sem = asyncio.Semaphore(2)  # 동시 컨텍스트 2개 제한
        

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
                "--single-process",              # renderer/GPU/zygote 프로세스 분리 안 함 → PID/메모리 크게 절감
                "--js-flags=--max-old-space-size=128",
            ]
        )
        logger.info("✅ 브라우저 준비 완료")

    async def new_context(self, **kwargs):
        await self._sem.acquire()
        try:
            context = await asyncio.wait_for(
                self.browser.new_context(
                    user_agent="Mozilla/5.0 (Linux; Android 10; SM-G981B)...",
                    **kwargs
                ),
                timeout=15
            )
        except Exception:
            self._sem.release()  # 실패했으면 반드시 permit 반환
            raise
        self._contexts.add(context)
        return context

    async def close_context(self, context):
        try:
            if context in self._contexts:
                self._contexts.discard(context)
                await asyncio.wait_for(context.close(), timeout=10)
        except Exception as e:
            print(f"컨텍스트 닫기 실패: {e}")
        finally:
            self._sem.release()

    async def restart(self):
        """브라우저가 응답 없을 때 강제 재생성"""
        logger.info("⚠️ 브라우저 강제 재시작")
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        self.browser = None
        self._contexts.clear()
        self._sem = asyncio.Semaphore(1)  # permit 누수 전부 초기화. 일단 안정성 챙기기
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