import os
import json
from flask import Blueprint, request, jsonify
from services import instagram_parser
from services import check_place
# models 파일에서 정의한 클래스들 임포트
from models import db, Place, InstaUrl, UrlPlace 
import uuid
import shutil

bp = Blueprint('instagram', __name__, url_prefix='/api/instagram')

# [설정] 영구 저장 경로
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static', 'uploads')
# 폴더가 없으면 생성
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@bp.route('/analyze', methods=['POST'])
def analyze_instagram():
    """
    인스타그램 URL 분석 및 장소 추출
    ---
    tags:
      - Instagram
    summary: 인스타그램 게시글 URL을 분석 후 장소 정보 추출 및 저장
    description: >
      입력받은 인스타그램 URL 크롤링을 통하여 캡션 혹은 이미지에서 정보 추출
      GPT를 통해 장소 정보 파싱. 네이버/구글 맵 API로 검증 후 DB(insta_url, place, url_place)에 저장
    consumes:
      - application/json
    produces:
      - application/json
    parameters:
      - name: body
        in: body
        required: true
        description: 분석할 인스타그램 URL JSON
        schema:
          type: object
          required:
            - url
          properties:
            url:
              type: string
              example: "https://www.instagram.com/p/DSL6Oaak82_/ "
              description: "분석할 인스타그램 게시글 링크"
    responses:
      200:
        description: 분석 및 저장 성공
        schema:
          type: object
          properties:
            status:
              type: string
              example: "success"
            results:
              type: array
              items:
                type: object
                properties:
                  name:
                    type: string
                    description: 장소 이름
                    example: "스타벅스 강남점"
                  category:
                    type: string
                    description: 장소 카테고리
                    example: "cafe"
      400:
        description: URL 파라미터 누락
      500:
        description: 서버 내부 오류 (크롤링 실패, GPT 파싱 실패 등)
    """
    try:
        data = request.get_json()
        url = data.get('url')
        if not url:
            return jsonify({'status': 'error', 'message': 'URL is required'}), 400

        print(f"[Start] 분석 시작: {url}")

        # 1. 크롤링 및 GPT 분석
        gpt_result = instagram_parser.process_instagram_post(url)
        if gpt_result['status'] != 'success':
            return jsonify({'status': 'fail', 'message': gpt_result.get('msg')}), 500

        # GPT 데이터 파싱
        try:
            raw_data = gpt_result['data'].replace("```json", "").replace("```", "").strip()
            gpt_candidates = json.loads(raw_data)
        except:
            return jsonify({'status': 'error', 'message': 'GPT parsing failed'}), 500

        saved_imgs = gpt_result.get('saved_images', [])
        
        if saved_imgs and len(saved_imgs) > 0:
            thumbnail_path = saved_imgs[0]
        else:
            thumbnail_path = None # 이미지가 없으면 None으로 설정
            print("[Warning] 분석 결과에서 이미지를 찾을 수 없습니다.")

        caption_text = gpt_result.get('caption', '') 


# 이미지 저장 X 안해도 됨
        # 파일 이동 로직을 위한 변수 설정
        temp_path = thumbnail_path  # 위에서 구한 값을 그대로 사용
        final_path = None
        
        if temp_path and os.path.exists(temp_path):
            # 1. 고유한 파일명 생성 (중복 방지)
            ext = temp_path.split('.')[-1] 
            new_filename = f"{uuid.uuid4()}.{ext}"
            
            # 2. 이동할 전체 경로
            destination = os.path.join(UPLOAD_FOLDER, new_filename)
            
            # 3. 파일 이동 (temp -> static/uploads)
            shutil.move(temp_path, destination)
            
            # 4. DB에 저장할 경로
            final_path = f"/static/uploads/{new_filename}"
            
            print(f"[Image] 영구 저장 완료: {final_path}")
        
        # -------------------------------------------------------
        # [Step 1] InstaUrl 테이블 저장
        # -------------------------------------------------------
        new_insta = InstaUrl(
            url=url,
            image=final_path,
            texts=caption_text # 캡션 전체 저장
        )
        db.session.add(new_insta)
        db.session.flush() # flush를 해야 new_insta.id가 생성됨
        print(f"[DB] InstaUrl 저장 완료 (ID: {new_insta.id})")

        # 2. 장소 검증 (네이버/구글)
        verified_places = check_place.check_place_on_naver(gpt_candidates)
        
        saved_results = []

        for p_info in verified_places:
            if not p_info.get('name'): continue

            # ---------------------------------------------------
            # [Step 3] Place (장소) 저장/조회
            # ---------------------------------------------------
            place = Place.query.filter(
                Place.name == p_info['name'],
                Place.address == p_info['address']
            ).first()

            if not place:
                place = Place(
                    name=p_info['name'],
                    address=p_info['address'],
                    list=p_info['category'],
                    latitude=p_info['latitude'],
                    longitude=p_info['longitude'],
                    photo=final_path,
                    rating_avg=0.0,
                    rating_count=0,
                    saved_count=0
                )
                db.session.add(place)
                db.session.flush() # place.id 생성
                print(f"   └─ [New Place] 장소 저장: {place.name}")

            # ---------------------------------------------------
            # [Step 4] UrlPlace (매핑) 저장
            # ---------------------------------------------------
            # 중복 매핑 방지
            mapping = UrlPlace.query.filter_by(
                instaurl_id=new_insta.id,
                placeid_id=place.id
            ).first()

            if not mapping:
                new_mapping = UrlPlace(
                    instaurl_id=new_insta.id, # 인스타 ID
                    placeid_id=place.id       # 장소 ID
                )
                db.session.add(new_mapping)
                print(f"   └─ [Mapping] 연결 완료: Insta({new_insta.id}) - Place({place.id})")

            saved_results.append({"name": place.name, "category": place.list})

        # 최종 커밋
        db.session.commit()

        return jsonify({
            'status': 'success',
            'results': saved_results
        }), 200

    except Exception as e:
        db.session.rollback()
        print(f"Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500