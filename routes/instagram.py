import os
import json
from flask import Blueprint, request, jsonify
from services import instagram_parser
from services import check_place
# models 파일에서 정의한 클래스들 임포트
from models import db, Place, InstaUrl, PlaceArea, UrlPlace 

bp = Blueprint('instagram', __name__, url_prefix='/api/instagram')

@bp.route('/analyze', methods=['POST'])
def analyze_instagram():
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

        # 썸네일 경로 및 캡션 텍스트 가져오기
        thumbnail_path = gpt_result.get('saved_images', [None])[0]
        caption_text = gpt_result.get('caption', '') # instagram_parser에서 caption도 반환한다고 가정

        # -------------------------------------------------------
        # [Step 1] InstaUrl 테이블 저장
        # -------------------------------------------------------
        new_insta = InstaUrl(
            url=url,
            image=thumbnail_path,
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
            # [Step 2] PlaceArea (지역) 확인 및 생성
            # ---------------------------------------------------
            area_name = extract_area_name(p_info['address'])
            
            # 이미 존재하는 지역인지 확인
            area = PlaceArea.query.filter_by(name=area_name).first()
            
            if not area:
                # 없으면 새로 생성
                area = PlaceArea(
                    name=area_name,
                    latitude=p_info['latitude'],  # 해당 지역 첫 장소의 좌표를 지역 중심으로 사용 (임시)
                    longitude=p_info['longitude'],
                    radiusm=1000.0 # 기본 반경 1km 설정
                )
                db.session.add(area)
                db.session.flush() # area.id 생성
                print(f"   └─ [New Area] 지역 생성: {area.name}")
            
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
                    photo=thumbnail_path,
                    area_id=area.id, # [중요] 위에서 구한 area.id 연결
                    rating_avg=0.0,
                    rating_count=0,
                    saved_count=0
                )
                db.session.add(place)
                db.session.flush() # place.id 생성
                print(f"   └─ [New Place] 장소 저장: {place.name}")
            else:
                # 이미 존재하면 area_id가 비어있을 경우 업데이트
                if not place.area_id:
                    place.area_id = area.id

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