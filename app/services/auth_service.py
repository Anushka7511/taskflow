import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import (
    create_access_token,
    decode_access_token,
    dummy_password_hash,
    hash_password,
    verify_password,
)
from app.database.errors import violated_constraint
from app.database.session import unit_of_work
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.user import LoginRequest, TokenResponse, UserCreate


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)

    def register(self, data: UserCreate) -> User:
        if self.users.get_by_email(data.email) is not None:
            raise ConflictError("Email is already registered")
        user = User(name=data.name, email=data.email, password_hash=hash_password(data.password))
        try:
            with unit_of_work(self.db):
                self.users.add(user)
        except IntegrityError as exc:
            # Two simultaneous registrations can both pass the check above;
            # the unique constraint is the real guarantee.
            if violated_constraint(exc) == "uq_users_email":
                raise ConflictError("Email is already registered") from exc
            raise
        return user

    def login(self, data: LoginRequest) -> TokenResponse:
        user = self.users.get_by_email(data.email)
        # Always run one Argon2 verification so timing does not reveal whether the e-mail exists.
        password_ok = verify_password(user.password_hash if user else dummy_password_hash(), data.password)
        if user is None or not password_ok:
            raise UnauthorizedError("Incorrect email or password")
        token, expires_in = create_access_token(str(user.id))
        return TokenResponse(access_token=token, expires_in=expires_in)

    def user_from_token(self, token: str) -> User:
        subject = decode_access_token(token)
        try:
            user_id = uuid.UUID(subject)
        except ValueError:
            raise UnauthorizedError("Invalid authentication token") from None
        user = self.users.get(user_id)
        if user is None:
            raise UnauthorizedError("User no longer exists")
        return user
