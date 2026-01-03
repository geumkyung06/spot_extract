import os
import json
import re
from openai import OpenAI
from dotenv import load_dotenv
import services.instagram_image_extracter as img_svc

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ───────────────────────────────────────────────────────────────
# 1. 캡션용 GPT 호출 (Text Only)
# ───────────────────────────────────────────────────────────────
def call_gpt_text(text):
    prompt = f"""
    아래 인스타그램 캡션에서 '장소명(상호명)'과 '주소'를 추출해줘.
    결과는 반드시 JSON 리스트 포맷이어야 해: [[ "장소명", "주소" ]]
    
    규칙:
    1. 주소가 없으면 "no_address"라고 적어.
    2. 장소명이 없거나 불확실하면 "no_name"이라고 적어.
    3. 코드블록(```json) 없이 순수 텍스트로 줘.

    캡션 내용:
    {text}
    """
    
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[
                {"role": "system", "content": "너는 맛집 데이터 추출 전문가야. JSON 형식으로만 대답해."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"GPT Text 에러: {e}")
        return None

# ───────────────────────────────────────────────────────────────
# 2. 이미지용 GPT-4o Vision 호출
# ───────────────────────────────────────────────────────────────
def call_gpt_vision(image_paths):
    if not image_paths:
        return None
    
    # 앞부분 3장만 분석
    target_images = image_paths[:3]
    
    messages = [
        {
            "role": "system", 
            "content": """
            너는 시각 정보 처리 전문가야. 다음 단계를 거쳐서 답해.

            1. [관찰]: 사진에 보이는 **모든 글자(한글, 영어, 숫자)**를 읽어. 간판, 메뉴판, 컵홀더, 영수증, 포스터 등을 자세히 봐.
            2. [추론]: 읽어낸 글자들 중에서 '가게 이름'이 있는지 판단해.
            3. [결과]: 최종 결과를 JSON 리스트 [[ "가게명", "주소" ]] 형태로 출력해.
            
            만약 가게 이름을 도저히 찾을 수 없다면 "no_name", "no_address"를 써.
            """
        }
    ]
    
    user_content = [
        {"type": "text", "text": "이 사진들을 보고 상호명과 주소를 찾아줘. 메뉴판이나 간판을 자세히 봐."}
    ]
    
    valid_count = 0
    for path in target_images:
        base64_img = img_svc.encode_image_to_base64(path)
        if base64_img:
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64_img}"
                }
            })
            valid_count += 1
            
    if valid_count == 0:
        return None

    messages.append({"role": "user", "content": user_content})

    try:
        print("GPT-4o(Vision)가 이미지를 정밀 분석 중...")
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=500,
            temperature=0.2
        )
        result = resp.choices[0].message.content
        
        # [디버깅] GPT가 실제로 뱉은 말 확인
        print(f"👀 Vision 응답: {result}")
        return result
    except Exception as e:
        print(f"GPT Vision 에러: {e}")
        return None

# ───────────────────────────────────────────────────────────────
# [수정됨] 결과 검증 함수 (이름만 있으면 성공!)
# ───────────────────────────────────────────────────────────────
def is_valid_place(gpt_result):
    if not gpt_result: 
        return False
    
    # 1. 마크다운 및 공백 제거
    cleaned = gpt_result.replace("```json", "").replace("```", "").strip()
    
    # 2. JSON 파싱 시도
    try:
        data = json.loads(cleaned)
        
        # 리스트가 아니거나 비어있으면 실패
        if not isinstance(data, list) or not data:
            return False
            
        # 3. 내용물 검사 (조건 완화: 이름만 있으면 OK)
        has_real_place = False
        
        for item in data:
            # item 예시: ["스타벅스", "no_address"] -> 성공
            # item 예시: ["no_name", "서울시..."] -> 실패
            
            if len(item) < 1: continue # 데이터 형식이 이상하면 건너뜀
            
            name = str(item[0]).strip()
            
            # 이름이 "no_name"이면 무효 (주소가 있어도 이름 모르면 실패로 간주)
            if not name or "no_name" in name.lower():
                continue
                
            # 여기까지 왔다면 이름은 유효함 (주소는 no_address여도 상관없음)
            has_real_place = True
            break
        
        return has_real_place

    except json.JSONDecodeError:
        # 파싱 실패 시 텍스트 검사 (비상용)
        if "no_name" in cleaned.lower():
            return False
        return False 

# ───────────────────────────────────────────────────────────────
# [메인] 전체 프로세스 함수
# ───────────────────────────────────────────────────────────────
def process_instagram_post(url: str):
    print(f"🚀 분석 시작: {url}")
    
    # 1. 데이터 추출
    raw_data = img_svc.extract_post_data(url)
    caption = raw_data['caption']
    image_urls = raw_data['images']
    
    # 2. 이미지 다운로드
    saved_paths = img_svc.download_images_to_temp(image_urls)
    print(f"임시 이미지 {len(saved_paths)}장 다운로드 완료")

    # 3. [1차] 캡션 분석
    print("1차: 캡션 분석 시도...")
    place_info = call_gpt_text(caption)
    
    final_source = "caption"
    
    # 검증: 이름만 있으면 통과
    if not is_valid_place(place_info):
        print("캡션 분석 실패 (이름 없음) -> 2차: GPT-4o 이미지 분석 시작")
        
        place_info = call_gpt_vision(saved_paths)
        final_source = "gpt4o_vision"
        
        if not is_valid_place(place_info):
            print("이미지 분석으로도 가게 이름을 찾지 못했습니다.")

    # 4. 결과 리턴
    clean_data = None
    if place_info:
        clean_data = place_info.replace("```json", "").replace("```", "").strip()

    if is_valid_place(place_info):
        print(f"장소 찾기 성공! (출처: {final_source})")
        return {
            "status": "success",
            "source": final_source,
            "data": clean_data,
            "saved_images": saved_paths
        }
    else:
        return {
            "status": "fail",
            "msg": "장소 추출 실패 (캡션/이미지 모두 실패)",
            "data": clean_data,
            "saved_images": saved_paths
        }