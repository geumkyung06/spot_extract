from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

# insta_url table
class InstaUrl(db.Model):
    __tablename__ = 'insta_url'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    url = db.Column(db.String(255))      # 인스타 링크
    image = db.Column(db.String(255))    # 썸네일
    texts = db.Column(db.Text)           # Text?

# place table
class Place(db.Model):
    __tablename__ = 'place'

    # id는 ERD에 맞춰 BIGINT PK로 설정
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    name = db.Column(db.String(255), nullable=False)
    address = db.Column(db.String(255))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    list = db.Column(db.String(255))     # 카테고리
    photo = db.Column(db.String(255))
    rating_avg = db.Column(db.Float, default=0.0)
    rating_count = db.Column(db.Integer, default=0)
    saved_count = db.Column(db.Integer, default=0)

# 3. url_place table
class UrlPlace(db.Model):
    __tablename__ = 'url_place'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # ERD 컬럼명 준수
    instaurl_id = db.Column(db.BigInteger, db.ForeignKey('insta_url.id'))
    placeid_id = db.Column(db.BigInteger, db.ForeignKey('place.id'))

db = SQLAlchemy()

# 4. saved_place table
class SavedPlace(db.Model):
    __tablename__ = 'saved_place'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    user_id = db.Column(db.BigInteger, nullable=False)  # 유저 ID
    place_id = db.Column(db.BigInteger, db.ForeignKey('places.id'), nullable=False) # 장소 ID
    
    rating = db.Column(db.Integer, default=0)           # 유저 별점
    save_type = db.Column(db.String(255), default="spot") # 저장 유형 (instagram, spot)
    
    # 관계 설정 (저장된 장소 정보를 가져오기 위해)
    place = db.relationship('Place', backref='saved_by_users')