"""Private, purpose-bound identity primitives. Keys are supplied explicitly."""
from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import re
from types import MappingProxyType

from .core import ApiError

PURPOSES = ('verify_email', 'change_email', 'reset_password')
KEY = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
KEY_REF = re.compile(r'^[A-Za-z0-9_-]{1,16}$')
ID = re.compile(r'^[A-Za-z0-9_-]{1,100}$')
CODES = frozenset(('invalid_input', 'invalid_token', 'not_ready', 'key_unavailable',
                   'unauthorized', 'forbidden', 'conflict', 'context_changed',
                   'not_found', 'expired', 'superseded', 'security_change'))


class IdentityError(ApiError):
    def __init__(self, status, code):
        self.code = code if code in CODES else 'invalid_input'
        message = {400: '인증 요청이나 링크가 유효하지 않습니다.',
                   401: '로그인 상태를 다시 확인해주세요.',
                   403: '현재 계정의 권한을 확인해주세요.',
                   404: '계정 처리 내역을 찾을 수 없습니다.',
                   409: '계정 정보나 요청 상태가 변경됐습니다.',
                   503: '계정 인증 처리 환경이 준비되지 않았습니다.'}.get(status, '계정 처리 상태를 확인해주세요.')
        super().__init__(status, message)


def require(value, code='invalid_input', status=400):
    if not value:
        raise IdentityError(status, code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def token_digest(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


@dataclass(frozen=True, repr=False)
class IdentityKeyring:
    active: str
    keys: object
    mode: str = 'synthetic'

    def __post_init__(self):
        require(type(self.keys) is dict and 1 <= len(self.keys) <= 8)
        require(self.mode in ('synthetic', 'external'))
        checked = {}
        for reference, key in self.keys.items():
            require(isinstance(reference, str) and KEY_REF.fullmatch(reference))
            require(type(key) is bytes and 32 <= len(key) <= 64)
            checked[reference] = key
        require(self.active in checked)
        object.__setattr__(self, 'keys', MappingProxyType(checked))

    def __repr__(self):
        return '<IdentityKeyring redacted>'

    def fingerprint(self, domain, value, reference=None):
        reference = reference or self.active
        require(reference in self.keys, 'key_unavailable', 503)
        return hmac.new(self.keys[reference], b'TPC-IDENTITY-v1\0'+domain.encode('ascii')+b'\0'+canonical(value).encode(), hashlib.sha256).hexdigest()

    def credential_stamp(self, user, reference):
        return self.fingerprint('credential', {'userId': user['id'], 'email': user['email'].lower(),
                                               'passwordHash': user['password_hash']}, reference)

    def token(self, challenge):
        reference = challenge['key_ref']
        require(reference in self.keys, 'key_unavailable', 503)
        identifier = challenge['id']
        require(isinstance(identifier, str) and re.fullmatch(r'ichallenge_[a-f0-9]{32}', identifier))
        header = b'\x01'+bytes([len(reference)])+reference.encode('ascii')+bytes.fromhex(identifier[len('ichallenge_'):])
        context = {k: challenge[k] for k in ('id', 'user_id', 'purpose', 'source_email', 'candidate_email',
                   'credential_stamp', 'credential_epoch', 'nonce', 'created_at', 'expires_at')}
        mac = hmac.new(self.keys[reference], b'TPC-IDENTITY-TOKEN-v1\0'+header+b'\0'+canonical(context).encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(header+mac).rstrip(b'=').decode('ascii')

    @staticmethod
    def parse(value):
        require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{68,100}', value), 'invalid_token')
        try:
            raw = base64.b64decode(value+'='*((-len(value)) % 4), altchars=b'-_', validate=True)
            require(base64.urlsafe_b64encode(raw).rstrip(b'=').decode() == value, 'invalid_token')
            require(raw[0] == 1 and 1 <= raw[1] <= 16 and len(raw) == 50+raw[1], 'invalid_token')
            reference = raw[2:2+raw[1]].decode('ascii')
            require(KEY_REF.fullmatch(reference), 'invalid_token')
            identifier = 'ichallenge_'+raw[2+raw[1]:18+raw[1]].hex()
            return identifier, reference
        except (ValueError, IndexError, UnicodeError):
            raise IdentityError(400, 'invalid_token') from None

    def check(self, token, challenge):
        identifier, reference = self.parse(token)
        require(identifier == challenge['id'] and reference == challenge['key_ref'], 'invalid_token')
        require(hmac.compare_digest(self.token(challenge), token) and
                hmac.compare_digest(token_digest(token), challenge['token_hash']), 'invalid_token')
