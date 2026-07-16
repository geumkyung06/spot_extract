import requests
import threading
from datetime import datetime

from models import db, Device, Notification, KakaoMem, Friend, Place, SavedPlace
from services.my_logger import get_my_logger

logger = get_my_logger(__name__)

def send_expo_push_notification(token, title, body):
    """
    Expo 서버로 푸시 알림을 발송하는 비동기 전용 함수
    """
    # Expo Push API 엔드포인트
    expo_push_url = 'https://exp.host/--/api/v2/push/send'

    headers = {
        'host': 'exp.host',
        'accept': 'application/json',
        'accept-encoding': 'gzip, deflate',
        'content-type': 'application/json'
    }

    payload = {
        'to': token,
        'title': title,
        'body': body,
        'sound': 'default'    }
    try:
        response = requests.post(expo_push_url, headers=headers, json=payload)
        logger.debug(f"[푸시 발송 결과] 상태코드: {response.status_code}, 내용: {response.text}")
    except Exception as e:
        logger.error(f"[푸시 발송 실패] {str(e)}")


def _push_async(token, title, body):
    if not token:
        return
    thr = threading.Thread(target=send_expo_push_notification, args=(token, title, body))
    thr.start()


def _get_active_token(user_id):
    device = db.session.query(Device).filter(
        Device.user_id == user_id,
        Device.is_active == True,
        Device.expo_push_token.isnot(None)
    ).first()
    return device.expo_push_token if device else None


def _get_actor_name(actor_id):
    mem = KakaoMem.query.get(actor_id)
    if not mem:
        return "친구"
    return mem.spot_nickname or mem.nickname


def get_follower_ids(user_id):
    """user_id를 팔로우하는 사람들(팔로워) id set 반환.
    Friend(member_id=X, friend_id=user_id, status='friend') -> X가 user_id를 팔로우 중."""
    rows = Friend.query.filter(
        Friend.friend_id == user_id,
        Friend.status == 'friend'
    ).all()
    return {row.member_id for row in rows if row.member_id is not None}


def get_following_ids(user_id):
    """user_id가 팔로우하는 사람들 id set 반환.
    Friend(member_id=user_id, friend_id=X, status='friend') -> user_id가 X를 팔로우 중."""
    rows = Friend.query.filter(
        Friend.member_id == user_id,
        Friend.status == 'friend'
    ).all()
    return {row.friend_id for row in rows if row.friend_id is not None}


def is_following(follower_id, target_id):
    """follower_id가 target_id를 팔로우하는지 여부."""
    row = Friend.query.filter(
        Friend.member_id == follower_id,
        Friend.friend_id == target_id,
        Friend.status == 'friend'
    ).first()
    return row is not None


def notify_place_bookmarked(recipient_id, actor_id, saved_place_ids, source_comment_id=None):
    """
    이벤트 #5: 친구가 내 장소 저장(북마크)
    recipient_id: 프로필/코멘트 주인 (알림 받을 사람)
    actor_id: 실제로 저장한 사람
    saved_place_ids: 이번에 새로 저장된 place_id 리스트
    """
    if not saved_place_ids or recipient_id == actor_id:
        return

    actor_name = _get_actor_name(actor_id)
    title = "친구가 내 장소 저장"
    target_type = "comment" if source_comment_id else "profile"

    places = Place.query.filter(Place.id.in_(saved_place_ids)).all()
    place_map = {p.id: p for p in places}

    for pid in saved_place_ids:
        place = place_map.get(pid)
        if not place:
            continue

        body = f"{actor_name}님이 회원님의 장소를 저장했습니다."

        noti = Notification(
            type="place_bookmarked",
            user_id=recipient_id,
            sender_id=actor_id,
            target_id=pid,
            target_type=target_type,
            title=title,
            body=body,
            route="place_detail",
            cta=None,
            is_read=False,
        )
        db.session.add(noti)

    if len(saved_place_ids) == 1:
        p = place_map.get(saved_place_ids[0])
        push_body = f"{actor_name}님이 회원님의 장소를 저장했습니다." + (f" ({p.name})" if p and p.name else "")
    else:
        push_body = f"{actor_name}님이 회원님의 장소 {len(saved_place_ids)}곳을 저장했습니다."

    token = _get_active_token(recipient_id)
    _push_async(token, title, push_body)


def notify_same_place_saved(actor_id, saved_place_ids, exclude_user_id=None):
    """
    이벤트 #6: 친구가 같은 장소 저장
    actor_id: 이번에 저장을 실행한 사람
    saved_place_ids: 이번에 새로 저장된 place_id 리스트
    exclude_user_id: #5로 이미 알림 나간 대상 (중복 방지)

    -> actor_id를 팔로우하는 사람들(팔로워) 중, 같은 place를 이미 저장한 사람에게 알림
    """
    if not saved_place_ids:
        return

    follower_ids = get_follower_ids(actor_id)
    if not follower_ids:
        return

    rows = (
        db.session.query(SavedPlace.user_id, SavedPlace.place_id)
        .filter(
            SavedPlace.place_id.in_(saved_place_ids),
            SavedPlace.user_id.in_(follower_ids),
        )
        .all()
    )

    if not rows:
        return

    place_ids_needed = {pid for _, pid in rows}
    places = Place.query.filter(Place.id.in_(place_ids_needed)).all()
    place_map = {p.id: p for p in places}

    actor_name = _get_actor_name(actor_id)
    title = "친구가 같은 장소 저장"

    by_follower = {}
    for follower_id, place_id in rows:
        if follower_id == exclude_user_id:
            continue
        by_follower.setdefault(follower_id, []).append(place_id)

    for follower_id, place_ids in by_follower.items():
        for pid in place_ids:
            place = place_map.get(pid)
            place_name = place.name if place else "장소"

            noti = Notification(
                type="friend_saved_same_place",
                user_id=follower_id,
                sender_id=actor_id,
                target_id=pid,
                target_type="place",
                title=title,
                body=f"{actor_name}님이 회원님과 같은 {place_name}을 저장했습니다.",
                route="place_detail",
                cta=None,
                is_read=False,
            )
            db.session.add(noti)

        if len(place_ids) == 1:
            p = place_map.get(place_ids[0])
            place_label = p.name if p and p.name else "장소"
            push_body = f"{actor_name}님이 회원님과 같은 {place_label}을 저장했습니다."
        else:
            push_body = f"{actor_name}님이 회원님과 같은 장소 {len(place_ids)}곳을 저장했습니다."

        token = _get_active_token(follower_id)
        _push_async(token, title, push_body)

def send_extraction_notification(user_id, status: str, caption: str, place_count: int = 0):
    """status: 'success' | 'failed'"""
    title = "추출 완료" if status == "success" else "추출 실패"
    noti_caption = (caption or "").strip('\n')[:15].rstrip()

    if status == "success":
        db_body = f"요청하신 게시물 추출이 완료되었습니다.\n{noti_caption}..."
        push_body = f"요청하신 게시물에서 {place_count}개의 장소를 추출했습니다. {noti_caption}...".rstrip()
    else:
        db_body = f"게시물에서 장소 추출을 실패했습니다.\n{noti_caption}..."
        push_body = f"게시물에서 장소 추출을 실패했습니다. {noti_caption}...".rstrip()

    try:
        new_noti = Notification(
            user_id=user_id,
            sender_id=None,
            type='instagram_extract',
            title=title,
            body=db_body,
            is_read=False
        )
        db.session.add(new_noti)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    token = _get_active_token(user_id)
    _push_async(token, title, push_body)