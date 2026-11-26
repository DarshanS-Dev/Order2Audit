"""Auth endpoints"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from services import auth_service
from database import get_db
from models import Role, User
from schemas import UserCreate, UserOut, TokenOutput

router = APIRouter(prefix="/auth", tags=["auth"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

DbDep = Annotated[Session, Depends(get_db)]

def get_current_user(token: Annotated[str, Depends(oauth2_scheme)], db: DbDep) -> User:
    return auth_service.get_user_from_token(db, token)


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*allowed: Role):
    """Usage: Depends(require_roles(Role.ADMIN, Role.OWNER))"""

    def checker(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission for this action",
            )
        return user

    return checker

@router.post(
    "/register",
    response_model=TokenOutput,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(Role.ADMIN))],
)
def register(user: UserCreate, db: DbDep):
    """Register a new user"""
    result = db.execute(select(User).where(User.username==user.username))
    is_exists = result.scalar_one_or_none()

    if is_exists:
        raise HTTPException(400, "Email already exists")

    hashed_password = auth_service.hash_pass(user.password)

    new_user = User(
        username=user.username,
        full_name=user.full_name,
        password=hashed_password,
        role=user.role,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    data = {
        "sub": str(new_user.id),
        "role": new_user.role.value,
    }

    return {"token": auth_service.create_token(data)}

@router.post("/login", response_model=TokenOutput)
def login(db: DbDep,form_data: OAuth2PasswordRequestForm = Depends()):
    """Login a user"""
    result = db.execute(select(User).where(User.username==form_data.username))
    user = result.scalar_one_or_none()

    if not user or not auth_service.verify_pass(form_data.password, user.password):
        raise HTTPException(401, "Invalid username or password")

    data = {
        "sub": str(user.id),
        "role": user.role.value,
    }

    return {"token": auth_service.create_token(data)}

@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user