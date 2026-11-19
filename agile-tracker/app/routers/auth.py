from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import current_user
from ..models import User
from ..schemas import LoginIn, RegisterIn, TokenOut, UserOut
from ..security import DUMMY_HASH, create_token, hash_password, login_limiter, verify_password

router = APIRouter(prefix="/api", tags=["Auth & users"])


@router.post("/auth/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    """Create an account. (Open sign-up keeps the demo simple; see README for production notes.)"""
    if not login_limiter.allow(f"reg:{request.client.host if request.client else '-'}"):
        raise HTTPException(429, "Too many attempts, try again in a minute")
    user = User(email=body.email.lower(), name=body.name, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "An account with this e-mail already exists")
    return user


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    key = f"{request.client.host if request.client else '-'}:{body.email.lower()}"
    if not login_limiter.allow(key):
        raise HTTPException(429, "Too many attempts, try again in a minute")
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if not user or not ok:
        raise HTTPException(401, "Invalid e-mail or password")  # same message for both cases
    return TokenOut(access_token=create_token(user.id), user=user)


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Team members (used for assignee pickers)."""
    return db.scalars(select(User).order_by(User.name)).all()
