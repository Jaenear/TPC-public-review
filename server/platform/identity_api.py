"""Pure self/admin foundation descriptors. Public mutations remain closed."""
from urllib.parse import parse_qs, urlsplit
from ..core import ApiError
from ..identity_contract import IdentityError


class IdentityContractAPI:
    def __init__(self, service):
        self.service = service

    def route(self, h, path, method, query):
        admin = path == '/api/manage/identity' or path.startswith('/api/manage/identity/')
        own = path == '/api/account/identity' or path.startswith('/api/account/identity/')
        if not admin and not own:
            return None
        with self.service.app.db.connect() as c:
            self.service._actor(c, h.user, admin)
        query = parse_qs(urlsplit(h.path).query, keep_blank_values=True, max_num_fields=100)
        if query:
            raise IdentityError(400, 'invalid_input')
        if method == 'GET' and path == ('/api/manage/identity/contract' if admin else '/api/account/identity/contract'):
            return 200, self.service.contract(h.user, admin), {}
        raise ApiError(405, '계정 기반 상태는 조회로만 제공됩니다. 인증·복구 쓰기 API는 별도 개발 단계입니다.')
