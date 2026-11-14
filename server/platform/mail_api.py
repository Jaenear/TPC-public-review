"""Admin-only read contract. Dispatch, feedback and identity HTTP are later work."""
from urllib.parse import parse_qs, urlsplit

from ..core import ApiError
from ..mail_contract import MailError


class MailContractAPI:
    def __init__(self, service):
        self.service = service

    def route(self, h, path, method, query):
        if path != '/api/manage/mail' and not path.startswith('/api/manage/mail/'):
            return None
        query = parse_qs(urlsplit(h.path).query, keep_blank_values=True, max_num_fields=100)
        with self.service.app.db.connect() as c:
            self.service._admin(c, h.user)
        if query:
            raise MailError(400, 'invalid_input')
        if path == '/api/manage/mail/contract' and method == 'GET':
            return 200, self.service.contract(h.user), {}
        raise ApiError(405, '메일 기반 상태는 조회로만 제공됩니다. 전달·반송 API는 별도 개발 단계입니다.')
