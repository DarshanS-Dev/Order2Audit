import jwt 
from jwt.exceptions import InvalidTokenError
from datetime import datetime, timedelta, timezone
import bcrypt
from fastapi import HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer
from typing import Annotated

from config import settings
from models import Role, User
from database import Session, get_db

oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")

def hash_pass(raw: str) -> str:
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()

def verify_pass(raw: str, hashed: str) -> bool:
    return bcrypt.checkpw(raw.encode(), hashed.encode())

def create_token(data: dict):
    to_encode = data.copy()
    expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
    to_encode.update({"exp": expiry})
    return jwt.encode(to_encode, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)

def verify_token(token: str):
    try:
        payload = jwt.decode(token, settings.secret_key.get_secret_value(), algorithms=[settings.algorithm])
        return payload
    except InvalidTokenError:          # replaces JWTError
        raise HTTPException(401, "Invalid token")

def get_user_from_token(db: Session, token: str) -> User:
    payload = verify_token(token)
    user_id = int(payload["sub"])

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "User not found or inactive")
    return user