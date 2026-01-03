import os
import json
import mysql.connector
from dotenv import load_dotenv

def save_user_info_from_jwt(user_id):
    load_dotenv()  

    db_config_path = os.getenv("DB_CONFIG_PATH")  

    if not db_config_path:
        raise ValueError("DB_CONFIG_PATH가 .env에 정의되어 있지 않습니다.")

    with open(db_config_path, 'r') as f:
        config = json.load(f)

    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()

    query = "INSERT IGNORE INTO users (user_id) VALUES (%s)"
    cursor.execute(query, (user_id,))
    conn.commit()

    cursor.close()
    conn.close()
