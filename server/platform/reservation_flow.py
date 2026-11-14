"""Reservation transitions; payment facts enter only through a trusted resolver."""
from datetime import timedelta
import hashlib
import re

from ..core import ApiError, new_id, text_field
from .coaching_contract import active, shape, digest, encode, instant, stamp
from .reservation_contract import CHOICE_FIELDS, expected, decoded, deadline
from .reservation_model import insert, update


class ReservationFlow:
    def __init__(self,model):self.m=model

    def session(self,c,reservation,data):
        ordinal=data.get('sessionOrdinal')
        if type(ordinal) is not int:raise ApiError(400,'회차 번호를 확인해주세요.')
        row=c.execute('SELECT * FROM booking_sessions WHERE reservation_id=? AND ordinal=?',(reservation['id'],ordinal)).fetchone()
        if not row:raise ApiError(404,'이 예약의 회차가 아닙니다.')
        expected(data,row,'sessionVersion');return row

    def current(self,c,reservation,session,at):
        member=c.execute("SELECT * FROM users WHERE id=? AND status='active'",(reservation['member_id'],)).fetchone()
        if not member:raise ApiError(409,'회원의 현재 상태를 먼저 확인해주세요.')
        occ=self.m.row(c,'booking_occupancies',session['occupancy_id']);hold=self.m.row(c,'slot_holds',occ['hold_id']);quote=decoded(hold);p=quote['candidateProduct']
        self.m.candidate(c,reservation['product_id'],{'productVersion':p['version'],'productRevisionId':p['revisionId'],'calendarVersion':p['availability']['calendarVersion'],'calendarRevisionId':p['availability']['calendarRevisionId'],'slotId':session['slot_id'],'slotVersion':1},at)
        slot=self.m.row(c,'coaching_slots',session['slot_id'])
        valid_until=reservation['valid_until'] or stamp(instant(at)+timedelta(days=decoded(reservation)['product']['sessionValidityDays']))
        if at>=valid_until or slot['ends_at']>valid_until:raise ApiError(409,'회차 유효 기간 안에서 끝나는 일정을 확인해주세요.')
        profile=c.execute('SELECT * FROM coach_profiles WHERE id=?',(reservation['coach_id'],)).fetchone()
        if not profile or profile['owner_user_id']!=reservation['owner_id']:raise ApiError(409,'담당자 기준이 바뀌었습니다.')
        if not occ['active'] or occ['expires_at'] is not None and occ['expires_at']<=at or occ['occupied_from']<at:raise ApiError(409,'현재 점유/준비 기한이 지났습니다.')
        return occ

    def close_in(self,c,reservation,at,values):
        for occ in c.execute('SELECT * FROM booking_occupancies WHERE reservation_id=? AND active=1',(reservation['id'],)).fetchall():update(c,'booking_occupancies',occ,{'active':0,'checked_at':at})
        for session in c.execute("SELECT * FROM booking_sessions WHERE reservation_id=? AND state IN ('requested','confirmed')",(reservation['id'],)).fetchall():update(c,'booking_sessions',session,{'state':'cancelled','updated_at':at})
        return update(c,'coaching_reservations',reservation,{**values,'deadline_at':None,'updated_at':at})

    def confirm_in(self,c,reservation,session,occ,at,values=None):
        update(c,'booking_occupancies',occ,{'kind':'confirmed','expires_at':None,'checked_at':at})
        session=update(c,'booking_sessions',session,{'state':'confirmed','updated_at':at})
        quote=decoded(reservation);confirmed=reservation['confirmed_at'] or at;valid=reservation['valid_until'] or stamp(instant(confirmed)+timedelta(days=quote['product']['sessionValidityDays']))
        reservation=update(c,'coaching_reservations',reservation,{**(values or {}),'state':'confirmed','deadline_at':None,'coach_accepted_at':reservation['coach_accepted_at'] or at,'confirmed_at':confirmed,'valid_until':valid,'updated_at':at})
        return reservation,session

    def respond(self,user,identifier,data):
        required={'version','sessionOrdinal','sessionVersion','action','reason','requestKey'};shape(data,required,required)
        if data['action'] not in ('accept','reject'):raise ApiError(400,'코치 확인 종류를 확인해주세요.')
        def change(c,actor,at,reason):
            r=self.m.load_reservation(c,actor,identifier);expected(data,r);self.m.operator(c,actor,r);s=self.session(c,r,data)
            if s['state']!='requested' or r['state'] not in ('awaiting_coach','confirmed'):raise ApiError(409,'현재 코치 확인을 기다리는 회차가 아닙니다.')
            if data['action']=='reject':
                if r['state']=='confirmed':
                    o=self.m.row(c,'booking_occupancies',s['occupancy_id']);update(c,'booking_occupancies',o,{'active':0,'checked_at':at});s=update(c,'booking_sessions',s,{'state':'unscheduled','slot_id':None,'occupancy_id':None,'updated_at':at});r=update(c,'coaching_reservations',r,{'updated_at':at})
                else:r=self.close_in(c,r,at,{'state':'cancelled','payment_state':'refund_pending' if r['payment_state']=='paid' else r['payment_state']})
            else:
                o=self.current(c,r,s,at)
                if r['state']=='confirmed' or r['payment_state'] in ('paid','not_required'):r,s=self.confirm_in(c,r,s,o,at)
                else:
                    end=deadline(at,decoded(r)['policy']['paymentSeconds'],o['occupied_from']);update(c,'booking_occupancies',o,{'expires_at':end,'checked_at':at});r=update(c,'coaching_reservations',r,{'state':'awaiting_payment','coach_accepted_at':at,'deadline_at':end,'updated_at':at})
            self.m.event(c,actor,'coach.'+data['action'],reason,at,reservation=r,session=s)
            return 'reservation',identifier,{'id':identifier,'version':r['version'],'state':r['state'],'paymentState':r['payment_state'],'sessionId':s['id']}
        return self.m.command(user,'reservation.respond:'+identifier,data,change)

    def perform(self,user,identifier,data):
        required={'version','sessionOrdinal','sessionVersion','action','reason','requestKey'};shape(data,required,required)
        if data['action'] not in ('complete','no_show'):raise ApiError(400,'수행/노쇼 종류를 확인해주세요.')
        def change(c,actor,at,reason):
            r=self.m.load_reservation(c,actor,identifier);expected(data,r);self.m.operator(c,actor,r);s=self.session(c,r,data)
            if r['state']!='confirmed' or s['state']!='confirmed':raise ApiError(409,'확정된 회차에서 수행을 확인해주세요.')
            slot=self.m.row(c,'coaching_slots',s['slot_id'])
            if slot['ends_at']>at:raise ApiError(409,'예정 종료 뒤 실제 수행/불참을 확인해주세요.')
            o=self.m.row(c,'booking_occupancies',s['occupancy_id']);update(c,'booking_occupancies',o,{'active':0,'checked_at':at});s=update(c,'booking_sessions',s,{'state':'completed' if data['action']=='complete' else 'no_show','performed_at':at,'updated_at':at})
            remaining=c.execute("SELECT 1 FROM booking_sessions WHERE reservation_id=? AND state!='completed' AND NOT(state='no_show' AND ?=1) LIMIT 1",(identifier,r['no_show_consumes'])).fetchone()
            r=update(c,'coaching_reservations',r,{'state':'confirmed' if remaining else 'completed','updated_at':at})
            self.m.event(c,actor,'session.'+data['action'],reason,at,reservation=r,session=s,details={'ordinal':s['ordinal'],'slotId':s['slot_id'],'occupancyId':o['id'],'performedAt':at,'consumed':s['state']=='completed' or bool(r['no_show_consumes'])})
            return 'reservation',identifier,{'id':identifier,'version':r['version'],'state':r['state'],'sessionId':s['id'],'sessionState':s['state']}
        return self.m.command(user,'reservation.perform:'+identifier,data,change)

    def cancel(self,user,identifier,data):
        required={'version','reason','requestKey'};shape(data,required|{'cause'},required)
        if data.get('cause','member_cancel') not in ('member_cancel','coach_absence','operator_cancel'):raise ApiError(400,'취소 원인을 확인해주세요.')
        def change(c,actor,at,reason):
            r=self.m.load_reservation(c,actor,identifier);expected(data,r)
            if r['state'] not in ('awaiting_coach','awaiting_payment','confirmed','expired','exception'):raise ApiError(409,'현재 예약은 새 취소 대상이 아닙니다.')
            op=actor['role']=='admin' or actor['id']!=r['member_id']
            if op:self.m.operator(c,actor,r)
            elif data.get('cause','member_cancel')!='member_cancel':raise ApiError(403,'코치/운영 처리 사유는 담당자만 확인할 수 있습니다.')
            policy=decoded(r)['policy']
            if not op and r['state']=='confirmed':
                for s in c.execute("SELECT s.* FROM booking_sessions s WHERE s.reservation_id=? AND s.state='confirmed'",(identifier,)).fetchall():
                    slot=self.m.row(c,'coaching_slots',s['slot_id'])
                    if stamp(instant(at)+timedelta(hours=policy['cancelBeforeHours']))>=slot['starts_at']:raise ApiError(409,'현재 취소 기한은 운영 검토가 필요합니다.')
            refund=r['payment_state']=='paid' and (policy['refundOnCancellation'] or data.get('cause')=='coach_absence' or r['state'] in ('awaiting_coach','awaiting_payment','expired','exception'))
            r=self.close_in(c,r,at,{'state':'cancelled','payment_state':'refund_pending' if refund else r['payment_state']})
            self.m.event(c,actor,'reservation.cancelled',reason,at,reservation=r,details={'cause':data.get('cause','member_cancel'),'refundRequired':refund})
            return 'reservation',identifier,{'id':identifier,'version':r['version'],'state':r['state'],'paymentState':r['payment_state'],'refundExecuted':False}
        return self.m.command(user,'reservation.cancel:'+identifier,data,change)

    def reschedule(self,user,identifier,data):
        required={'version','sessionOrdinal','sessionVersion','productId','policyId','reason','requestKey'}|CHOICE_FIELDS;shape(data,required,required)
        def change(c,actor,at,reason):
            r=self.m.load_reservation(c,actor,identifier);expected(data,r);s=self.session(c,r,data);policy=decoded(r)['policy']
            op=actor['role']=='admin' or actor['id']!=r['member_id']
            if op:self.m.operator(c,actor,r)
            elif policy['rescheduleRequiresCoach']:raise ApiError(403,'이 정책의 일정 변경은 코치 확인이 필요합니다.')
            if r['state']!='confirmed' or s['state']!='confirmed':raise ApiError(409,'확정된 유효 회차에서 일정을 변경해주세요.')
            old=self.m.row(c,'booking_occupancies',s['occupancy_id']);slot=self.m.row(c,'coaching_slots',s['slot_id'])
            if old['occupied_from']<=at or stamp(instant(at)+timedelta(hours=policy['rescheduleBeforeHours']))>=slot['starts_at']:raise ApiError(409,'기존 준비/변경 기한이 지난 회차입니다.')
            if data['slotId']==s['slot_id']:raise ApiError(400,'다른 후보를 선택해주세요.')
            update(c,'booking_occupancies',old,{'active':0,'checked_at':at})
            hold,occ=self.m.make_hold(c,actor,data,at,parent=r,ordinal=s['ordinal'],as_operator=op)
            s=update(c,'booking_sessions',s,{'slot_id':hold['slot_id'],'occupancy_id':occ['id'],'updated_at':at})
            update(c,'booking_occupancies',occ,{'kind':'confirmed','expires_at':None,'reservation_id':r['id'],'session_id':s['id'],'checked_at':at});hold=update(c,'slot_holds',hold,{'state':'consumed','updated_at':at});r=update(c,'coaching_reservations',r,{'updated_at':at})
            rid=new_id('bookingreschedule');insert(c,'booking_reschedules',{'id':rid,'reservation_id':r['id'],'session_id':s['id'],'from_slot_id':slot['id'],'to_slot_id':s['slot_id'],'from_occupancy_id':old['id'],'to_occupancy_id':occ['id'],'target_hold_id':hold['id'],'actor_id':actor['id'],'reason':reason,'reservation_version':r['version'],'created_at':at})
            self.m.event(c,actor,'session.rescheduled',reason,at,reservation=r,hold=hold,session=s,details={'rescheduleId':rid,'fromSlotId':slot['id'],'toSlotId':s['slot_id']})
            return 'reservation',identifier,{'id':identifier,'version':r['version'],'sessionId':s['id'],'rescheduleId':rid,'slotId':s['slot_id']}
        return self.m.command(user,'reservation.reschedule:'+identifier,data,change)

    def observe_payment(self,identifier,reference,kind):
        # Resolver contract: verified local ledger facts only. Do not perform
        # gateway/network I/O while this model owns the SQLite write transaction.
        source=self.m.payment_source
        if not source or not callable(getattr(source,'lookup',None)) or not re.fullmatch(r'[A-Za-z0-9_-]{3,80}',str(getattr(source,'identity',''))):raise ApiError(503,'신뢰한 예약 결제 근거 제공자가 연결되지 않았습니다.')
        if kind not in ('paid','refunded') or not isinstance(reference,str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,160}',reference):raise ApiError(400,'서버의 검증된 결제 사건 참조를 확인해주세요.')
        key='payment-'+hashlib.sha256(encode({'reservationId':identifier,'reference':reference,'kind':kind,'authority':source.identity}).encode()).hexdigest()
        data={'requestKey':key,'reason':'서버 결제 근거 확인','reservationId':identifier,'reference':reference,'kind':kind}
        def change(c,actor,at,reason):
            r=self.m.row(c,'coaching_reservations',identifier);proof=source.lookup(c,reference)
            required={'reservationId','memberId','productId','orderReference','amount','currency','mode','authority','eventId','kind'}
            if not isinstance(proof,dict) or set(proof)!=required or proof['reservationId']!=identifier or proof['memberId']!=r['member_id'] or proof['productId']!=r['product_id'] or proof['orderReference']!=r['payment_order_ref'] or type(proof['amount']) is not int or proof['amount']!=r['price'] or proof['amount']<=0 or proof['currency']!='KRW' or proof['mode']!=('synthetic' if r['scope']=='technical' else 'live') or proof['authority']!=source.identity or proof['kind']!=kind or not isinstance(proof['eventId'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,160}',proof['eventId']):raise ApiError(409,'결제 사실의 예약/회원/상품/주문·금액/모드 근거가 다릅니다.')
            hashed=digest(proof);old=c.execute('SELECT * FROM booking_payment_facts WHERE authority=? AND source_event_id=?',(source.identity,proof['eventId'])).fetchone()
            if old:
                if old['reservation_id']!=identifier or old['source_hash']!=hashed:raise ApiError(409,'다른 원 결제 사실과 충돌합니다.')
                return 'reservation',identifier,{'id':identifier,'version':r['version'],'paymentFactId':old['id'],'factAlreadyRecorded':True}
            if c.execute('SELECT 1 FROM booking_payment_facts WHERE reservation_id=? AND kind=?',(identifier,kind)).fetchone():raise ApiError(409,'이미 기록한 정규 결제 사실의 원 사건을 확인해주세요.')
            paid=c.execute("SELECT * FROM booking_payment_facts WHERE reservation_id=? AND kind='paid'",(identifier,)).fetchone()
            if kind=='refunded' and not paid:raise ApiError(409,'원 결제 근거가 없는 환불 완료는 기록할 수 없습니다.')
            fid=new_id('bookingpaymentfact');insert(c,'booking_payment_facts',{'id':fid,'reservation_id':identifier,'kind':kind,'amount':proof['amount'],'currency':'KRW','mode':proof['mode'],'authority':source.identity,'source_event_id':proof['eventId'],'source_order_id':proof['orderReference'],'source_hash':hashed,'paid_fact_id':paid['id'] if kind=='refunded' else None,'observed_at':at})
            if kind=='refunded':
                r=self.close_in(c,r,at,{'state':'cancelled' if r['state']=='cancelled' else 'exception','payment_state':'refunded','payment_fact_id':fid})
            else:
                s=c.execute('SELECT * FROM booking_sessions WHERE reservation_id=? AND ordinal=1',(identifier,)).fetchone();valid=False
                try:
                    if r['state'] not in ('awaiting_payment','awaiting_coach') or not r['deadline_at'] or r['deadline_at']<=at or s['state']!='requested':raise ApiError(409,'예약 진행 기한이 지났습니다.')
                    if r['sequence']=='coach_then_payment' and not r['coach_accepted_at']:raise ApiError(409,'코치 확인 이전의 결제입니다.')
                    o=self.current(c,r,s,at);valid=True
                except ApiError:pass
                if not valid:r=self.close_in(c,r,at,{'state':'exception','payment_state':'paid','payment_fact_id':fid})
                elif r['coach_accepted_at']:r,s=self.confirm_in(c,r,s,o,at,{'payment_state':'paid','payment_fact_id':fid})
                else:
                    end=deadline(at,decoded(r)['policy']['coachSeconds'],o['occupied_from']);update(c,'booking_occupancies',o,{'expires_at':end,'checked_at':at});r=update(c,'coaching_reservations',r,{'payment_state':'paid','payment_fact_id':fid,'state':'awaiting_coach','deadline_at':end,'updated_at':at})
            self.m.event(c,None,'payment.'+kind,reason,at,reservation=r,details={'paymentFactId':fid,'amount':proof['amount'],'mode':proof['mode'],'needsReview':r['state']=='exception'})
            return 'reservation',identifier,{'id':identifier,'version':r['version'],'state':r['state'],'paymentState':r['payment_state'],'paymentFactId':fid,'realGatewayCalled':False}
        return self.m.command(None,'payment.'+kind,data,change,system_scope='payment:'+source.identity)

    def expire_one(self,c,o,at,actor=None,reason=None):
        hold=self.m.row(c,'slot_holds',o['hold_id'])
        if o['kind']=='hold':
            update(c,'booking_occupancies',o,{'active':0,'checked_at':at});hold=update(c,'slot_holds',hold,{'state':'expired','updated_at':at});self.m.event(c,actor,'hold.expired',reason or '서버 점유 기한 만료',at,hold=hold)
        else:
            r=self.m.row(c,'coaching_reservations',o['reservation_id']);s=self.m.row(c,'booking_sessions',o['session_id'])
            if r['state']=='confirmed':
                update(c,'booking_occupancies',o,{'active':0,'checked_at':at});s=update(c,'booking_sessions',s,{'state':'unscheduled','slot_id':None,'occupancy_id':None,'updated_at':at});r=update(c,'coaching_reservations',r,{'updated_at':at});action='session.request_expired'
            else:r=self.close_in(c,r,at,{'state':'exception' if r['payment_state']=='paid' else 'expired'});action='reservation.expired'
            self.m.event(c,actor,action,reason or '서버 진행 기한 만료',at,reservation=r,hold=hold,session=s,details={'slotId':o['slot_id'],'occupancyId':o['id']})

    def expire_target(self,user,kind,identifier,data):
        shape(data,{'version','reason','requestKey'},{'version','reason','requestKey'})
        if kind not in ('hold','reservation'):raise ApiError(400,'명시 만료 대상을 확인해주세요.')
        def change(c,actor,at,reason):
            if actor['role']!='admin':raise ApiError(403,'명시 만료는 현재 관리자만 처리할 수 있습니다.')
            row=self.m.load_hold(c,actor,identifier) if kind=='hold' else self.m.load_reservation(c,actor,identifier);expected(data,row)
            clause="hold_id=? AND kind='hold'" if kind=='hold' else "reservation_id=? AND kind='request'"
            rows=c.execute('SELECT * FROM booking_occupancies WHERE '+clause+' AND active=1 AND expires_at<=? ORDER BY expires_at,id',(identifier,at)).fetchall()
            if not rows:raise ApiError(409,'현재 만료 처리할 미확정 점유가 없습니다.')
            processed=[]
            for original in rows:
                current=self.m.row(c,'booking_occupancies',original['id'])
                if current['active']:self.expire_one(c,current,at,actor,reason);processed.append(current['id'])
            table='slot_holds' if kind=='hold' else 'coaching_reservations';row=self.m.row(c,table,identifier)
            return kind,identifier,{'id':identifier,'version':row['version'],'state':row['state'],'processedOccupancyIds':processed,'realGatewayCalled':False}
        return self.m.command(user,kind+'.expire:'+identifier,data,change)

    def expire(self,limit):
        if type(limit) is not int or not 1<=limit<=500:raise ApiError(400,'만료 처리 범위는1–500개입니다.')
        processed=[]
        with self.m.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE');self.m.require_schema(c);at=self.m.at()
            rows=c.execute('SELECT * FROM booking_occupancies WHERE active=1 AND expires_at IS NOT NULL AND expires_at<=? ORDER BY expires_at,id LIMIT ?',(at,limit)).fetchall()
            for original in rows:
                o=self.m.row(c,'booking_occupancies',original['id'])
                if not o['active']:continue
                self.expire_one(c,o,at)
                processed.append(o['id'])
        return {'processedOccupancyIds':processed,'count':len(processed),'checkedAt':at,'realGatewayCalled':False}
