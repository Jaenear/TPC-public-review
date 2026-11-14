"""Private review API; legacy request, member and admin routes remain compatible."""
import re
from urllib.parse import parse_qs, urlsplit

from ..core import ApiError
from .hand_review_contract import active, bounded_query
from .common import roles

REVIEW = r'([a-z0-9_]{1,100})'
ASSET = r'(hrattachment_[a-f0-9]{32})'


class HandReviewAPI:
    def __init__(self, app, model): self.app=app; self.m=model

    def write_actor(self, h):
        with self.app.db.connect() as c: actor=active(c,h.user)
        if len(h.headers.get_all('X-TPC-User',[]))!=1 or h.headers.get('X-TPC-User')!=actor['id']:
            raise ApiError(401,'리뷰 요청의 현재 계정을 다시 확인해주세요.')
        self.app.limiter.check(('hand-review',actor['id']),60,60)
        return actor

    def route(self, h, path, method, query):
        if not path.startswith(('/api/hand-reviews','/api/hand-review-commands','/api/studio/hand-reviews','/api/manage/hand-reviews')): return None
        q=parse_qs(urlsplit(h.path).query,keep_blank_values=True,max_num_fields=100)
        if method=='GET':
            queues={'/api/hand-reviews':'member','/api/studio/hand-reviews':'studio','/api/manage/hand-reviews':'manage'}
            if path in queues: return 200,self.m.reads.listing(h.user,queues[path],q),{}
            match=re.fullmatch('/api/hand-reviews/'+REVIEW+'/(revisions|events)',path)
            if match: return 200,self.m.reads.history(h.user,match[1],match[2],q),{}
            match=re.fullmatch('/api/hand-reviews/'+REVIEW+'/attachments',path)
            if match: return 200,self.m.attachments.listing(h.user,match[1],q),{}
            bounded_query(q,set())
            match=re.fullmatch('/api/hand-reviews/'+REVIEW+'/attachments/'+ASSET+'(?:/(file))?',path)
            if match:
                if match[3]:
                    from .router import HANDLED
                    self.m.attachments.file(h,h.user,match[1],match[2]);return HANDLED
                return 200,self.m.attachments.detail(h.user,match[1],match[2]),{}
            match=re.fullmatch('/api/hand-reviews/'+REVIEW,path)
            if match:return 200,self.m.reads.detail(h.user,match[1]),{}
            match=re.fullmatch(r'/api/hand-review-commands/([A-Za-z0-9_-]{8,128})',path)
            if match:return 200,self.m.receipt(h.user,match[1]),{}
        if method=='POST':
            bounded_query(q,set());actor=self.write_actor(h)
            match=re.fullmatch('/api/hand-reviews/'+REVIEW+'/attachments',path)
            if match:
                result=self.m.attachments.upload(h,actor,match[1]);return (200 if result['replayed'] else 201),result,{}
            data=h.body(strict=True)
            if path=='/api/hand-reviews':
                result=self.m.create(actor,data);return (200 if result['replayed'] else 201),result,{}
            match=re.fullmatch('/api/hand-reviews/'+REVIEW+'/(adopt|revise|cancel)',path)
            if match:
                result=self.m.adopt(actor,match[1],data) if match[2]=='adopt' else self.m.change(actor,match[1],match[2],data)
                return 200,result,{}
            match=re.fullmatch('/api/(?:studio|manage)/hand-reviews/'+REVIEW+'/(assign|unassign|start|request-information|answer|cancel)',path)
            if match:
                with self.app.db.connect() as c:
                    actor=active(c,actor)
                    if path.startswith('/api/manage/') and actor['role']!='admin' or actor['role']!='admin' and 'coach' not in roles(c,actor):
                        raise ApiError(403,'현재 운영자 또는 담당 코치 권한을 확인해주세요.')
                return 200,self.m.change(actor,match[1],match[2],data),{}
            match=re.fullmatch('/api/hand-reviews/'+REVIEW+'/attachments/'+ASSET+'/(inspect|block|delete|cancel|expire)',path)
            if match:return 200,self.m.attachments.operate(actor,match[1],match[2],match[3],data),{}
        raise ApiError(405,'지원하는 리뷰 경로와 요청 방식을 확인해주세요.')
