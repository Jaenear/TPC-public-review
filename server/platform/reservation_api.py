"""Authenticated technical reservation HTTP contracts; commercial PG stays gated."""
import re
from urllib.parse import parse_qs,urlsplit

from ..core import ApiError
from .coaching_contract import active,shape,encode,bounded_query
from .reservation_queries import ReservationQueries
from .reservation_scheduling import ReservationScheduling

HOLD=r'(slothold_[a-f0-9]{32})'
RESERVATION=r'(coachingreservation_[a-f0-9]{32})'
PRODUCT=r'(cproduct_[a-f0-9]{32})'
POLICY=r'(reservationpolicy_[a-f0-9]{32})'


class ReservationAPI:
    def __init__(self,app,model):self.app=app;self.m=model;self.reads=ReservationQueries(model);self.scheduling=ReservationScheduling(model)

    def response(self,value):
        return {**value,'scope':{**self.m.scope(),'kind':'reservation_api','publicWriteAPIEnabled':True,'technicalOnly':True,'commercialBookingsEnabled':False,'technicalWriteScope':'technical','memberBindingHeader':'X-TPC-User','writeScopeHeader':'X-TPC-Reservation-Scope'}}

    def write(self,h,action,*args,admin=False,coach=False,prepare=None,create=False):
        # Scope acknowledges the current technical-only contract; it cannot
        # configure a gateway, approve a commercial policy or assert paid.
        with self.app.db.connect() as c:
            actor=active(c,h.user,coach)
            if admin and actor['role']!='admin':raise ApiError(403,'현재 관리자 권한이 필요합니다.')
        if h.headers.get('X-TPC-User')!=actor['id']:raise ApiError(401,'예약 요청의 현재 계정을 다시 확인해주세요.')
        if h.headers.get('X-TPC-Reservation-Scope')!='technical':raise ApiError(409,'현재 예약 API는 명시한 기술 검수 범위에서만 사용할 수 있습니다.')
        data=h.body(strict=True)
        try:encode(data).encode('utf-8')
        except (ValueError,TypeError,UnicodeError):raise ApiError(400,'유효한 JSON 값과 문자열을 입력해주세요.')
        if prepare:data=prepare(data)
        self.app.limiter.check(('coaching.reservation',actor['id']),60,60)
        result=action(actor,*args,data)
        return (201 if create and not result.get('replayed') else 200),self.response(result),{}

    def route(self,h,path,method,query):
        policy_path=re.fullmatch('/api/coaching/products/'+PRODUCT+'/reservation-policies',path)
        if not policy_path and not path.startswith(('/api/coaching/holds','/api/coaching/reservations','/api/coaching/reservation-commands','/api/studio/coaching/reservations','/api/manage/coaching/')):return None
        # Preserve blank members here so duplicate/empty filters cannot turn
        # into a different, broader query. The dispatch field limit is shared.
        query=parse_qs(urlsplit(h.path).query,keep_blank_values=True,max_num_fields=100)
        if method=='GET':
            match=re.fullmatch('/api/coaching/reservations/'+RESERVATION+'/scheduling-candidates',path)
            if match:return 200,self.response(self.scheduling.candidates(h.user,match[1],query)),{}
            if policy_path:return 200,self.response(self.reads.policies(h.user,policy_path[1],query)),{}
            if path in ('/api/coaching/holds','/api/coaching/reservations','/api/studio/coaching/reservations','/api/manage/coaching/holds'):
                kind='hold' if path.endswith('/holds') else 'reservation'
                return 200,self.response(self.reads.listing(h.user,kind,query,operator=path.startswith('/api/studio/'),admin=path.startswith('/api/manage/'))),{}
            match=re.fullmatch('/api/coaching/reservations/'+RESERVATION+'/events',path)
            if match:return 200,self.response(self.reads.events(h.user,match[1],query)),{}
            bounded_query(query,set())
            match=re.fullmatch('/api/coaching/(?:holds/'+HOLD+'|reservations/'+RESERVATION+')',path)
            if match:return 200,self.response(self.m.get(h.user,'hold' if match[1] else 'reservation',match[1] or match[2])),{}
            match=re.fullmatch(r'/api/coaching/reservation-commands/([A-Za-z0-9_-]{8,128})',path)
            if match:return 200,self.response(self.m.receipt(h.user,match[1])),{}
            match=re.fullmatch('/api/manage/coaching/reservation-policies/'+POLICY,path)
            if match:return 200,self.response(self.m.get(h.user,'policy',match[1])),{}
        if method=='POST':
            bounded_query(query,set())
            if path=='/api/coaching/holds':return self.write(h,self.m.hold,create=True)
            match=re.fullmatch('/api/coaching/holds/'+HOLD+'/(release|request)',path)
            if match:return self.write(h,getattr(self.m,match[2]),match[1])
            match=re.fullmatch('/api/coaching/reservations/'+RESERVATION+'/(cancel|reschedule)',path)
            if match:return self.write(h,getattr(self.m,match[2]),match[1])
            match=re.fullmatch('/api/studio/coaching/reservations/'+RESERVATION+'/(respond|perform)',path)
            if match:return self.write(h,getattr(self.m,match[2]),match[1],coach=True)
            match=re.fullmatch('/api/manage/coaching/(?:holds/'+HOLD+'|reservations/'+RESERVATION+')/expire',path)
            if match:return self.write(h,self.m.expire_target,'hold' if match[1] else 'reservation',match[1] or match[2],admin=True)
            match=re.fullmatch('/api/manage/coaching/products/'+PRODUCT+'/reservation-policies',path)
            if match:
                def prepare(data):
                    fields={'productVersion','payload','approvalReference','reason','requestKey'};shape(data,fields,fields);return {**data,'productId':match[1]}
                return self.write(h,self.m.publish_policy,admin=True,prepare=prepare,create=True)
        raise ApiError(405,'지원하는 예약 경로와 요청 방식을 확인해주세요.')
