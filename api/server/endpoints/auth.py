from starlette.requests import Request
from fastapi import APIRouter, Depends

from api.security import (jwt_refresh_token_required, create_access_token, create_refresh_token, get_jwt_identity,
                          get_raw_jwt, decode_token)
from api.fastapi_config import FastApiConfig

from http import HTTPStatus

from api.server.utils.problem import Problem
from api_gateway.serverdb import User, db
from api.server.db.tokens import revoke_token

token_problem_title = 'Could not grant access token.'
invalid_username_password_problem = Problem(
    HTTPStatus.UNAUTHORIZED, token_problem_title, 'Invalid username or password.')
user_deactivated_problem = Problem(HTTPStatus.UNAUTHORIZED, token_problem_title, 'User is deactivated.')

router = APIRouter()


def _authenticate_and_grant_tokens(json_in, with_refresh=False):
    username = json_in.get('username', None)
    password = json_in.get('password', None)
    if not (username and password):
        return invalid_username_password_problem

    user = User.query.filter_by(username=username).first()
    if user is None:
        return invalid_username_password_problem
    try:
        password = password.encode('utf-8')
    except UnicodeEncodeError:
        return invalid_username_password_problem
    if not user.active:
        return user_deactivated_problem
    if user.verify_password(password):
        response = {'access_token': create_access_token(identity=user.id, fresh=True)}
        if with_refresh:
            user.login(request.environ.get('HTTP_X_REAL_IP', request.remote_addr))
            db.session.commit()
            response['refresh_token'] = create_refresh_token(identity=user.id)
        return response, HTTPStatus.CREATED
    else:
        return invalid_username_password_problem


@router.post("/auth")
def login(request: Request):
    json_in = await request.get_json()
    return _authenticate_and_grant_tokens(json_in, with_refresh=True)


def fresh_login(request: Request):
    json_in = await request.get_json()
    return _authenticate_and_grant_tokens(json_in)


@jwt_refresh_token_required
@router.post("/auth/refresh")
def refresh(request: Request):
    current_user_id = get_jwt_identity(request)
    user = User.query.filter(User.id == current_user_id).first()
    if user is None:
        revoke_token(get_raw_jwt(request))
        return Problem(
            HTTPStatus.UNAUTHORIZED,
            "Could not grant access token.",
            f"User {current_user_id} from refresh JWT identity could not be found.")
    if user.active:
        return {'access_token': create_access_token(identity=current_user_id)}, HTTPStatus.CREATED
    else:
        return user_deactivated_problem


@router.post("/auth/logout")
def logout(request: Request):
    data = request.get_json()
    refresh_token = data.get('refresh_token', None) if data else None
    if refresh_token is None:
        return Problem(HTTPStatus.BAD_REQUEST, 'Could not logout.', 'A refresh token is required to logout.')
    decoded_refresh_token = decode_token(refresh_token)
    refresh_token_identity = decoded_refresh_token[FastApiConfig.JWT_IDENTITY_CLAIM]
    user_id = get_jwt_identity()
    if user_id == refresh_token_identity:
        user = User.query.filter(User.id == user_id).first()
        if user is not None:
            user.logout()
        revoke_token(decode_token(refresh_token))
        return None, HTTPStatus.NO_CONTENT
    else:
        return Problem(
            HTTPStatus.BAD_REQUEST,
            'Could not logout.',
            'The identity of the refresh token does not match the identity of the authentication token.')

