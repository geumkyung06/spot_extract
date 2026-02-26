import asyncio
import json
import re
import os
from playwright.async_api import async_playwright
from openai import OpenAI
import logging

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
    success_step = "failed"

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
            
            logging.debug("1단계: 일반 게시물 로직 시도...")
            
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
                        success_step = "1-1 (JSON-LD)"
            except: pass

            # 1-2. 정규식(Regex)
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
                            success_step = "1-2 (Regex)"
                            break
                except: pass


            # 릴스
            if not caption_text:
                logging.debug("1단계 실패. 2단계(릴스 로직)로 전환합니다...")

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
                            success_step = "2-1 (Reels Logic)"
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
                            success_step = "2-2 (Reels UI element)"
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
                            success_step = "2-3 (Reels Regex)"
                    except: pass
                
                if caption_text:
                    # 성공 시: URL, 성공한 단계, 본문 앞부분 출력
                    logging.info(f"[SUCCESS] {post_url} | Step: {success_step} | Text: {caption_text[:10]}...")
                else:
                    logging.warning(f"[FAILED] {post_url} | 캡션을 찾지 못했습니다.")

        except Exception as e:
            logging.error(f"Playwright 에러: {e}")
        
        await browser.close()

    return caption_text

def clean_text(text):
    if not text:
        return ""
    # 1. 이모지 및 특수문자 제거 (한글, 영문, 숫자, 공백, 기본 문장부호만 남김)
    # 가-힣: 한글, a-zA-Z: 영문, 0-9: 숫자, \s: 공백, \. \, \! \?: 기본 부호
    cleaned = re.sub(r'@.*', '', text)
    cleaned = re.sub(r'[^ㄱ-ㅣ가-힣a-zA-Z0-9\s\.\,\!\?\#\-\+\:]', '', cleaned)
    
    cleaned.strip()
    # 괄호도 빼기 > 띄어쓰기로 변환
    lines = re.split(r'\n{2,}', cleaned)

    # 해시태그 여럿이면 그 부분 삭제    
    cleaned_list = []
    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            continue
            
        # 해시태그 과다 문단 제거
        if stripped_line.count("#") > 1:
            continue
            
        # 한글 비중 체크 (영어만 있는 문단 제거)
        # 10% 미만이면 영어 문단으로 간주하고 버림
        if not is_korean_content(stripped_line, threshold=0.1):
            logging.debug(f"영어 문단 제외됨: {stripped_line[:20]}...")
            continue
            
        cleaned_list.append(stripped_line)
    
    final_text = "\n\n".join(cleaned_list)
    return final_text, cleaned_list

def is_korean_content(text, threshold=0.1):
    # 문단 내 한글 글자 수 계산
    korean_chars = len(re.findall(r'[가-힣]', text))
    total_chars = len(text.strip().replace(" ", "")) # 공백 제외 전체 글자 수
    
    if total_chars == 0: return False
    
    # 한글 비율 계산
    ratio = korean_chars / total_chars
    return ratio >= threshold

def list_chunk(lst, n):
    return [lst[i:i+n] for i in range(0, len(lst), n)]

def check_rulebase_place(list_caption: list): 
    list_caption_with_ratio = [] # 이중리스트, [ratio, 문장]

    allowed_tags = ["NNG","NNP", "NNB", "NNM","NR","NP","ON", "UN", "OL"] # 명사 비율 확인
    
    # 주소
    combined_addr_pattern = re.compile(r'[가-힣]+(시|구|군|동|읍|면|로|길)\s?\d+')

    address_idxs = []
    
    kkma = Kkma()
    for idx, splits in enumerate(str(list_caption).split("\n")) :
        splits = splits.strip()
        if not splits:
            continue

        list_paragrath = []
        if combined_addr_pattern.search(splits):
            ratio = 1.0
            address_idxs.append(idx)
            pattern = "address"
        else :
            try :
                pos = kkma.pos(splits)
                pattern = "description"
                logging.debug(pos)

                po_count = len(pos)
                N_count = len([text for text, po in pos if po in allowed_tags])
                ratio = N_count/po_count
            except Exception as e:
                logging.error(f"Kkma 분석 에러 (내용: {splits}): {e}")
                ratio = 0
                pattern = "error"

        logging.info(f"pattern: {pattern}, content: {splits}")
        list_paragrath.append(ratio)
        list_paragrath.append(splits)
        list_caption_with_ratio.append(list_paragrath)

# 주소 있는지 없는지 먼저 훑기
# 주소 있는 캡션이면 그 때 해당 인덱스 제외 nlpy 돌리기
# 해시태그 이후 글 전부 삭제
    return list_caption_with_ratio, address_idxs # 문단 리스트

