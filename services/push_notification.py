import requests
import threading
from models import db, Device, Notification
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
        'sound': 'default'
    }
    
    try:
        response = requests.post(expo_push_url, headers=headers, json=payload)
        logger.debug(f"[푸시 발송 결과] 상태코드: {response.status_code}, 내용: {response.text}")
    except Exception as e:
        logger.error(f"[푸시 발송 실패] {str(e)}")

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
    except Exception as e:
        db.session.rollback()
        raise

    target_device = db.session.query(Device).filter(
        Device.user_id == user_id,
        Device.is_active == True,
        Device.expo_push_token.isnot(None)
    ).first()

    if target_device and target_device.expo_push_token:
        thr = threading.Thread(
            target=send_expo_push_notification,
            args=(target_device.expo_push_token, title, push_body)
        )
        thr.start()