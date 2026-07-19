from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from auth import (
    EXPIRE_MINS, authenticate_user, create_access_token,
    get_current_user, hash_password, _verify_password,
    validate_password_strength,
)
from database import get_db
from schemas import LoginRequest, TokenOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
async def login_form(form: OAuth2PasswordRequestForm = Depends()):
    user = authenticate_user(form.username, form.password)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="Incorrect username or password",
                            headers={"WWW-Authenticate": "Bearer"})
    token = create_access_token({"sub": user["username"]},
                                timedelta(minutes=EXPIRE_MINS))
    return TokenOut(access_token=token, token_type="bearer",
                    username=user["username"], role=user["role"])


@router.post("/login/json", response_model=TokenOut)
async def login_json(body: LoginRequest):
    user = authenticate_user(body.username, body.password)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="Incorrect username or password")
    token = create_access_token({"sub": user["username"]},
                                timedelta(minutes=EXPIRE_MINS))
    return TokenOut(access_token=token, token_type="bearer",
                    username=user["username"], role=user["role"])


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    return {"username": current_user["username"], "role": current_user["role"]}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password:     str


@router.post("/change-password")
async def change_password(
    body:         ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
    conn               = Depends(get_db),
):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT password_hash FROM users WHERE username=%s LIMIT 1",
            (current_user["username"],),
        )
        row = cur.fetchone()
    finally:
        cur.close()

    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")

    if not _verify_password(body.current_password, row["password_hash"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Current password is incorrect")

    if body.current_password == body.new_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="New password must differ from current password")

    validate_password_strength(body.new_password)

    new_hash = hash_password(body.new_password)
    cur2 = conn.cursor()
    try:
        cur2.execute(
            "UPDATE users SET password_hash=%s WHERE username=%s",
            (new_hash, current_user["username"]),
        )
    finally:
        cur2.close()

    return {"message": "Password changed successfully"}
