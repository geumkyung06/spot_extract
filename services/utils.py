import os

BUCKET_NAME = os.getenv("BUCKET_NAME")
S3_BASE_URL = os.environ.get("S3_BASE_URL", f"https://{BUCKET_NAME}.s3.ap-northeast-2.amazonaws.com")

def get_full_photo_url(photo_path):
    """DB에 저장된 상대 경로를 받아 전체 S3 URL 리스트 또는 문자열로 반환"""
    if not photo_path:
        return ""
    base_url = S3_BASE_URL.rstrip('/')
    
    return [f"{base_url}/{p.strip().lstrip('/')}" for p in photo_path.split(",") if p.strip()]