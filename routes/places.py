from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db, Place, SavedPlace, SavedSeq
from services.push_notification import notify_place_bookmarked, notify_same_place_saved, is_following

user_places_bp = Blueprint("saved_places", __name__)

from services.my_logger import get_my_logger
logger = get_my_logger(__name__)


@user_places_bp.route("/places", methods=["POST"], strict_slashes=False)
@jwt_required()
def save_user_places():
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

        source_type = body.get("source_type")
        source_user_id = body.get("source_user_id")
        source_comment_id = body.get("source_comment_id")

        logger.debug(f"saved_ids: {saved_ids}, source_type: {source_type}, source_user_id: {source_user_id}")

        if saved_ids:
            _bump_saved_seq(len(saved_ids))

            should_notify_bookmark = (
                source_type in ("friend_profile", "comment")
                and source_user_id
                and source_user_id != user_id
                and is_following(source_user_id, user_id)  # source_user_id가 실제로 나를 팔로우 중인지 확인
            )
            logger.debug(
                f"[알림판단] should_notify_bookmark={should_notify_bookmark} "
                f"(source_type={source_type}, source_user_id={source_user_id}, user_id={user_id})"
            )

            if should_notify_bookmark:
                try:
                    logger.debug(
                        f"[알림호출] notify_place_bookmarked 시작 - "
                        f"recipient={source_user_id}, actor={user_id}, saved_ids={saved_ids}, comment_id={source_comment_id}"
                    )
                    result = notify_place_bookmarked(source_user_id, user_id, saved_ids, source_comment_id)  # #5
                    logger.debug(f"[알림호출] notify_place_bookmarked 결과: {result}")
                except Exception:
                    logger.exception(
                        f"[알림실패] notify_place_bookmarked 예외 발생 - "
                        f"recipient={source_user_id}, actor={user_id}, saved_ids={saved_ids}"
                    )
            else:
                logger.debug("[알림스킵] notify_place_bookmarked 조건 불충족")

            try:
                logger.debug(
                    f"[알림호출] notify_same_place_saved 시작 - "
                    f"user_id={user_id}, saved_ids={saved_ids}, exclude_user_id={source_user_id}"
                )
                result = notify_same_place_saved(user_id, saved_ids, exclude_user_id=source_user_id)  # #6
                logger.debug(f"[알림호출] notify_same_place_saved 결과: {result}")
            except Exception:
                logger.exception(
                    f"[알림실패] notify_same_place_saved 예외 발생 - "
                    f"user_id={user_id}, saved_ids={saved_ids}"
                )
        else:
            logger.debug(f"신규 저장 없음 (전부 중복) - user_id={user_id}, target_ids={target_ids}")

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

@user_places_bp.route("/places/<int:place_id>/toggle", methods=["POST"], strict_slashes=False)
@jwt_required()
def toggle_bookmark(place_id):
    """
    장소 북마크 토글 (저장 <-> 해제)
    ---
    tags:
      - Saved Places
    parameters:
      - name: place_id
        in: path
        type: integer
        required: true
      - in: body
        name: body
        required: false
        schema:
          type: object
          properties:
            save_type:
              type: string
              example: "spot"
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
        description: 토글 성공 (isMarked로 최종 상태 반환)
      404:
        description: 장소 없음
      500:
        description: 서버 에러
    """
    try:
        user_id = int(get_jwt_identity())
        body = request.get_json() or {}

        logger.debug(f"toggle_bookmark 요청 - user_id={user_id}, place_id={place_id}, body={body}")

        place = Place.query.filter_by(id=place_id).first()
        if not place:
            logger.warning(f"toggle_bookmark 실패 - place_id={place_id} 존재하지 않음")
            return jsonify({"status": "error", "message": "Place not found"}), 404

        existing = SavedPlace.query.filter_by(user_id=user_id, place_id=place_id).first()
        logger.debug(f"기존 저장 여부 - existing={'있음(id=' + str(existing.id) + ')' if existing else '없음'}")

        if existing:
            # 해제
            db.session.delete(existing)
            if place.saved_count and place.saved_count > 0:
                place.saved_count -= 1
            db.session.commit()
            logger.debug(f"북마크 해제 완료 - user_id={user_id}, place_id={place_id}, saved_count={place.saved_count}")
            return jsonify({"status": "success", "isMarked": False, "message": "unsaved"}), 200

        # 저장
        save_type = body.get("save_type", "spot")
        saved_ids = _do_save_places(user_id, {place_id}, save_type)
        logger.debug(f"신규 저장 결과 - saved_ids={saved_ids}")

        if saved_ids:
            _bump_saved_seq(len(saved_ids))

            source_type = body.get("source_type")
            source_user_id = body.get("source_user_id")
            source_comment_id = body.get("source_comment_id")

            should_notify_bookmark = (
                source_type in ("friend_profile", "comment")
                and source_user_id
                and source_user_id != user_id
                and is_following(source_user_id, user_id)  # source_user_id가 실제로 나를 팔로우 중인지 확인
            )
            logger.debug(
                f"[알림판단] should_notify_bookmark={should_notify_bookmark} "
                f"(source_type={source_type}, source_user_id={source_user_id}, user_id={user_id})"
            )

            if should_notify_bookmark:
                try:
                    logger.debug(f"[알림호출] notify_place_bookmarked 시작 - recipient={source_user_id}, actor={user_id}")
                    result = notify_place_bookmarked(source_user_id, user_id, saved_ids, source_comment_id)
                    logger.debug(f"[알림호출] notify_place_bookmarked 결과: {result}")
                except Exception:
                    logger.exception(f"[알림실패] notify_place_bookmarked 예외 - recipient={source_user_id}")
            else:
                logger.debug("[알림스킵] notify_place_bookmarked 조건 불충족")

            try:
                logger.debug(f"[알림호출] notify_same_place_saved 시작 - user_id={user_id}, saved_ids={saved_ids}")
                result = notify_same_place_saved(user_id, saved_ids, exclude_user_id=source_user_id)
                logger.debug(f"[알림호출] notify_same_place_saved 결과: {result}")
            except Exception:
                logger.exception(f"[알림실패] notify_same_place_saved 예외 - user_id={user_id}")
        else:
            logger.warning(f"toggle_bookmark 이상 상황 - existing=None인데 saved_ids도 비어있음. place_id={place_id}, user_id={user_id}")
        
        db.session.commit()
        logger.debug(f"북마크 저장 완료 - user_id={user_id}, place_id={place_id}")
        return jsonify({"status": "success", "isMarked": True, "message": "saved"}), 200

    except Exception as e:
        db.session.rollback()
        logger.exception(f"toggle_bookmark failed - user_id={user_id if 'user_id' in locals() else 'unknown'}, place_id={place_id}")
        return jsonify({"error": str(e)}), 500