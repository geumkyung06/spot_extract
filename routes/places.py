import logging
from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db, Place, SavedPlace, SavedSeq
user_places_bp = Blueprint("saved_places", __name__, url_prefix='/places')


@user_places_bp.route("/", methods=["POST"])
@jwt_required()
def save_user_places():
    """
    유저별 장소 저장
    ---
    tags:
      - Saved Places
    consumes:
      - application/json
    parameters:
      - in: body
        name: body
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
              example: [1, 2]
            places:
              type: array
              items:
                type: object
                properties:
                  place_id:
                    type: integer
    responses:
      200:
        description: 저장 성공
      400:
        description: 잘못된 요청
      500:
        description: 서버 에러
    """

    try:
        user_id = get_jwt_identity() 

        body = request.get_json() or {}
        save_type = body.get("save_type", "spot") # 기본값 설정 - 인스타에선 "instagram"

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
        
        # DB 저장
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

        if saved_count > 0:
            seq_row = SavedSeq.query.first() 
            if seq_row:
                seq_row.next_val = (seq_row.next_val or 0) + saved_count

        db.session.commit()

        return jsonify({
            "status": "success",
            "saved_count": saved_count,
            "message": f"saved {saved_count} place"
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


'''@user_places_bp.route("/", methods=["GET"])
@jwt_required()
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
        user_id = get_jwt_identity()
        
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
                    "list": place_info.list, # list로 변경해야함
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
        return jsonify({"error": str(e)}), 500'''