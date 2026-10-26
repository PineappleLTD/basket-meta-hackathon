from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app import auth
from app.auth import CurrentUser
from app.db import SessionDep
from app.models import User
from app.schemas import AuthOut, Credentials, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=AuthOut, status_code=201)
def signup(body: Credentials, session: SessionDep):
    if session.scalar(select(User.id).where(User.email == body.email)) is not None:
        raise HTTPException(409, "An account with that email already exists")
    user = User(email=body.email, password_hash=auth.hash_password(body.password))
    session.add(user)
    session.flush()
    token = auth.start_session(session, user)
    session.commit()
    return AuthOut(token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=AuthOut)
def login(body: Credentials, session: SessionDep):
    user = auth.authenticate(session, body.email, body.password)
    if user is None:
        raise HTTPException(401, "Wrong email or password")
    token = auth.start_session(session, user)
    session.commit()
    return AuthOut(token=token, user=UserOut.model_validate(user))


@router.post("/logout", status_code=204)
def logout(session: SessionDep, token: Annotated[str, Depends(auth.current_token)]):
    """Ends this session only. Succeeds even if the token was already invalid."""
    auth.end_session(session, token)
    session.commit()


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user
