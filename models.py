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
    texts = db.Column(db.Text)           # 캡션 전체 저장

# place table
class Place(db.Model):
    __tablename__ = 'place'

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    name = db.Column(db.String(255)) 
    address = db.Column(db.String(255))
    
    gid = db.Column(db.String(255), unique=True, nullable=False)

    latitude = db.Column(db.Float)  
    longitude = db.Column(db.Float)
    
    list = db.Column(db.Enum('accessory','bar','cafe','cloth','etc','restaurant')) 
    photo = db.Column(db.String(1000))
    
    rating_avg = db.Column(db.Float, default=0.0)
    rating_count = db.Column(db.Integer, default=0, nullable=False)
    saved_count = db.Column(db.Integer, default=0, nullable=False)
    score = db.Column(db.Float, default=0.0, nullable=False)
    search_count = db.Column(db.Integer, default=0, nullable=False)
    place_area_id = db.Column(db.BigInteger, nullable=True)

# list : exhibition, activitiy, prop_shop, clothing_store > cloth , dessert, cafe, bar, restaurant, etc

# 3. url_place table
class UrlPlace(db.Model):
    __tablename__ = 'url_place'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # ERD 컬럼명 준수
    instaurl_id = db.Column(db.BigInteger, db.ForeignKey('insta_url.id'))
    placeid_id = db.Column(db.BigInteger, db.ForeignKey('place.id'))

# 4. saved_place table
class SavedPlace(db.Model):
    __tablename__ = 'saved_place'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    user_id = db.Column(db.BigInteger, nullable=False)  # 유저 ID
    place_id = db.Column(db.BigInteger, db.ForeignKey('place.id'), nullable=False) # 장소 ID
    
    rating = db.Column(db.Integer, default=0)           # 유저 별점
    save_type = db.Column(db.String(255), default="spot") # 저장 유형 (instagram, spot)
    
    # 관계 설정 (저장된 장소 정보를 가져오기 위해)
    place = db.relationship('Place', backref='saved_by_users')

# 5. kakao_mem table
class KakaoMem(db.Model):
    __tablename__ = 'kakao_mem'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    email = db.Column(db.String(255), unique=True, nullable=False)
    
    info = db.Column(db.Text) 
    
    kakao_id = db.Column(db.String(255), unique=True, nullable=False)
    nickname = db.Column(db.String(255), nullable=False) 
    
    password = db.Column(db.String(255)) 
    photo = db.Column(db.String(255)) 
    spot_nickname = db.Column(db.String(255))


# 6. friend table
class Friend(db.Model):
    __tablename__ = 'friend'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    status = db.Enum('block','friend','give','waiting') # block 이면 친구 삭제되게 해야함
    
    friend_id = db.Column(db.BigInteger, db.ForeignKey('user.id')) 
    
    member_id = db.Column(db.BigInteger, nullable=True)  
    