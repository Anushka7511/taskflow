from fastapi import APIRouter

from app.core.dependencies import AuthServiceDep, CurrentUser
from app.routers.responses import errors
from app.schemas.user import LoginRequest, TokenResponse, UserCreate, UserRead

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=201,
    summary="Register a new user",
    responses=errors(409, 422),
)
def register(data: UserCreate, service: AuthServiceDep):
    """Create an account.

    - **Auth:** none
    - **Errors:** 409 e-mail already registered (case-insensitive), 422 invalid input
    """
    return service.register(data)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in and obtain a JWT access token",
    responses=errors(401, 422),
)
def login(data: LoginRequest, service: AuthServiceDep):
    """Exchange e-mail + password for a bearer token.

    - **Auth:** none
    - **Errors:** 401 incorrect credentials (same message for unknown e-mail and wrong password)
    """
    return service.login(data)


@router.get("/me", response_model=UserRead, summary="Current user profile", responses=errors(401))
def me(user: CurrentUser):
    """Return the authenticated user. **Auth:** Bearer token required."""
    return user
