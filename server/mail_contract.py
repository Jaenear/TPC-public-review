"""Private transport contracts and versioned transactional templates.

No SMTP credentials, network calls, persisted bodies or raw identity tokens.
Identity authorities are injected explicitly; no keys or transport are discovered.
"""
from dataclasses import dataclass
import hashlib
import html
import ipaddress
import json
import re
from types import MappingProxyType
from urllib.parse import parse_qs, urlsplit

from .core import ApiError

KEY = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
ID = re.compile(r'^[A-Za-z0-9_-]{1,100}$')
OPAQUE = re.compile(r'^[A-Za-z0-9_.-]{1,128}$')
CODES = frozenset(('queued', 'claimed', 'accepted', 'delivered', 'bounced',
    'not_ready', 'invalid_input', 'not_found', 'conflict', 'source_unavailable',
    'context_changed', 'member_changed', 'recipient_changed', 'suppressed',
    'expired', 'provider_rejected', 'provider_unknown', 'lease_expired',
    'cancelled', 'configuration_changed', 'template_changed', 'attempt_limit',
    'definitely_not_submitted', 'hard_bounce', 'complaint', 'operator_hold',
    'operator_release'))


class MailError(ApiError):
    def __init__(self, status, code):
        self.code = code if code in CODES else 'invalid_input'
        message = {400: '메일 작업의 입력과 계약을 확인해주세요.',
                   401: '로그인 상태를 다시 확인해주세요.',
                   403: '현재 관리자 권한이 필요합니다.',
                   404: '메일 작업을 찾을 수 없습니다.',
                   409: '메일 작업이나 전달 조건이 변경됐습니다.',
                   503: '메일 전달 환경이나 연결 업무가 준비되지 않았습니다.'}.get(status, '메일 처리 상태를 확인해주세요.')
        super().__init__(status, message)


def require(value, code='invalid_input', status=400):
    if not value:
        raise MailError(status, code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def address(value):
    require(isinstance(value, str) and 3 <= len(value) <= 254)
    require(re.fullmatch(r'[^@\s<>\x00-\x1f\x7f]+@[^@\s<>\x00-\x1f\x7f]+\.[^@\s<>\x00-\x1f\x7f]+', value))
    return value.lower()


def address_hash(value):
    # Internal index only. This is not anonymisation or an exported identifier.
    return hashlib.sha256(address(value).encode('utf-8')).hexdigest()


@dataclass(frozen=True, repr=False)
class TrustedMailRecipient:
    """A backend source-authorized target bound to the same source fingerprint."""
    address: str
    source_hash: str

    def __post_init__(self):
        require(address(self.address) == self.address)
        require(isinstance(self.source_hash, str) and re.fullmatch(r'[a-f0-9]{64}', self.source_hash))

    def __repr__(self):
        return '<TrustedMailRecipient redacted>'


@dataclass(frozen=True, repr=False)
class MailProfile:
    reference: str
    application_origin: str
    sender_address: str
    mode: str = 'synthetic'

    def __post_init__(self):
        require(isinstance(self.reference, str) and ID.fullmatch(self.reference))
        require(self.mode in ('synthetic', 'external'))
        address(self.sender_address)
        require(isinstance(self.application_origin, str))
        try:
            p = urlsplit(self.application_origin)
        except ValueError:
            raise MailError(400, 'invalid_input') from None
        require(p.scheme == 'https' and bool(p.hostname) and p.username is None and p.password is None and not p.query and not p.fragment and p.path in ('', '/') and self.application_origin == self.application_origin.rstrip('/'))
        require(p.netloc.isascii() and not re.search(r'[\s\x00-\x1f\x7f]', p.netloc))
        require(self.application_origin == 'https://'+p.netloc.lower())
        try:
            ipaddress.ip_address(p.hostname)
        except ValueError:
            require(re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p.hostname))
        try:
            require(p.port is None or 1 <= p.port <= 65535)
        except ValueError:
            raise MailError(400, 'invalid_input') from None

    @property
    def fingerprint(self):
        return digest({'reference': self.reference, 'origin': self.application_origin,
                       'sender': address(self.sender_address), 'mode': self.mode})

    def __repr__(self):
        return '<MailProfile redacted>'


@dataclass(frozen=True)
class MailTemplate:
    id: str
    version: int
    title: str
    introduction: str
    action_label: str
    path: str
    identity: bool

    @property
    def fingerprint(self):
        return digest(self.__dict__)


TEMPLATES = MappingProxyType({(t.id, t.version): t for t in (
    MailTemplate('account_notice', 1, 'TPC 계정 안내', '계정 정보와 캠프 이용 안내를 확인해주세요.', '마이 캠프 열기', '/my', False),
    MailTemplate('verify_email', 1, 'TPC 이메일 인증', '본인이 요청한 이메일 인증을 완료해주세요.', '이메일 인증하기', '/account/verify', True),
    MailTemplate('change_email', 1, 'TPC 이메일 변경 확인', '본인이 요청한 이메일 변경을 확인해주세요.', '이메일 변경 확인', '/account/email-change', True),
    MailTemplate('reset_password', 1, 'TPC 비밀번호 재설정', '본인이 요청한 비밀번호 재설정을 진행해주세요.', '비밀번호 재설정', '/account/reset', True),
)})


def template_for(identifier, version):
    require(type(version) is int and isinstance(identifier, str))
    t = TEMPLATES.get((identifier, version))
    require(t is not None)
    return t


def checked_source(value):
    require(type(value) is dict and set(value) == {'kind', 'id', 'version'})
    require(value['kind'] in ('account', 'identity_challenge', 'identity_notice'))
    require(isinstance(value['id'], str) and ID.fullmatch(value['id']))
    require(type(value['version']) is int and value['version'] >= 1)
    return dict(value)


