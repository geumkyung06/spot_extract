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