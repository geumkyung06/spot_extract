from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt_identity
from models import db, Place, SavedPlace  

# 보관함에서 장소 삭제
def delete_my_place(place_id):
    try:
        user_id = get_jwt_identity()

        # 1. 보관함에서 해당 장소 삭제
        saved_place = SavedPlace.query.filter_by(
            user_id=user_id,
            place_id=place_id
        ).first()

        if not saved_place:
            return jsonify({"error": "Saved place not found"}), 404

        db.session.delete(saved_place)

        # 2. 장소 테이블의 총 저장 수(saved_count) 감소
        place = Place.query.get(place_id)
        if place and place.saved_count and place.saved_count > 0:
            place.saved_count -= 1

        db.session.commit()

        return jsonify({
            "status": "success",
            "message": "장소가 보관함에서 삭제되었습니다."
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500