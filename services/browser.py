import os
from playwright.async_api import async_playwright

class BrowserManager:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None

    async def start(self):
        if self.browser: return 
        
        self.playwright = await async_playwright().start()
        
        # 브라우저 실행 (가계정 보호를 위해 느린 동작 옵션 추가 가능)
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        
        # auth.json 파일 존재 여부 확인
        # 파일이 있어야만 세션 불러오기, 없으면 일반 브라우저로
        storage_state = "auth.json" if os.path.exists("auth.json") else None
        
        if storage_state:
            print(f"로그인 세션({storage_state})을 로드합니다.")
        else:
            print("로그인 세션 파일이 없어 비로그인 상태로 시작합니다.")

        # 컨텍스트 생성 (세션 주입)
        self.context = await self.browser.new_context(
            storage_state=storage_state,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
        )
        print("브라우저 및 컨텍스트 준비 완료")

    async def stop(self):
        if self.context: await self.context.close()
        if self.browser: await self.browser.close()
        if self.playwright: await self.playwright.stop()
        self.browser = None # 상태 초기화
        print("브라우저 종료")

# 싱글톤 패턴처럼 사용하기 위해 인스턴스 생성
browser_service = BrowserManager()


'''
# 프록시 사용하게 될 시 
self.browser = await self.playwright.chromium.launch(
    headless=True,
    proxy={
        "server": "http://p.webshare.io:80", # 프록시 서버 주소
        "username": "your_username",         # 프록시 아이디
        "password": "your_password"          # 프록시 비밀번호
    },
    args=["--no-sandbox", "--disable-setuid-sandbox"]
)
'''