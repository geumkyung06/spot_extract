from flask import request
import json, os, re, io
import asyncio, aiohttp
import uuid
import logging
import html
from playwright.async_api import async_playwright
from google import genai
from google.genai import types
from PIL import Image

# 설정
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)
sem = asyncio.Semaphore(3) # OCR 동시 요청 제한

# 브라우저 매니저
class BrowserManager:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None

    async def start(self):
        """서버 시작 시 또는 최초 요청 시 실행"""
        if self.browser is not None:
            return # 이미 켜져 있으면 패스

        self.playwright = await async_playwright().start()
        # 리눅스 호환성 및 속도 최적화 옵션 추가
        self.browser = await self.playwright.chromium.launch(
            headless=True, 
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        # 컨텍스트(세션) 미리 생성
        self.context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36"
        )
        print("✅ 브라우저(Warm) 준비 완료")

    async def stop(self):
        """서버 완전 종료 시에만 호출"""
        if self.context: await self.context.close()
        if self.browser: await self.browser.close()
        if self.playwright: await self.playwright.stop()
        self.browser = None
        print("🛑 브라우저 종료")

# 전역 브라우저 인스턴스 (재사용을 위해 함수 밖으로 뺌)
global_browser_manager = BrowserManager()

# 이미지 처리
def crop_and_save_image(image_data, cut_height=250):
    try:
        with Image.open(io.BytesIO(image_data)) as img:
            w, h = img.size
        
            # 1. 윗부분 크롭 (상단 불필요 정보 제거)
            if h > cut_height:
                crop_box = (0, cut_height, w, h)
                img = img.crop(crop_box)
            
            # 2. 리사이징 (BILINEAR: 속도 최우선)
            max_size = 800
            if max(img.size) > max_size:
                ratio = max_size / max(img.size)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.BILINEAR)
            
            # 3. 흑백 변환 (OCR 인식률 유지하면서 용량 감소)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img = img.convert("L")

            # 4. 저장 (optimize=False로 저장 속도 확보)
            output_buffer = io.BytesIO()
            img.save(output_buffer, format='JPEG', quality=50)
            output_buffer.seek(0) # 포인터 초기화
            
            return output_buffer, img

    except Exception as e:
        print(f"이미지 처리 에러: {e}")
        return None

# 크롤링 로직 (리소스 차단 및 타임아웃 단축)
async def extract_images(browser_manager: BrowserManager, post_url: str):
    ordered_images = [] 
    seen_base_urls = set()

    # 이미 켜져 있는 context에서 '탭'만 새로 엶
    page = await browser_manager.context.new_page()

    # 이미지, 폰트, 미디어 차단
    await page.route("**/*", lambda route: 
        route.abort() if route.request.resource_type in ["media", "font", "stylesheet"] 
        else route.continue_()
    )

    # 정규식 패턴: < or " 가 나오기 전까지의 URL 캡처
    url_pattern = r'https://scontent[^\s"\'<]+|https:\\/\\/scontent[^\s"\'<]+'
    dimension_pattern = re.compile(r'[ps]\d{2,4}x\d{2,4}')

    def process_and_add(raw_urls):
        for raw_url in raw_urls:
            # 꼬리 자르기
            clean_url = raw_url.split('\\u003C')[0].split('<')[0]
            clean_url = clean_url.split('\\u0022')[0].split('"')[0]

            # 디코딩
            clean_url = clean_url.replace('\\/', '/')
            clean_url = clean_url.replace('\\u0026', '&')
            clean_url = clean_url.replace('\\u0025', '%') 
            clean_url = html.unescape(clean_url)
            
            # 필러링 로직(비디오 등)
            if ".mp4" in clean_url: continue 
            if "dash" in clean_url or "segment" in clean_url.lower(): continue 
            if "/t51.2885-19/" in clean_url: continue 
            if "vp/" in clean_url: continue 
            
            # 고화질 추출 (아이콘, 작은 썸네일 제거)
            if dimension_pattern.search(clean_url): continue # 예) p640x640 방지
            if re.search(r'\/s\d{3,4}x\d{3,4}\/', clean_url): continue # 예) /s320x320/ 방지
            if "c0." in clean_url: continue # 크롭된 썸네일 방지
            
            base_url = clean_url.split('?')[0]
            
            if base_url not in seen_base_urls:
                seen_base_urls.add(base_url)
                ordered_images.append(clean_url)

    async def handle_response(response):
        if "graphql/query" in response.url or "api/v1" in response.url:
            try:
                body = await response.text()
                matches = re.findall(url_pattern, body)
                process_and_add(matches)
            except:
                pass

    page.on("response", handle_response)

    try:
        await page.goto(post_url, wait_until="domcontentloaded", timeout=10000)
        
        # HTML에서 1차 추출
        html_content = await page.content()
        process_and_add(re.findall(url_pattern, html_content))

        # 백그라운드 API 통신 대기 (제한 없이 3초 풀 대기)
        for _ in range(6): 
            await asyncio.sleep(0.5)

        # 안전장치: 너무 많이 잡히면 앞부분(메인사진)만 자름
        if len(ordered_images) > 10:
            ordered_images = ordered_images[:10]

    except Exception as e:
        print(f"추출 에러: {e}")
    finally:
        await page.close()

    return ordered_images

