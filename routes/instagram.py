import os
import json
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from services import instagram_parser
from services import check_place
from services import delete_place

# models 파일에서 정의한 클래스들 임포트
from models import db, Place, InstaUrl, UrlPlace , SavedPlace

import uuid
import shutil

bp = Blueprint('instagram', __name__)

# [설정] 영구 저장 경로
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static', 'uploads')
# 폴더가 없으면 생성
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 게시물 분석 후 장소 정보와 이미지를 DB에 저장 및 유저 화면에 반환
@bp.route('/analyze', methods=['POST'])
@jwt_required()
def analyze_instagram():
    """
    인스타그램 URL 분석
    ---
    tags:
      - Instagram
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            url:
              type: string
              example: "https://www.instagram.com/p/..."
    responses:
      200:
        description: 분석 성공
      500:
        description: 서버 에러
    """
    try:
        data = request.get_json()
        url = data.get('url') # 프론트한테서 받아옴
        if not url:
            return jsonify({'status': 'error', 'message': 'URL is required'}), 400

        print(f"[Start] 분석 시작: {url}")

        post_places = [] # 프론트에 보낼 장소들 (name, address, category(list))
        new_places = [] # db에 새로 저장할 장소들

        # 1. DB에 이미 URL이 있는지 확인
        print("check db have place...")
        db_places = check_db_have_place(url)

        # 2. 장소 확인 후 프론트에게 보낼 장소 정보 준비
        if db_places:
            print("DB 캐시 존재")
            post_places = db_places
        else:
            print("DB에 없음. 캡션 분석 시도")
            # 캡션 파싱 로직
            caption_result = check_caption_place(url)
            
            if caption_result:
                print("캡션에서 장소 발견")
                post_places = caption_result
                new_places = caption_result # 새로 찾은 거니까 저장 목록에도 추가
            else:
                print("캡션 실패. OCR 분석 시도")
                # OCR 로직 

                # 캡션 우선 확인
                # 캡션 파싱 함수 필요
                # 캡션은 gpt 사용하지 말고 가게명, 주소 하드코딩으로 찾기 > 거의 형태가 같으니까 일단 이렇게 

                # 캡션 확인 안될 시 이미지 확인
                # 이미지 dom으로 저장 후 바로 gpt4o로 ocr > 이미지 저장은 대략 20초 > 3초 내로 줄어듬 > 아마 23초 정도,,,, 일단 함수 먼저 완성
                ocr_result = check_ocr_place(url)
                
                if ocr_result:
                    print("OCR에서 장소 발견")
                    post_places = ocr_result
                    new_places = ocr_result


        # 3. 새로운 장소 저장 - new_places 사용
        save_places_to_db(new_places)
    

        # 4. 프론트에 보낼 장소 정보 - post_places 사용
        return jsonify({'status':'success', 'results':post_places}), 200

    except Exception as e:
        db.session.rollback()
        print(f"Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# 저장할 장소 받아오기
@bp.route('/', methods=['GET'])
@jwt_required()
def get_places(saved_place):
    try:
        user_id = get_jwt_identity()



        return jsonify({'status':'success', 'results':saved_place}), 200
    except Exception as e:
        return jsonify({'status':'error', 'message':str(e)}), 500


def check_db_have_place(post_places = [], url=""):
    '''
        1. 인스타그램 저장 로직 변경
        db에 이미 url 존재하는지 
        없다 > 캡션 확인 > 장소 추출(이거 gpt 쓰지 말고 그냥 찾기로 해보기) > 장소가 db에 있는지 확인
                                                            > 없다 > 네이버 검색으로 주소 찾기
                                                            > 있다 > 그냥 쓰기
                    > 추출이 안된다 > 이미지 다운받고 ocr > 장소가 db에 있는지 확인
                                                > 없다 > 네이버 검색으로 주소 찾기
                                                > 있다 > 그냥 쓰기
        있다 > 해당 id 내용 불러오기
    '''
    # 1. DB에 이미 URL이 있는지 확인
    existing_insta = UrlPlace.query.filter_by(url=url).all() # 해당 url 속 여러 장소 placeid_id 전부 가져오기

    if existing_insta:
        # 이미 존재하면 해당 장소 불러오기

        for place in existing_insta :
            existing_place = Place.query.filter_by(id=place.placeid_id).first() # placeid_id / id 비교
            post_places["id"].append(existing_place.id)
            post_places["name"].append(existing_place.name)
            post_places["address"].append(existing_place.address)
            post_places["category"].append(existing_place.category)
            
        return post_places
    
def check_caption_place(post_places = [], url=""):
    '''
    캡션에서 장소 추출
    '''
    # 캡션 파싱 함수 필요
    # 캡션은 gpt 사용하지 말고 가게명, 주소 하드코딩으로 찾기 > 거의 형태가 같으니까 일단 이렇게 

    return post_places

def check_ocr_place(post_places = [], url=""):
    '''
    이미지 OCR에서 장소 추출
    '''
    # 이미지 dom으로 저장 후 바로 gpt4o로 ocr > 이미지 저장은 대략 20초 > 3초 내로 줄어듬 > 아마 23초 정도,,,, 일단 함수 먼저 완성

    return post_places

def save_places_to_db(new_places = []):
    if new_places:
            for p_info in new_places:
                place = Place.query.filter(
                    Place.name == p_info['name'],
                    Place.address == p_info['address'],
                    Place.category == p_info['category'],
                    Place.latitude == p_info['latitude'],
                    Place.longitude == p_info['longitude'],
                    Place.rating == p_info['rating'],
                    Place.rating_count == p_info['rating_count']   
                ).first()

                if not place:
                    place = Place(
                        name=p_info['name'],
                        address=p_info['address'],
                        category=p_info['category'],
                        latitude=p_info['latitude'],
                        longitude=p_info['longitude'],
                        rating=p_info['rating'],
                        rating_count=p_info['rating_count']
                    )
                    db.session.add(place)
                    db.session.flush()  # flush를 해야 place.id가 생성됨
                    print(f"[DB] Place 저장 완료 (ID: {place.id})")