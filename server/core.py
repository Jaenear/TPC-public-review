"""Shared validation and identifiers for platform domains."""

import re
import uuid
from datetime import datetime, timezone


def now_iso():
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def new_id(prefix):
    return prefix + "_" + uuid.uuid4().hex


class ApiError(Exception):
    def __init__(self, status, message, headers=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = headers or {}


def text_field(data, key, minimum=1, maximum=2000, optional=False):
    value = data.get(key, "" if optional else None)
    if not isinstance(value, str):
        raise ApiError(400, "{} 항목을 확인해주세요.".format(key))
    value = value.strip()
    if (len(value) < minimum and not (optional and not value)) or len(value) > maximum:
        raise ApiError(
            400, "{} 항목은 {}~{}자로 입력해주세요.".format(key, minimum, maximum)
        )
    if "\x00" in value:
        raise ApiError(400, "올바르지 않은 문자가 포함되어 있습니다.")
    return value


def choice_field(data, key, choices):
    value = text_field(data, key, maximum=100)
    if value not in choices:
        raise ApiError(400, "{} 항목을 선택해주세요.".format(key))
    return value


def email_field(data):
    email = text_field(data, "email", maximum=254).lower()
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email):
        raise ApiError(400, "이메일 주소를 확인해주세요.")
    return email


def password_field(data, registering=False):
    password = data.get("password")
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        raise ApiError(400, "비밀번호를 확인해주세요.")
    if registering and len(password) < 8:
        raise ApiError(400, "비밀번호는 8자 이상으로 입력해주세요.")
    return password
