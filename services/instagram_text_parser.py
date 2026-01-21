import asyncio
import json
import re
import os
from playwright.async_api import async_playwright
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

KOREAN_REGIONS = [
    # 1. 광역자치단체
    "서울", "서울특별시", "부산", "부산광역시",
    "인천", "인천광역시", "대구", "대구광역시", "대전", "대전광역시",
    "광주", "광주광역시", "울산", "울산광역시", "세종", "세종특별자치시",
    "강원", "강원도", "제주", "제주도",
    "경기", "경기도", "충북", "충청북도", "충남", "충청남도",
    "전북", "전라북도", "전남", "전라남도", "경북", "경상북도",
    "경남", "경상남도",

    # 2. 유명한 거
    "분당", "판교",      
    "일산",       
    "동탄",         
    "송도", "청라",   
    "위례",              
    "대부도", "제부도", 
    "잠실", "석촌", "성수", "강남", "명동", "중구", "홍대", "마포", "연남", "상수", "압구정", "종로", "이태원", "한남", "동대문", "뚝섬"

    # 3. 경기도 도시
    "수원", "수원시", "성남", "성남시", "의정부", "의정부시",
    "안양", "안양시", "부천", "부천시", "광명", "광명시",
    "평택", "평택시", "동두천", "동두천시", "안산", "안산시",
    "고양", "고양시", "과천", "과천시", "구리", "구리시",
    "남양주", "남양주시", "오산", "오산시", "시흥", "시흥시",
    "군포", "군포시", "의왕", "의왕시", "하남", "하남시",
    "용인", "용인시", "파주", "파주시", "이천", "이천시",
    "안성", "안성시", "김포", "김포시", "화성", "화성시",
    "광주", "광주시", "양주", "양주시", "포천", "포천시",
    "여주", "여주시", 
    
    "연천", "연천군", "가평", "가평군", "양평", "양평군"
    ]

async def get_caption_no_login(post_url: str):
    caption_text = ""
    async with async_playwright() as p:
        
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
            locale="ko-KR",
            viewport={"width": 360, "height": 800} 
        )
        page = await context.new_page()
        
        await page.route("**/*", lambda route: 
            route.abort() if route.request.resource_type in ["image", "media", "font"] 
            else route.continue_()
        )

        try:
            await page.goto(post_url, wait_until="domcontentloaded", timeout=15000)
            
            print("1단계: 일반 게시물 로직 시도...")
            
            # 일반 포스트
            # 1-1. JSON-LD
            try:
                json_ld_handle = await page.query_selector('script[type="application/ld+json"]')
                if json_ld_handle:
                    json_text = await json_ld_handle.inner_text()
                    data = json.loads(json_text)
                    
                    if "caption" in data:
                        caption_text = data["caption"]
                    elif "articleBody" in data:
                        caption_text = data["articleBody"]
                    
                    if caption_text:
                        print(">> 1단계 JSON-LD 성공")
            except: pass

            # 1-2. 정규식
            if not caption_text:
                try:
                    content = await page.content()
                    patterns = [
                        r'"edge_media_to_caption"\s*:\s*\{\s*"edges"\s*:\s*\[\s*\{\s*"node"\s*:\s*\{\s*"text"\s*:\s*"([^"]+)"',
                        r'"caption"\s*:\s*\{\s*"text"\s*:\s*"([^"]+)"'
                    ]
                    for pattern in patterns:
                        match = re.search(pattern, content)
                        if match:
                            raw_text = match.group(1)
                            caption_text = json.loads(f'"{raw_text}"')
                            print(">> 1단계 정규식 성공")
                            break
                except: pass


            # 릴스
            if not caption_text:
                print("1단계 실패. 2단계(릴스 로직)로 전환합니다...")

                # 2-1. Meta Tag 추출
                try:
                    meta_desc = await page.get_attribute('meta[property="og:description"]', 'content')
                    if meta_desc:
                        if ": \"" in meta_desc:
                            caption_text = meta_desc.split(": \"", 1)[1].rsplit("\"", 1)[0]
                        elif ": “" in meta_desc:
                             caption_text = meta_desc.split(": “", 1)[1].rsplit("”", 1)[0]
                        else:
                            caption_text = meta_desc
                        
                        if caption_text:
                            print(">> 2단계 Meta Tag 성공")
                except: pass

                # 2-2. UI 요소 직접 추출
                if not caption_text:
                    try:
                        # 릴스나 게시물의 본문은 보통 h1 태그나 특정 클래스에 있음
                        element = await page.query_selector('h1')
                        if not element:
                            element = await page.query_selector('div[data-testid="content-container"] span')
                        
                        if element:
                            caption_text = await element.inner_text()
                            print(">> 2단계 UI 요소 추출 성공")
                    except: pass

                # 2-3. 정규식 
                if not caption_text:
                    try:
                        content = await page.content() 
                        reel_pattern = r'"clips_metadata"\s*:\s*\{.*?"caption"\s*:\s*"([^"]+)"'
                        
                        match = re.search(reel_pattern, content)
                        if match:
                            raw_text = match.group(1)
                            caption_text = json.loads(f'"{raw_text}"')
                            print(">> 2단계 릴스 정규식 성공")
                    except: pass

        except Exception as e:
            print(f"Playwright 에러: {e}")
        
        await browser.close()

    return caption_text

