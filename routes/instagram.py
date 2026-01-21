import os
import json
import asyncio
import aiohttp
import time
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
import re

from services.instagram_text_parser import get_caption_no_login, split_caption, extract_places_with_gpt, is_place_post
from services.instagram_image_extracter import safe_ocr, extract_images, process_download
from services.check_place import process_places
from services.browser import browser_service

# models 파일에서 정의한 클래스들 임포트
from models import db, Place, InstaUrl, UrlPlace

import uuid
import shutil

bp = Blueprint('instagram', __name__)

SAVE_FOLDER = "downloaded_images"

# 게시물 분석 후 장소 정보와 이미지를 DB에 저장 및 유저 화면에 반환
@bp.route('/analyze', methods=['POST'])
@jwt_required()
async def analyze_instagram():
    """
    인스타그램 게시물 URL 분석 및 장소 추출
    ---
    tags:
      - Instagram
    security:
      - Bearer: []
    description: >
      인스타그램 게시물 URL을 받아 캡션 또는 이미지를 분석하여 장소 정보 추출
      DB에 캐싱된 정보가 있으면 즉시 반환, 없으면 외부 API(Naver/Google)를 통해 정보를 수집 후 저장
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - url
          properties:
            url:
              type: string
              description: 분석할 인스타그램 게시물 URL
              example: "https://www.instagram.com/p/CXYZ123abc/"
    responses:
      200:
        description: 분석 및 장소 추출 성공
        schema:
          type: object
          properties:
            status:
              type: string
              example: "success"
            results:
              type: array
              description: 추출된 장소 목록
              items:
                type: object
                properties:
                  id:
                    type: integer
                    description: 장소 ID (DB PK)
                    example: 123
                  name:
                    type: string
                    description: 장소명
                    example: "스타벅스 강남점"
                  address:
                    type: string
                    description: 도로명 주소
                    example: "서울 강남구 강남대로 123"
                  list:
                    type: string
                    description: 카테고리 (콤마로 구분된 문자열)
                    example: "카페,디저트"
                  latitude:
                    type: number
                    format: float
                    description: 위도
                    example: 37.498095
                  longitude:
                    type: number
                    format: float
                    description: 경도
                    example: 127.027610
                  rating_avg:
                    type: number
                    format: float
                    description: 구글 평점 (0.0 ~ 5.0)
                    example: 4.5
                  rating_count:
                    type: integer
                    description: 리뷰 수
                    example: 120
                  photo:
                    type: string
                    description: 대표 이미지 URL 또는 경로
                    example: "https://example.com/image.jpg"
      400:
        description: 잘못된 요청 (URL 누락, 유효하지 않은 URL, 장소 게시물 아님)
        schema:
          type: object
          properties:
            status:
              type: string
              example: "error"
            message:
              type: string
              example: "It is not a place post"
      500:
        description: 서버 내부 오류
        schema:
          type: object
          properties:
            status:
              type: string
              example: "error"
            message:
              type: string
    """
    try:
        data = request.get_json()
        url = data.get('url') # 프론트한테서 받아옴
        if not url:
            return jsonify({'status': 'error', 'message': 'URL is required'}), 400

        print(f"[Start] 분석 시작: {url}")

        post_places = [] # 프론트에 보낼 장소들 (name, address, category(list), rating_avg, rating_count)
        new_places = [] # db에 새로 저장할 장소들

        # 0. 장소 설명 게시물인지 확인
        is_place = is_place_post(url)
        
        if not is_place:
            return jsonify({'status': 'error', 'message': "It is not a place post"}), 400
        
        # 1. DB에 이미 URL이 있는지 확인
        print("DB에 존재하는 게시물인지 확인 중...")
        pattern = r'/(?:p|reel)/([^/?]+)'
    
        match = re.search(pattern, url)
        if match:
            shortcut = match.group(1)
        else: 
            return jsonify({'status': 'error', 'message': 'Invalid URL'}), 400
        
        db_places = check_db_have_url(shortcut)
        # 2. 장소 확인 후 프론트에게 보낼 장소 정보 준비
        # Q. 가능하면 네이버 검색 돌리기 전에 저장되어있는지 파악하는게 좋을 듯
        if db_places:
            print("DB 캐시 존재")
            post_places = db_places
        else:
            print("DB에 없음. 캡션 분석 시도")
            # 캡션 파싱 로직
            candidates, caption = await check_caption_place(url)
            
            #url_place에 저장
            try:
                save_caption = caption[:252] + "..." if caption and len(caption) > 255 else caption
                new_entry = InstaUrl(url=shortcut, texts=save_caption)
                db.session.add(new_entry)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"InstaUrl 저장 실패: {e}")

            if not candidates:
                print("캡션에서 장소 못 찾음. OCR 시도...")
                candidates = await check_ocr_place(url)

            to_search_naver = []
            if candidates:
                # db에서 있는지 확인
                # candidates = {'place': '아우스페이스', 'address': '경기도 파주시 탄현면 새오리로 145-21'}
                existing_place, to_search_naver = check_db_have_place(candidates) 
                if existing_place:
                        print(f"DB에 이미 있는 장소: {existing_place['name']}")
                        post_places.append(existing_place)
            else:
                return jsonify({'status':'failed', 'message': "can not found places"}), 404
            
            if to_search_naver:
                search_results = process_places(to_search_naver)
                
                new_places.extend(search_results)
                post_places.extend(search_results)

            if new_places:
                save_places_to_db(new_places)
    
        # 프론트에 보낼 장소 정보
        return jsonify({'status':'success', 'results':post_places}), 200

    except Exception as e:
        db.session.rollback()
        print(f"Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

def check_db_have_url(url=""):
    post_places = []
    # DB에 이미 URL이 있는지 확인
    #existing_insta = UrlPlace.query.filter_by(url=url).all() # 해당 url 속 여러 장소 placeid_id 전부 가져오기

    # 해당 url 속 여러 장소 placeid_id 전부 가져오기 +  placeid_id / id 비교
    existing_insta = Place.query.join(UrlPlace, Place.id == UrlPlace.placeid_id).filter(UrlPlace.url == url).all()
    if existing_insta:
        # 이미 존재하면 해당 장소 불러오기

        for place in existing_insta :
            #existing_place = Place.query.filter_by(id=place.placeid_id).first() # placeid_id / id 비교
            place_data = {
            "id": place.id,          
            "name": place.name,
            "address": place.address,
            "list": place.list, 
            "rating_avg": place.rating_avg,
            "rating_count": place.rating_count,
            "photo": place.photo    
            }   
            post_places.append(place_data)
            
        return post_places
    
        
def check_db_have_place(candidates=[]): # 장소명, 주소 한번에 보내기
    post_places = [] 
    no_places = []   

    if not candidates:
        return [], []

    for cand in candidates:
        input_name = cand.get('name')
        input_addr = cand.get('address', '').strip()

        # 이름 검색
        found_places = Place.query.filter_by(name=input_name).all()
        
        target_place = None

        if found_places:
            if not input_addr:
                # 주소 없으면 동명의 장소가 1개일 때만 확신하고 가져옴
                if len(found_places) == 1:
                    target_place = found_places[0]
            else:
                # 주소 비교
                for db_place in found_places:
                    if is_address_match(input_addr, db_place.address):
                        target_place = db_place
                        break

        if target_place:
            print(f"DB 캐시 Hit: {target_place.name} (ID: {target_place.id})")
            place_data = {
                "id": target_place.id,          
                "name": target_place.name,
                "address": target_place.address,
                "list": target_place.list, 
                "rating_avg": target_place.rating_avg,
                "rating_count": target_place.rating_count,
                "photo": target_place.photo    
                }   
            post_places.extend(place_data)
        else:
            print(f"검색 필요: {input_name}")

            no_places.append(input_name) 
        
    return post_places, no_places


def is_address_match(input_addr, db_addr):
    """
    인스타 주소가 DB 주소에 포함되는지 확인
    ex) input: "연남동", DB: "서울 마포구 연남동 123" -> True
    """
    if not db_addr: return False

    # 공백 제거 후 단순 포함 관계 확인
    # ex) "마포구연남동" in "서울마포구연남동123"
    clean_input = input_addr.replace(" ", "")
    clean_db = db_addr.replace(" ", "")
    
    if clean_input in clean_db:
        return True

    input_tokens = set(input_addr.split())
    
    # DB 주소 안에 인스타 주소의 단어들이 얼마나 들어있는지 확인
    match_count = 0
    for token in input_tokens:
        if token in db_addr: # "연남동"이 DB 주소 문자열 안에 있는가?
            match_count += 1
    
    # 인스타 주소 단어의 70% 이상이 DB 주소에 포함되면 같은 곳으로 간주
    if len(input_tokens) > 0 and (match_count / len(input_tokens) >= 0.7):
        return True
        
    return False
    
async def check_caption_place( url=""):
    '''
    캡션에서 장소 추출
    '''
    try:
        if not url == 'No URL':
            print("URL이 비어있습니다.")
            return [], ""
        
        caption = asyncio.run(get_caption_no_login(url))
        
        if not caption:
            print("캡션을 찾을 수 없습니다.")
            return [], ""
        
        # 규칙 기반 좀 더 타이트하게 해야함
        '''places, caption = split_caption(caption)

        if not places:
            places = extract_places_with_gpt(caption)'''
        places = extract_places_with_gpt(caption)

        return places, caption

    except Exception as e:
        print(f"서버 에러: {e}")        
        return [], ""

async def check_ocr_place(url=""):
    if not url:
        return []

    try:
        start_total = time.time()
        print(f"분석 요청: {url}")

        # 이미지 URL 추출
        if not browser_service.browser:
            # 혹시라도 꺼져있으면 비상 실행
            await browser_service.start()
            
        image_urls = await extract_images(browser_service, url)
        
        if not image_urls:
            return []

        print(f"{len(image_urls)}개 이미지 추출")

        if not os.path.exists(SAVE_FOLDER):
            os.makedirs(SAVE_FOLDER)

        # 다운로드 및 전처리
        saved_files = []
        connector = aiohttp.TCPConnector(limit=10)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [process_download(session, url, i) for i, url in enumerate(image_urls)]
            results = await asyncio.gather(*tasks)
            saved_files = [r for r in results if r is not None]

        if not saved_files:
            return []

        # OCR 수행
        ocr_tasks = [safe_ocr(filepath) for filepath in saved_files]
        ocr_results_list = await asyncio.gather(*ocr_tasks)

        # 결과 필터링 및 병합
        final_places = []
        for res in ocr_results_list:
            if isinstance(res, list):
                final_places.extend(res)
            elif isinstance(res, dict) and "error" not in res:
                # 에러가 아닌 단일 객체인 경우 (구조에 따라 다름)
                final_places.append(res)

        # OCR은 돌렸는데 텍스트가 하나도 안 나온 경우
        if not final_places:
             return []

        end_total = time.time()
        total_time = end_total - start_total
        print(f"OCR 처리 시간: {total_time}s")
        return final_places

    except Exception as e:
        print(f"서버 에러: {e}")
        return []

def save_places_to_db(new_places = []): 
    try :
        for p_info in new_places:
            place = Place.query.filter(
                Place.name == p_info['name'],
                Place.address == p_info['address']
            ).first()

            if not place:
                place = Place(
                    name=p_info.get('name'),
                    address=p_info.get('address'),
                    list=p_info.get('list'),
                    latitude=p_info.get('latitude'),
                    longitude=p_info.get('longitude'),
                    rating=p_info.get('rating'),
                    rating_count=p_info.get('rating_count'),
                    photo=p_info.get('photo', '')
                )
                db.session.add(place)
                db.session.flush()  # flush를 해야 place.id가 생성됨

                print(f"[DB] Place saved (ID: {place.id})")
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"DB 저장 실패: {e}")