"""Explicit same-origin identity commands; no link GET consumes a token."""
from urllib.parse import parse_qs, urlsplit

from ..core import ApiError
from ..identity_contract import require
from .identity_link_views import IdentityLinkViews

PROFILE = '/api/me/profile'
REQUESTS = {
    '/api/account/identity/verification-requests': 'verify_email',
    '/api/account/identity/email-change-requests': 'change_email',
}
COMPLETIONS = {
    '/api/account/identity/verification-completions': 'verify_email',
    '/api/account/identity/email-change-completions': 'change_email',
    '/api/auth/recovery/completions': 'reset_password',
}
RECOVERY_REQUEST = '/api/auth/recovery/requests'
INSPECTIONS = {
    '/api/account/identity/verification-inspections': 'verify_email',
    '/api/account/identity/email-change-inspections': 'change_email',
    '/api/auth/recovery/inspections': 'reset_password',
}


class IdentityCommandsAPI:
    def __init__(self, identity, recovery):
        self.identity, self.recovery, self.app = identity, recovery, identity.app
        self.views = IdentityLinkViews(identity)

    def _actor(self, h):
        with self.app.db.connect() as c:
            return self.identity._actor(c, h.user)

    def _profile(self, actor):
        with self.app.db.connect() as c:
            c.execute('BEGIN')
            user = self.identity._actor(c, actor)
            summary = self.identity._summary_in(c, user)
            return {'profile': {'id': user['id'], 'name': user['name'], 'email': user['email'],
                                'version': user['version']},
                    'identity': summary,
                    'requests': self.views.requests_in(c, user),
                    'capabilities': {'identityKeyConfigured': self.identity.keyring is not None,
                                     'identityKeyMode': self.identity.keyring.mode if self.identity.keyring else 'unconfigured',
                                     'recoveryProcessing': 'automatic_when_configured',
                                     'realMailDeliveryAccepted': False}}

    def _limit(self, h, category, limit, period, value=None):
        self.app.limiter.check(('identity-http', category, h.client_address[0]), limit, period, busy_timeout_ms=2000)
        if value is not None:
            self.app.limiter.check(('identity-input', category, value), limit, period, busy_timeout_ms=2000)

    @staticmethod
    def _query(h):
        require(not parse_qs(urlsplit(h.path).query, keep_blank_values=True, max_num_fields=100))

    def route(self, h, path, method, query):
        known = path == PROFILE or path == RECOVERY_REQUEST or path in REQUESTS or path in COMPLETIONS or path in INSPECTIONS
        public_prefix = path == '/api/auth/recovery' or path.startswith('/api/auth/recovery/')
        if not known and not public_prefix:
            return None
        # Current self permission precedes protected method/body validation.
        needs_session = path == PROFILE or path in REQUESTS or (path in COMPLETIONS and COMPLETIONS[path] != 'reset_password') or (path in INSPECTIONS and INSPECTIONS[path] != 'reset_password')
        actor = self._actor(h) if needs_session else None
        self._query(h)
        if path == PROFILE:
            if method == 'GET':
                return 200, self._profile(actor), {}
            if method != 'PATCH':
                raise ApiError(405, '본인 정보는 조회하거나 표시이름을 수정할 수 있습니다.')
            self._limit(h, 'profile', 20, 60, actor['id'])
            data = h.body(strict=True)
            require(set(data) == {'requestKey', 'expectedVersion', 'name'})
            result = self.identity.save_profile(actor, {'requestKey': data['requestKey'],
                                                       'userVersion': data['expectedVersion'], 'name': data['name']})
            return 200, {**self._profile(actor), 'replayed': result['replayed'], 'receipt': result['receipt']}, {}
        if method != 'POST' or not known:
            raise ApiError(405, '계정 요청은 정해진 완료·요청 경로로 제출해주세요.')
        if path == RECOVERY_REQUEST:
            self._limit(h, 'recovery-request', 20, 900)
            return 202, self.recovery.submit(h.body(strict=True)), {}
        if path in INSPECTIONS:
            self._limit(h, 'identity-inspect', 30, 900)
            data = h.body(strict=True)
            require(set(data) == {'token'} and isinstance(data['token'], str) and 1 <= len(data['token']) <= 128)
            self.app.limiter.check(('identity-token', data['token']), 10, 900, busy_timeout_ms=2000)
            return 200, self.views.inspect(data['token'], INSPECTIONS[path], h.user), {}
        if path in REQUESTS:
            self._limit(h, 'identity-issue', 10, 60, actor['id'])
            purpose = REQUESTS[path]
            data = h.body(strict=True)
            expected = {'requestKey', 'identityVersion'} | ({'candidateEmail', 'currentPassword'} if purpose == 'change_email' else set())
            require(set(data) == expected)
            result = self.identity.issue(actor, {**data, 'purpose': purpose})
            challenge = result['challenge']
            return 202, {'request': {'accepted': True, 'purpose': purpose, 'replayed': result['replayed']},
                         'identity': result['current'],
                         'challenge': {k: challenge[k] for k in ('state', 'expiresAt', 'expired')},
                         'mailDeliveryConfirmed': False}, {}
        purpose = COMPLETIONS[path]
        self._limit(h, 'identity-complete', 30, 900)
        data = h.body(strict=True)
        expected = {'requestKey', 'token'} | ({'newPassword'} if purpose == 'reset_password' else set())
        require(set(data) == expected and isinstance(data['token'], str) and 1 <= len(data['token']) <= 128)
        self.app.limiter.check(('identity-token', data['token']), 10, 900, busy_timeout_ms=2000)
        intent = {'requestKey': data['requestKey']}
        if purpose == 'reset_password':
            intent['newPassword'] = data['newPassword']
        result = self.identity.consume(data['token'], purpose, intent, actor=h.user)
        revoked = purpose != 'verify_email'
        response = {'completed': True, 'purpose': purpose, 'replayed': result['replayed'],
                    'sessionsRevoked': revoked, 'requiresLogin': revoked, 'mailDeliveryConfirmed': False}
        # Sessions are revoked in the credential transaction. A late response
        # must never delete a cookie established by a newer login in another tab.
        return 200, response, {}