def check_time_line(lines):
    strong_keywords = ['영업', '운영', '오픈', '마감', '휴무', '휴일']
    time_patterns = [
        r'\d{1,2}:\d{2}', r'\d{1,2}\s*시', r'\d{1,2}\s*~', r'[AaPp][Mm]\s*\d{1,2}'
    ]
    day_patterns = [
        r'[/(]\s*[월화수목금토일]\s*[)/]', r'[월화수목금토일]\s*요일',
        r'[월화수목금토일]\s*[-~]\s*[월화수목금토일]', r'\d{2,4}[\.\/\-]\d{1,2}[\.\/\-]\d{1,2}'
    ]

    found_time_index = -1
    for idx, line in enumerate(lines):
        is_time_line = False
        if any(k in line for k in strong_keywords): is_time_line = True
        if not is_time_line:
            if any(re.search(p, line) for p in time_patterns): is_time_line = True
            elif any(re.search(p, line) for p in day_patterns): is_time_line = True
        
        if is_time_line:
            found_time_index = idx
            break 
    
    if found_time_index > 0: 
        real_address = lines[:found_time_index]
        return "\n".join(real_address)
    else:
        return None

# 규칙 기반 수정해야함 -> 그전까지 일단 ai만 사용
def split_caption(caption):
    if not caption: return [], []

    spt_caption = [block.strip() for block in caption.split("\n\n") if block.strip()]
    if spt_caption: spt_caption.pop(0)

    explanation = []
    place = []

    # 장소명 
    strong_keywords = ["매장", "주소", "위치"]
    
    detail_pattern = re.compile(r'[가-힣]+(시|구|군|동|읍|면|로|길)')
    number_pattern = re.compile(r'\d+(-\d+)?')

    for block in spt_caption:
        matched_region = next((region for region in KOREAN_REGIONS if region in block), None)

        has_strong_keyword = any(k in block for k in strong_keywords)

        if matched_region and (detail_pattern.search(block) or number_pattern.search(block)) or has_strong_keyword:
            if "!" not in block:
                lines = block.splitlines()
                time_check_text = check_time_line(lines)

                if time_check_text: 
                    target_line = time_check_text 
                else : 
                    target_line = "\n".join(lines[:3])

                place.append(target_line)

            else: 
                explanation.append(block)
        else:
            explanation.append(block)
            
    if not place:
        for block in explanation[:]: 
            lines = block.splitlines()
            add_caption = check_time_line(lines)

            if add_caption:
                place.append(add_caption)
                explanation.remove(block)
    
    # 제거 리스트
    remove_prefixes = ["매장명", "매장", "주소", "위치"]

    for idx, info in enumerate(place):
        for prefix in remove_prefixes:
            if prefix in info:
                info = info.split(prefix, 1)[1]
                info = info.lstrip(" :").strip()

        info = re.sub(r'@[a-zA-Z0-9_.]+', '', info)
        info = re.sub(r'[^가-힣a-zA-Z0-9\s]', '', info)
        info = " ".join(info.split())
        place[idx] = info

    return place, caption

def extract_places_with_gpt(caption):
    """
    GPT-4o-mini를 사용하여 캡션에서 장소 정보를 정형화된 JSON으로 추출
    """
    if not caption:
        return [], "없음"

    try:
        # 프롬프트: AI에게 역할을 부여하고 출력 형식을 강제함
        response = client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[
                {
                    "role": "system",
                    "content": (
                        "너는 인스타그램 캡션에서 '상호명(name)'과 '주소/위치(address)'를 추출하는 전문 AI야. "
                        "다음 규칙을 반드시 지켜:\n"
                        "1. 결과는 반드시 JSON 형식으로만 출력해.\n"
                        "2. 장소가 여러 곳이면 배열에 담아.\n"
                        "3. 장소 언급이 없으면 빈 리스트를 반환해.\n"
                        "4. '가고 싶다' 같은 단순 희망 사항은 제외하고, 실제 방문하거나 추천한 곳만 추출해.\n\n"
                        
                        "출력 예시 포맷:\n"
                        "{\n"
                        "  'places': [{'name': '상호명1', 'address': '주소1'}, ...]"
                        "}"
                    )
                },
                {"role": "user", "content": caption}
            ],
            temperature=0,  # 창의성 0 (정확한 추출 위함)
            response_format={"type": "json_object"}  # JSON 강제 모드
        )

        # 결과 파싱
        result = json.loads(response.choices[0].message.content)
        places = result.get('places', [])
        return places

    except Exception as e:
        print(f"GPT Error: {e}")
        return [], "에러"

def is_place_post(caption):
    if not caption:
        return [], "없음"

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",  
            messages=[
                {
                    "role": "system",
                    "content": (
                        "해당 캡션이 장소를 설명하는 글인지 확인하고 True/Flase로 출력해"
                    )
                },
                {"role": "user", "content": caption}
            ],
            temperature=0, # 창의성 0 
        )

        # 결과 파싱
        result = json.loads(response.choices[0].message.content)
        return result

    except Exception as e:
        print(f"GPT Error: {e}")
        return [], "에러"