def action_url(value, profile, template):
    require(isinstance(value, str) and len(value) <= 2048)
    require(not re.search(r'[\s\x00-\x1f\x7f]', value))
    try:
        p, origin = urlsplit(value), urlsplit(profile.application_origin)
    except ValueError:
        raise MailError(400, 'invalid_input') from None
    require(p.scheme == origin.scheme and p.netloc == origin.netloc and p.username is None and p.password is None and p.path in ('', '/') and not p.query)
    if not template.identity:
        require(p.fragment == template.path)
    else:
        fragment = urlsplit(p.fragment)
        require(fragment.path == template.path and not fragment.fragment)
        try:
            values = parse_qs(fragment.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
        except ValueError:
            raise MailError(400, 'invalid_input') from None
        require(set(values) == {'token'} and len(values['token']) == 1 and re.fullmatch(r'[A-Za-z0-9_-]{16,256}', values['token'][0]))
    return value


@dataclass(frozen=True, repr=False)
class MailEnvelope:
    sender: str
    recipient: str
    subject: str
    text: str
    html: str

    @property
    def fingerprint(self):
        return digest(self.__dict__)

    def __repr__(self):
        return '<MailEnvelope redacted>'


def render(template, profile, recipient, display_name, url):
    require(isinstance(profile, MailProfile))
    address(recipient)
    require(isinstance(display_name, str) and 2 <= len(display_name) <= 50 and not re.search(r'[\x00-\x1f\x7f]', display_name))
    action_url(url, profile, template)
    text = display_name+'님,\n\n'+template.introduction+'\n'+template.action_label+': '+url+'\n\n본인이 요청하지 않았다면 이 메일의 링크를 사용하지 마세요.\nTPC'
    body = '<!doctype html><html lang="ko"><body><p>'+html.escape(display_name)+'님,</p><p>'+html.escape(template.introduction)+'</p><p><a href="'+html.escape(url, quote=True)+'">'+html.escape(template.action_label)+'</a></p><p>본인이 요청하지 않았다면 이 메일의 링크를 사용하지 마세요.</p><p>TPC</p></body></html>'
    return MailEnvelope(address(profile.sender_address), address(recipient), template.title, text, body)


@dataclass(frozen=True, repr=False)
class MailClaim:
    job_id: str
    attempt_id: str
    lease_token: str

    def __post_init__(self):
        require(isinstance(self.job_id, str) and ID.fullmatch(self.job_id))
        require(isinstance(self.attempt_id, str) and ID.fullmatch(self.attempt_id))
        require(isinstance(self.lease_token, str) and re.fullmatch(r'[A-Za-z0-9_-]{32,128}', self.lease_token))

    def __repr__(self):
        return '<MailClaim redacted>'


@dataclass(frozen=True, repr=False)
class SubmissionResult:
    outcome: str
    code: str
    provider_reference: str = None

    def __post_init__(self):
        require(self.outcome in ('accepted', 'not_submitted', 'rejected', 'unknown'))
        allowed = {'accepted': {'accepted'}, 'not_submitted': {'definitely_not_submitted'},
                   'rejected': {'provider_rejected'}, 'unknown': {'provider_unknown'}}
        require(self.code in allowed[self.outcome])
        require(self.provider_reference is None or isinstance(self.provider_reference, str) and OPAQUE.fullmatch(self.provider_reference))
        require(self.outcome != 'accepted' or self.provider_reference is not None)

    @property
    def fingerprint(self):
        return digest(self.__dict__)

    def __repr__(self):
        return '<SubmissionResult redacted>'


@dataclass(frozen=True, repr=False)
class DeliveryEvidence:
    """For an already verified trusted adapter. No HTTP feedback endpoint yet."""
    attempt_id: str
    profile_hash: str
    event_key: str
    delivery_key: str
    provider_reference: str
    outcome: str

    def __post_init__(self):
        require(isinstance(self.attempt_id, str) and ID.fullmatch(self.attempt_id))
        require(isinstance(self.profile_hash, str) and re.fullmatch(r'[a-f0-9]{64}', self.profile_hash))
        require(isinstance(self.event_key, str) and KEY.fullmatch(self.event_key))
        require(isinstance(self.delivery_key, str) and KEY.fullmatch(self.delivery_key))
        require(isinstance(self.provider_reference, str) and OPAQUE.fullmatch(self.provider_reference))
        require(self.outcome in ('delivered', 'bounced'))

    @property
    def fingerprint(self):
        return digest(self.__dict__)

    def __repr__(self):
        return '<DeliveryEvidence redacted>'


class DefinitelyNotSubmitted(Exception):
    """Only an adapter that knows it sent nothing may raise this signal."""
    def __str__(self):
        return 'Mail submission did not start'

    def __repr__(self):
        return '<DefinitelyNotSubmitted>'


class UnavailableMailAdapter:
    ready = False
    mode = 'unconfigured'
    supports_idempotency = False
    supports_query = False
    def submit(self, envelope, delivery_key):
        raise MailError(503, 'not_ready')


class AccountMailSource:
    """Existing account facts only; verification/recovery authority stays absent."""
    identity_ready = False

    def fingerprint_in(self, connection, user, source, template):
        if template.identity:
            raise MailError(503, 'source_unavailable')
        if source != {'kind': 'account', 'id': user['id'], 'version': user['version']}:
            return None
        return digest({'source': source, 'purpose': template.id, 'userId': user['id'],
                       'status': user['status'], 'email': address(user['email'])})

    def url_in(self, connection, job, source, template, profile):
        return profile.application_origin+'/#'+template.path
