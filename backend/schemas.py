from pydantic import BaseModel, ConfigDict

from models import Role

#Auth 

class UserCreate(BaseModel):
    username: str
    full_name: str
    password: str
    role: Role

class TokenOutput(BaseModel):
    token: str
    token_type: str = "bearer"
    expires_in: int
    role: Role

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    role: Role
    is_active: bool
    