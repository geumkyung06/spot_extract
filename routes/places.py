import logging
from flask import Blueprint, request, jsonify, g
from models import db, Place, SavedPlace # models.py에서 임포트

user_places_bp = Blueprint("user_places", __name__, url_prefix='/places')

# [미들웨어 대용] 로그인 체크 (실제로는 app.before_request나 데코레이터로 처리 권장)..?
@user_places_bp.before_request
def _auth_guard():
    if not getattr(g, "user_id", None):
        g.user_id = 1  # [TEST] 임시 유저 ID

@user_places_bp.route("/", methods=["POST"])
def save_user_places():
    """
    장소 보관함 저장
    ---
    tags:
      - User Places
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            save_type:
              type: string
              example: "instagram"
            place_ids:
              type: array
              items:
                type: integer
              example:
                - 1
                - 2
    responses:
      200:
        description: 저장 성공
      400:
        description: 잘못된 요청
      500:
        description: 서버 에러
    """
    
    try:
        body = request.get_json() or {}
        user_id = g.user_id
        save_type = body.get("save_type", "instagram")

        target_ids = set()
        
        if "places" in body and isinstance(body["places"], list):
            for item in body["places"]:
                if isinstance(item, dict) and item.get("place_id"):
                    target_ids.add(int(item["place_id"]))
        
        elif "place_ids" in body and isinstance(body["place_ids"], list):
            for pid in body["place_ids"]:
                target_ids.add(int(pid))

        if not target_ids:
            return jsonify({"error": "No place_ids provided"}), 400

        saved_count = 0
        
        # 2. DB 저장 (Upsert: 없으면 넣고, 있으면 스킵)..?
        for pid in target_ids:
            # 장소가 실제로 존재하는지 확인
            place_exists = Place.query.get(pid)
            if not place_exists:
                continue

            # 이미 저장했는지 확인
            existing = SavedPlace.query.filter_by(
                user_id=user_id,
                place_id=pid
            ).first()

            if not existing:
                new_save = SavedPlace(
                    user_id=user_id,
                    place_id=pid,
                    save_type=save_type,
                    rating=0
                )
                db.session.add(new_save)
                
                # 장소 테이블의 총 저장 수(saved_count) 증가
                place_exists.saved_count = (place_exists.saved_count or 0) + 1
                
                saved_count += 1

        db.session.commit()

        return jsonify({
            "status": "success",
            "saved_count": saved_count,
            "message": f"{saved_count}개의 장소가 저장되었습니다."
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@user_places_bp.route("/", methods=["GET"])
def list_my_places():
    """
    보관함 장소 목록 조회
    ---
    tags:
      - User Places
    parameters:
      - name: page
        in: query
        type: integer
        default: 1
      - name: size
        in: query
        type: integer
        default: 20
    responses:
      200:
        description: 조회 성공
    """

    try:
        user_id = g.user_id
        page = int(request.args.get("page", 1))
        size = int(request.args.get("size", 20))

        # Pagination: SavedPlace 조회(최신순)
        pagination = SavedPlace.query.filter_by(user_id=user_id)\
            .order_by(SavedPlace.created_at.desc())\
            .paginate(page=page, per_page=size, error_out=False)

        results = []
        for item in pagination.items:
            # item.place를 통해 Place 테이블 정보 접근 (Relationship 활용)
            place_info = item.place 
            if not place_info:
                continue
                
            results.append({
                "saved_id": item.id,       # saved_place 테이블의 ID
                "save_type": item.save_type,
                "saved_at": item.created_at,
                "place": {                 # 실제 장소 상세 정보
                    "place_id": place_info.id,
                    "name": place_info.name,
                    "address": place_info.address,
                    "category": place_info.list,
                    "latitude": place_info.latitude,
                    "longitude": place_info.longitude,
                    "thumbnail": place_info.photo,
                    "rating_avg": place_info.rating_avg
                }
            })

        return jsonify({
            "page": page,
            "total_pages": pagination.pages,
            "total_items": pagination.total,
            "places": results
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500