from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db, Place, SavedPlace, SavedSeq
from services.push_notification import notify_place_bookmarked, notify_same_place_saved

user_places_bp = Blueprint("saved_places", __name__)

from services.my_logger import get_my_logger
logger = get_my_logger(__name__)


@user_places_bp.route("/places", methods=["POST"], strict_slashes=False)
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
              example: "spot"
            place_ids:
              type: array
              items:
                type: integer
              example: [1, 2]
            source_type:
              type: string
              example: "friend_profile"
            source_user_id:
              type: integer
              example: 42
            source_comment_id:
              type: integer
              example: null
    responses:
      200:
        description: 저장 성공
      400:
        description: 잘못된 요청
      500:
        description: 서버 에러
    """
    try:
        user_id = int(get_jwt_identity())
        if not user_id:
            return jsonify({'status': 'error', 'message': 'Authentication required'}), 401

        body = request.get_json() or {}
        save_type = body.get("save_type", "spot")

        input_ids = body.get("place_ids", [])
        logger.debug(f"추출된 input_ids: {input_ids}")

        target_ids = set()
        if isinstance(input_ids, list):
            for pid in input_ids:
                try:
                    if pid is not None and str(pid).strip() != "":
                        target_ids.add(int(pid))
                except (ValueError, TypeError):
                    logger.warning(f"숫자 변환 실패: {pid}")
                    continue

        logger.debug(f"최종 target_ids: {target_ids}")
        if not target_ids:
            return jsonify({"error": "No place_ids provided"}), 400

        saved_ids = _do_save_places(user_id, target_ids, save_type)

        if saved_ids:
            _bump_saved_seq(len(saved_ids))

            source_type = body.get("source_type")
            source_user_id = body.get("source_user_id")
            source_comment_id = body.get("source_comment_id")

            if source_type in ("friend_profile", "comment") and source_user_id and source_user_id != user_id:
                notify_place_bookmarked(source_user_id, user_id, saved_ids, source_comment_id)  # #5

            notify_same_place_saved(user_id, saved_ids, exclude_user_id=source_user_id)  # #6

        db.session.commit()

        return jsonify({
            "status": "success",
            "saved_count": len(saved_ids),
            "message": f"saved {len(saved_ids)} place"
        }), 200

    except Exception as e:
        db.session.rollback()
        logger.exception("save_user_places failed")
        return jsonify({"error": str(e)}), 500


def _do_save_places(user_id, target_ids, save_type):
    """실제 저장 + saved_count 증가. DB 존재 여부/중복 체크 포함.
    커밋은 호출부에서 한 번만 수행."""
    saved_ids = []
    for pid in target_ids:
        place_exists = Place.query.filter_by(id=pid).first()
        if not place_exists:
            logger.warning(f"Place {pid} not found in DB - SKIPPING")
            continue

        existing = SavedPlace.query.filter_by(user_id=user_id, place_id=pid).first()
        if existing:
            continue

        db.session.add(SavedPlace(user_id=user_id, place_id=pid, save_type=save_type, rating=0))
        place_exists.saved_count = (place_exists.saved_count or 0) + 1
        saved_ids.append(pid)

    return saved_ids


def _bump_saved_seq(count):
    if count <= 0:
        return
    seq_row = SavedSeq.query.first()
    if seq_row:
        seq_row.next_val = (seq_row.next_val or 0) + count
    else:
        db.session.add(SavedSeq(next_val=count))