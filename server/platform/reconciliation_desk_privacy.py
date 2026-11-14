"""Bounded operator text, with credentials/contact/card/raw-notice exclusion."""

import re

from ..core import ApiError

EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
KEY = re.compile(r"\b(?:test|live)_(?:g?sk|g?ck)_[A-Za-z0-9_=-]+", re.I)
PHONE = re.compile(r"(?<!\d)0(?:1[016789]|[2-6]\d)[ -]?\d{3,4}[ -]?\d{4}(?!\d)")
CARD = re.compile(r"(?<![\w])(?:\d[ -]?){12,18}\d(?![\w])")
RAW = re.compile(r"\{[\s\S]*[\"'](?:paymentKey|eventType|secret|customerKey)[\"']\s*:|tosspayments-webhook-signature\s*:", re.I)


def scrub(value, secrets=()):
    value = str(value or "")[:1500]
    if RAW.search(value):
        return "[원문 식별정보를 제외한 별도 검토 메모가 필요합니다.]"
    for secret in secrets:
        if isinstance(secret, str) and len(secret.strip()) >= 6:
            value = value.replace(secret, "[식별정보 제외]")
    for pattern in (EMAIL, KEY, PHONE, CARD):
        value = pattern.sub("[식별정보 제외]", value)
    return value


def checked_note(value, secrets=(), maximum=1500):
    if not isinstance(value, str) or not 10 <= len(value.strip()) <= maximum:
        raise ApiError(400, "검토 사유를10–{}자로 입력해주세요.".format(maximum))
    note = value.strip()
    if scrub(note, secrets) != note:
        raise ApiError(400, "카드·연락처·결제 식별자·키·웹훅 원문을 제외하고 검토 내용을 적어주세요.")
    return note