# 다운로드
async def process_download(session, img_url):
    try:
        async with session.get(img_url) as response:
            if response.status == 200:
                data = await response.read()
                
                result = await asyncio.to_thread(crop_and_save_image, data, 150)
                # 크롭 결과가 None인지 확인
                if not result: 
                    print(f"크롭 결과 없음: {result}]")
                    return None, None

                byte_buffer, pil_image = result # 언팩 에러 방지
                async with sem:
                    ocr_result = await asyncio.to_thread(gemini_flash_ocr, pil_image)
                return ocr_result

    except Exception as e:
        print(f"개별 처리 에러: {e}")
        return None

def gemini_flash_ocr(pil_image):
    try:
 
        response = client.models.generate_content(
        model='gemini-2.5-flash-lite',
        contents=[
            """이미지에서 텍스트를 추출할 때 다음 규칙을 절대적으로 준수해:
            1. 너의 배경지식을 활용해 단어를 '교정'하거나 '추정'하지 마.
            2. '삼원샏'처럼 한국어 맞춤법에 어긋나거나 생소한 단어라도 이미지에 보이는 '모양 그대로' 추출해.
            3. 글자가 뭉쳐있다면 'ㅅ, ㅏ, ㅁ, ㅇ, ㅜ, ㅓ, ㄴ, ㅅ, ㅐ, ㄷ' 처럼 자음과 모음을 하나씩 꼼꼼히 확인해.
            4. 이미지에서 모든 텍스트를 추출(raw_text)한 뒤, 그 내용을 바탕으로 상호명과 주소(places)를 구분해서 정리해줘.""",
            pil_image
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json", 
            temperature=0.1, # 좀 더 테스트
            top_p=0.1,   
            response_schema={
                "type": "OBJECT",
                "properties": {
                    "raw_text": {
                        "type": "STRING", 
                        "description": "이미지에 보이는 모든 텍스트를 빠짐없이 있는 그대로 먼저 다 적어."
                    },
                    "places": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "name": {"type": "STRING", "description": "가게 이름 (ex: 코히루). 없으면 빈 문자열"},
                                "address": {"type": "STRING", "description": "도로명/지번 주소 (ex: 서울 중구 동호로...). 없으면 빈 문자열"}
                            }
                        }
                    }
                }
            }
        )
    )  
        
        data = json.loads(response.text)
        
        places_list = data.get('places', [])
        valid_data = [item for item in places_list if item.get('name') and item['name'].strip() != ""]
        
        # 정제 로직
        for item in valid_data:
            clean_addr = re.sub(r'#\S+', '', item.get('address', ''))
            clean_addr = re.sub(r'[^\w\s\(\)\-,.]', '', clean_addr) 
            clean_name = item.get('name', '').replace('#', '')
            clean_name = re.sub(r'[^\w\s\(\)\-,.&\'\+]', '', clean_name)
            
            item['address'] = clean_addr.strip()
            item['name'] = clean_name.strip()
        
        return valid_data
    except Exception as e:
        return {"error": f"에러 발생: {str(e)}"}

# 메인
async def extract_insta_images(url=""):
    # [중요] 전역 브라우저 매니저 사용
    # 처음 실행될 때만 start()가 작동하고, 이후에는 무시됨 (Warm Start 효과)
    await global_browser_manager.start()

    # Flask request 객체 처리 (JSON 바디가 없으면 인자 url 사용)
    target_url = url
    try:
        if request:
            data = request.get_json(silent=True)
            if data and 'url' in data:
                target_url = data.get('url')
    except RuntimeError:
        pass # Flask context 밖에서 실행될 경우 대비

    ocr_results = []
    keys_to_delete = [] # 삭제할 파일 목록
    
    try:        
        # 전역 매니저를 넘겨줌
        image_urls = await extract_images(global_browser_manager, target_url)
        print(image_urls[1])
        print(f"{len(image_urls)}장 URL 확보 완료")

        if image_urls:
            print("이미지 다운로드 및 변환 중...")
            connector = aiohttp.TCPConnector(limit=10)
            async with aiohttp.ClientSession(connector=connector) as session:
                tasks = [process_download(session, img_url) for img_url in image_urls[1:]]
                results = await asyncio.gather(*tasks)
            
            # 2차원 리스트([[{},{}], [{},{}]])를 1차원으로 평탄화
            for res in results:                
                if res:
                    if isinstance(res, list):
                        ocr_results.extend(res)
                    elif isinstance(res, dict) and "error" not in res:
                        ocr_results.append(res)
            print("OCR 결과: {ocr_results}")
    except Exception as e:
        print(f"전체 프로세스 에러: {e}")
        return {"error": str(e)}
    
    return image_urls, ocr_results