def check_place_in_caption(dict_paragraf: dict, list_caption: list): 
    # dict_paragraf >  {문단 : [[ratio, 문장]], address_idxs]}
    result = []

    # 문단별로 확인
    for idx, data in dict_paragraf.items():
        list_caption_with_ratio = data[0]
        address_idxs = data[1]
        address_count = len(address_idxs)
        logging.debug(f"주소 개수: {address_count}")

        if address_count :
            if address_count > 1: 
                chunk_size = len(list_caption_with_ratio) // address_count
                caption_list_with_chunk = list_chunk(list_caption_with_ratio, chunk_size) # 문단 내에서 주소 기준 문장별로 나누기(이중리스트)
            else : 
                caption_list_with_chunk = [list_caption_with_ratio]

            logging.debug(f"----주소 기준 문장 분할----\n{caption_list_with_chunk}")
            result.append(check_base_on_address(caption_list_with_chunk, address_idxs))
            # number를 추가하게 되면 주소까지 한번에 나옴. > 근데 가게명에 넘버 있는 경우가 있음
            # 숫자 포함 후 > 주소 엮어서 확인 과정 필요
    
    logging.info(f"주소 기준 분할 문장 : {len(caption_list_with_chunk)}개")
    return result

def check_base_on_address(caption_list_with_chunk: list, address_idxs: list) :
    result = []

    ratio_idx = []
    for idx, caption in enumerate(caption_list_with_chunk): 
        # 주소 위치를 제외하고 맥스값
        copy_caption = caption[:]
        copy_address_idx = address_idxs[:]
        
        if copy_caption:
            address_idx = copy_address_idx.pop(0)
            del copy_caption[address_idx]
        logging.debug(f"주소 인덱스 삭제: {copy_caption}")

        ratio_max = max(copy_caption)
        original_idx = caption.index(ratio_max)
        ratio_idx.append(original_idx)

    logging.debug(f"주소 제외 명사 비율 최대 인덱스: {ratio_idx}")
    if not ratio_idx: return result

    place_idxs = Counter(ratio_idx)
    place_idx = place_idxs.most_common(n=1)[0][0] # 최빈값이 장소 idx [(1 : 5)]
    
    copy_address_idx = address_idxs[:]
    address_idx = Counter(copy_address_idx)
    address_idx = address_idx.most_common(n=1)[0][0]
    '''address_idx = copy_address_idx.pop(0)'''
    logging.debug(f"장소 인덱스: {place_idx}, 주소 인덱스: {address_idx}")

    # [{'name': '상호명1', 'address': '주소1'}, ...]
    for caption in caption_list_with_chunk:
        if len(caption) > place_idx:
            result.append({'name' : caption[place_idx][1], 'address' :caption[address_idx][1]}) # 장소, 주소

    return result

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
        logging.error(f"GPT Error: {e}")
        return [], "에러"

def is_place_post(caption):
    try:
        # 추후 수정 필요함!!!!!!!
        # 핵심 장소 키워드
        place_keywords = ['공간', '곳', '장소', '작업실', '매장', '스토어', '전시']
        
        # 방문 정보 키워드 (축제/팝업도 포함시키기 위함)
        info_keywords = ['📍', '주소', '위치', '🗓️', '기간', '일시', '운영', '지도', '근처']
        
        # 행동/추천 키워드
        action_keywords = ['추천', '공유', '저장', '가보세', '방문', '데이트', '소개']

        # 장소 키워드 출현 빈도
        place_score = sum(1 for word in place_keywords if word in caption)
        
        # 방문 정보 유무 (좌표나 기간이 명시되었는가?)
        info_score = sum(1 for word in info_keywords if word in caption)
        
        # 행동 유도 유무
        action_score = sum(1 for word in action_keywords if word in caption)

        is_valid = place_score >= 1 or info_score >= 1 or action_score >= 1
        
        logging.info(f"valid:{is_valid}\nscore - place:{place_score}, info:{info_score}, action:{action_score}")
        return is_valid

    except Exception as e:
        logging.error(f"Error: {e}", flush=True)
        return False, "에러"

# 규칙 기반 수정해야함 -> 그전까지 일단 ai만 사용
def split_caption(caption):
    if not caption:
        logging.info("No caption")
        return

    cleaned_caption, list_caption = clean_text(caption)
    
    logging.debug(cleaned_caption)
 
    try:
        logging.info("[형태소 분석 중...]")
        dict_paragraf = {} # {문단 : [list_caption_with_ratio, address_idxs]}
        have_address = 0

        for idx, text in enumerate(list_caption): # 문단별로 파악
            list_caption_with_ratio, address_idxs = check_rulebase_place(text)
            logging.debug(f"---------list_caption_with_ratio-------- \n{list_caption_with_ratio}")
            logging.debug(f"---------address_idxs-------- \n{address_idxs}")
            if address_idxs: have_address = 1
            dict_paragraf[idx] = [list_caption_with_ratio, address_idxs]

        if have_address : 
            result = check_place_in_caption(dict_paragraf , list_caption)
        else:
            logging.info("장소 아님")
        
        logging.info(f"추출 결과 :{result}")
        return result

    except Exception as e:
        logging.error(f"분석 에러: {e}")
        return []
