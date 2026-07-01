import requests
import threading
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


def send_extraction_notification(user_id, cursor, db, status: str, caption: str, place_count: int = 0):
    """status: 'success' | 'failed'"""
    title = "추출 완료" if status == "success" else "추출 실패"
    noti_caption = (caption or "").strip('\n')[:15].rstrip()

    if status == "success":
        db_title = "추출 완료"
        db_body = f"요청하신 게시물 추출이 완료되었습니다.\n{noti_caption}..."
        push_body = f"요청하신 게시물에서 {place_count}개의 장소를 추출했습니다. {noti_caption}...".rstrip()
    else:
        db_title = "추출 실패"
        db_body = f"게시물에서 장소 추출을 실패했습니다.\n{noti_caption}..."
        push_body = f"게시물에서 장소 추출을 실패했습니다. {noti_caption}...".rstrip()

    noti_query = """
        INSERT INTO notifications (user_id, sender_id, type, title, body, is_read, created_at)
        VALUES (%s, NULL, 'instagram_extract', %s, %s, 0, NOW())
    """
    cursor.execute(noti_query, (user_id, db_title, db_body))
    db.commit()

    token_query = """
        SELECT expo_push_token FROM devices 
        WHERE user_id = %s AND is_active = 1 AND expo_push_token IS NOT NULL
    """
    cursor.execute(token_query, (user_id,))
    target_device = cursor.fetchone()

    if target_device and target_device['expo_push_token']:
        thr = threading.Thread(
            target=send_expo_push_notification,
            args=(target_device['expo_push_token'], title, push_body)
        )
        thr.start()