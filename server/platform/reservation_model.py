"""Atomic reservation domain. Public write APIs/merchant activation are separate work."""
from datetime import timedelta
import json
import sqlite3

from ..core import ApiError, new_id, now_iso, text_field
from .common import audit, roles
from .coaching_contract import active, shape, digest, encode, instant, stamp, zone
from .reservation_capabilities import hold_controls, reservation_controls
from .reservation_contract import CHOICE_FIELDS, policy_payload, request_key, expected, decoded, deadline, occupied, future_context, effective_state


def insert(c,table,values):
    names=list(values)
    c.execute('INSERT INTO '+table+'('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',[values[k] for k in names])


def update(c,table,row,values):
    names=list(values)
    changed=c.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in names)+',version=version+1 WHERE id=? AND version=?',[values[k] for k in names]+[row['id'],row['version']])
    if changed.rowcount!=1:raise ApiError(409,'다른 예약 변경이 있습니다. 현재 원문을 다시 확인해주세요.')
    return c.execute('SELECT * FROM '+table+' WHERE id=?',(row['id'],)).fetchone()


class ReservationModel:
    def __init__(self,app,coaching,*,clock=None,payment_source=None):
        self.app=app;self.coaching=coaching;self.clock=clock or (lambda:now_iso());self.payment_source=payment_source
        from .reservation_flow import ReservationFlow
        self.flow=ReservationFlow(self)

    def at(self):return stamp(instant(self.clock()))

    def require_schema(self,c):
        if c.execute('PRAGMA user_version').fetchone()[0]<29:raise ApiError(409,'예약 모델의 추가 이관이 필요합니다.')

    def row(self,c,table,identifier):
        row=c.execute('SELECT * FROM '+table+' WHERE id=?',(identifier,)).fetchone()
        if not row:raise ApiError(404,'접근 가능한 예약 원문이 없습니다.')
        return row

    def access(self,c,actor,row,kind):
        if actor['role']=='admin' or row['member_id']==actor['id']:return
        if kind=='reservation' and 'coach' in roles(c,actor) and row['owner_id']==actor['id']:
            profile=c.execute('SELECT * FROM coach_profiles WHERE id=?',(row['coach_id'],)).fetchone()
            if profile and profile['owner_user_id']==actor['id']:return
        raise ApiError(404,'접근 가능한 본인/담당 예약이 없습니다.')

    def load_reservation(self,c,actor,identifier):
        row=self.row(c,'coaching_reservations',identifier);self.access(c,actor,row,'reservation');return row

    def load_hold(self,c,actor,identifier):
        row=self.row(c,'slot_holds',identifier);self.access(c,actor,row,'hold');return row

    def operator(self,c,actor,row):
        if actor['role']=='admin':return
        profile=c.execute('SELECT * FROM coach_profiles WHERE id=?',(row['coach_id'],)).fetchone()
        if 'coach' not in roles(c,actor) or row['owner_id']!=actor['id'] or not profile or profile['owner_user_id']!=actor['id']:raise ApiError(403,'현재 담당 코치/운영 권한이 필요합니다.')

    def event(self,c,actor,action,reason,at,*,reservation=None,hold=None,session=None,details=None):
        insert(c,'booking_events',{'id':new_id('bookingevent'),'reservation_id':reservation['id'] if reservation else None,'hold_id':hold['id'] if hold else None,'session_id':session['id'] if session else None,'actor_id':actor['id'] if actor else None,'action':action,'version':reservation['version'] if reservation else hold['version'],'reason':reason,'details':encode(details or {}),'created_at':at})

    def command(self,user,operation,data,mutate,*,system_scope=None):
        key=request_key(data);reason=text_field(data,'reason',minimum=3,maximum=1000)
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE');self.require_schema(c)
            actor=None if system_scope else active(c,user);scope=system_scope or 'user:'+actor['id'];at=self.at()
            old=c.execute('SELECT * FROM booking_commands WHERE actor_scope=? AND request_key=?',(scope,key)).fetchone()
            hashed=digest({'operation':operation,'body':data})
            if old:
                if old['command']!=operation or old['request_hash']!=hashed:raise ApiError(409,'다른 본문/작업에 사용한 원 요청 키입니다.')
                current=self.detail_in(c,actor,old['entity_type'],old['entity_id'],at,system=bool(system_scope))
                return {**current,'receipt':self.receipt_view(old),'replayed':True}
            try:kind,identifier,result=mutate(c,actor,at,reason)
            except sqlite3.IntegrityError as e:
                if str(e)=='Coach time range is already occupied':raise ApiError(409,'같은 코치의 시간이 이미 점유되어 있습니다.') from e
                raise
            command_id=new_id('bookingcommand')
            insert(c,'booking_commands',{'id':command_id,'actor_scope':scope,'actor_id':actor['id'] if actor else None,'request_key':key,'command':operation,'request_hash':hashed,'entity_type':kind,'entity_id':identifier,'result':encode(result),'created_at':at})
            audit(c,actor,'reservation.'+operation,kind,identifier,{'commandId':command_id,'result':result})
            current=self.detail_in(c,actor,kind,identifier,at,system=bool(system_scope))
            return {**current,'receipt':self.receipt_view(self.row(c,'booking_commands',command_id)),'replayed':False}

    def receipt_view(self,row):
        return {'id':row['id'],'requestKey':row['request_key'],'command':row['command'],'entityType':row['entity_type'],'entityId':row['entity_id'],'result':json.loads(row['result']),'createdAt':row['created_at']}

    def receipt(self,user,key):
        with self.app.db.connect() as c:
            c.execute('BEGIN');self.require_schema(c);actor=active(c,user)
            original=c.execute('SELECT * FROM booking_commands WHERE actor_scope=? AND request_key=?',('user:'+actor['id'],key)).fetchone()
            if not original:raise ApiError(404,'본인의 원 요청 결과가 없습니다.')
            return {**self.detail_in(c,actor,original['entity_type'],original['entity_id'],self.at()),'receipt':self.receipt_view(original),'replayed':True}

    def detail_in(self,c,actor,kind,identifier,at,*,system=False):
        if kind=='policy':
            row=self.row(c,'reservation_policies',identifier)
            if not system and actor['role']!='admin':raise ApiError(403,'정책 검수 원문은 관리자만 조회할 수 있습니다.')
            return {'policy':{'id':row['id'],'productId':row['product_id'],'productRevisionId':row['product_revision_id'],'productVersion':row['product_version'],'scope':row['scope'],'payload':decoded(row,'payload'),'approvalReference':row['approval_reference'],'createdAt':row['created_at']},'scope':self.scope()}
        if kind=='hold':
            row=self.row(c,'slot_holds',identifier)
            if not system:self.access(c,actor,row,kind)
            occupation=c.execute('SELECT * FROM booking_occupancies WHERE hold_id=?',(identifier,)).fetchone()
            state=effective_state(row,at,'hold')
            parent=self.row(c,'coaching_reservations',row['reservation_id']) if row['reservation_id'] else None
            return {'hold':{'id':row['id'],'memberId':row['member_id'],'coachId':row['coach_id'],'productId':row['product_id'],'slotId':row['slot_id'],'policyId':row['policy_id'],'quote':decoded(row),'state':state,'storedState':row['state'],'version':row['version'],'expiresAt':row['expires_at'],'remainingSeconds':max(0,int((instant(row['expires_at'])-instant(at)).total_seconds())) if state=='active' else 0,'reservationId':occupation['reservation_id'] if occupation else None,'parentReservationId':row['reservation_id'],'parentReservationVersion':parent['version'] if parent else None,'sessionOrdinal':row['session_ordinal'],'createdAt':row['created_at']},'capabilities':hold_controls(self,c,actor,row,at),'scope':self.scope()}
        row=self.row(c,'coaching_reservations',identifier)
        if not system:self.access(c,actor,row,kind)
        sessions=c.execute('SELECT * FROM booking_sessions WHERE reservation_id=? ORDER BY ordinal',(identifier,)).fetchall()
        timeline=c.execute('SELECT * FROM booking_events WHERE reservation_id=? ORDER BY rowid DESC LIMIT 100',(identifier,)).fetchall()
        facts=c.execute('SELECT * FROM booking_payment_facts WHERE reservation_id=? ORDER BY rowid',(identifier,)).fetchall()
        effective=effective_state(row,at)
        return {'reservation':{'id':row['id'],'memberId':row['member_id'],'coachId':row['coach_id'],'productId':row['product_id'],'policyId':row['policy_id'],'legacyInquiryId':row['legacy_inquiry_id'],'quote':decoded(row),'state':effective,'storedState':row['state'],'paymentState':row['payment_state'],'paymentOrderReference':row['payment_order_ref'],'sequence':row['sequence'],'version':row['version'],'deadlineAt':row['deadline_at'],'confirmedAt':row['confirmed_at'],'validUntil':row['valid_until'],'schedulingExpired':bool(row['valid_until'] and row['valid_until']<=at),'sessionCount':row['session_count'],'consumedSessionCount':sum(s['state']=='completed' or s['state']=='no_show' and row['no_show_consumes'] for s in sessions),'sessions':[self.session_view(c,s,at) for s in sessions],'paymentFacts':[{'id':f['id'],'kind':f['kind'],'amount':f['amount'],'currency':f['currency'],'mode':f['mode'],'observedAt':f['observed_at']} for f in facts],'createdAt':row['created_at'],'updatedAt':row['updated_at']},'events':[{'id':e['id'],'action':e['action'],'version':e['version'],'sessionId':e['session_id'],'reason':e['reason'],'details':json.loads(e['details']),'createdAt':e['created_at']} for e in timeline],'capabilities':reservation_controls(self,c,actor,row,sessions,at),'scope':self.scope()}

    def session_view(self,c,s,at):
        slot=self.row(c,'coaching_slots',s['slot_id']) if s['slot_id'] else None
        occ=self.row(c,'booking_occupancies',s['occupancy_id']) if s['occupancy_id'] else None
        expired=s['state']=='requested' and occ and occ['expires_at'] and occ['expires_at']<=at
        return {'id':s['id'],'ordinal':s['ordinal'],'state':'request_expired' if expired else s['state'],'storedState':s['state'],'slotId':s['slot_id'],'slot':self.coaching.slots.slot_view(slot,zone('Asia/Seoul')) if slot else None,'occupancyId':s['occupancy_id'],'deadlineAt':occ['expires_at'] if occ and occ['active'] else None,'version':s['version'],'performedAt':s['performed_at']}

    @staticmethod
    def scope():return {'kind':'reservation_model','publicWriteAPIEnabled':False,'commercialAcceptance':'pending','realGatewayConnected':False}

    def get(self,user,kind,identifier):
        if kind not in ('hold','reservation','policy'):raise ApiError(400,'예약 원문 종류를 확인해주세요.')
        with self.app.db.connect() as c:
            c.execute('BEGIN');self.require_schema(c);return self.detail_in(c,active(c,user),kind,identifier,self.at())

    def publish_policy(self,user,data):
        shape(data,{'productId','productVersion','payload','approvalReference','reason','requestKey'},{'productId','productVersion','payload','approvalReference','reason','requestKey'})
        def change(c,actor,at,reason):
            if actor['role']!='admin':raise ApiError(403,'관리자 검수가 필요합니다.')
            pid=text_field(data,'productId',maximum=100);root,revision,_,_=self.coaching.slots.current(c,pid)
            if type(data['productVersion']) is not int or data['productVersion']!=root['public_version']:raise ApiError(409,'현재 상품 공개판을 다시 확인해주세요.')
            payload=policy_payload(data['payload']);identifier=new_id('reservationpolicy')
            if c.execute('SELECT 1 FROM reservation_policies WHERE product_id=? AND product_revision_id=? AND payload_hash=?',(pid,revision['id'],digest(payload))).fetchone():raise ApiError(409,'같은 상품 판본/정책이 이미 있습니다.')
            insert(c,'reservation_policies',{'id':identifier,'product_id':pid,'product_revision_id':revision['id'],'product_version':root['public_version'],'scope':'technical','payload':encode(payload),'payload_hash':digest(payload),'approval_reference':text_field(data,'approvalReference',minimum=3,maximum=300),'created_by':actor['id'],'created_at':at})
            return 'policy',identifier,{'id':identifier,'scope':'technical'}
        return self.command(user,'policy.publish',data,change)

    def candidate(self,c,pid,choice,at):return future_context(c,self.coaching,pid,choice,at)

    def make_hold(self,c,actor,data,at,*,parent=None,ordinal=None,as_operator=False):
        pid=text_field(data,'productId',maximum=100);root,revision,payload,profile,slot=self.candidate(c,pid,data,at)
        policy=self.row(c,'reservation_policies',text_field(data,'policyId',maximum=100));rules=decoded(policy,'payload')
        if policy['scope']!='technical':raise ApiError(409,'실제 코칭 정책 인수/공개 API 연결을 먼저 확인해주세요.')
        if policy['product_id']!=pid:raise ApiError(404,'이 상품의 정책이 아닙니다.')
        product=self.coaching.slots.product_view(c,root,revision,payload,profile)
        if parent:
            if not c.execute("SELECT 1 FROM users WHERE id=? AND status='active'",(parent['member_id'],)).fetchone():raise ApiError(409,'회원의 현재 상태를 먼저 확인해주세요.')
            original=decoded(parent)['product']
            if parent['member_id']!=actor['id'] and actor['role']!='admin':
                if as_operator:self.operator(c,actor,parent)
                else:raise ApiError(403,'본인의 회차부터 선택해주세요.')
            if parent['state']!='confirmed' or parent['payment_state'] not in ('paid','not_required') or not parent['valid_until'] or at>=parent['valid_until']:raise ApiError(409,'현재 진행 가능한 예약/유효 회차를 확인해주세요.')
            if parent['product_id']!=pid or parent['policy_id']!=policy['id'] or parent['owner_id']!=profile['owner_user_id'] or any(original[k]!=product[k] for k in ['durationMinutes','method']):raise ApiError(409,'기존 제공 조건과 현재 코치/후보가 다릅니다. 운영 검토가 필요합니다.')
            if slot['ends_at']>parent['valid_until']:raise ApiError(409,'회차 유효 기간 안의 일정을 선택해주세요.')
            owner=parent['member_id'];quoted_product=original
        else:
            if policy['product_revision_id']!=revision['id'] or policy['product_version']!=root['public_version']:raise ApiError(409,'현재 상품 판본의 정책 검수가 필요합니다.')
            owner=actor['id'];quoted_product=product
        if occupied(c,profile['id'],slot['occupied_from'],slot['occupied_until'],at):raise ApiError(409,'같은 코치의 준비 시간을 포함한 범위가 이미 점유되어 있습니다.')
        expires=deadline(at,rules['holdSeconds'],slot['occupied_from']);hid=new_id('slothold');oid=new_id('bookingoccupancy')
        quote={'scope':'technical','product':quoted_product,'candidateProduct':product,'slot':self.coaching.slots.slot_view(slot,zone('Asia/Seoul')),'policyId':policy['id'],'policy':rules}
        insert(c,'slot_holds',{'id':hid,'member_id':owner,'coach_id':profile['id'],'owner_id':profile['owner_user_id'],'product_id':pid,'slot_id':slot['id'],'policy_id':policy['id'],'quote':encode(quote),'quote_hash':digest(quote),'reservation_id':parent['id'] if parent else None,'session_ordinal':ordinal,'state':'active','expires_at':expires,'created_at':at,'updated_at':at})
        insert(c,'booking_occupancies',{'id':oid,'hold_id':hid,'reservation_id':None,'session_id':None,'coach_id':profile['id'],'slot_id':slot['id'],'occupied_from':slot['occupied_from'],'occupied_until':slot['occupied_until'],'kind':'hold','active':1,'expires_at':expires,'checked_at':at,'created_at':at})
        return self.row(c,'slot_holds',hid),self.row(c,'booking_occupancies',oid)

    def hold(self,user,data):
        required={'productId','policyId','requestKey','reason'}|CHOICE_FIELDS
        shape(data,required|{'reservationId','sessionOrdinal','reservationVersion'},required)
        def change(c,actor,at,reason):
            parent=None;ordinal=None
            if 'reservationId' in data:
                parent=self.load_reservation(c,actor,text_field(data,'reservationId',maximum=100));expected(data,parent,'reservationVersion')
                if type(data.get('sessionOrdinal')) is not int:raise ApiError(400,'회차 번호를 확인해주세요.')
                ordinal=data['sessionOrdinal'];session=c.execute('SELECT * FROM booking_sessions WHERE reservation_id=? AND ordinal=?',(parent['id'],ordinal)).fetchone()
                if not session or session['state'] not in ('unscheduled','no_show') or session['state']=='no_show' and parent['no_show_consumes']:raise ApiError(409,'새 일정이 필요한 유효 회차를 선택해주세요.')
            elif {'sessionOrdinal','reservationVersion'}&set(data):raise ApiError(400,'회차의 예약 원문이 필요합니다.')
            hold,occ=self.make_hold(c,actor,data,at,parent=parent,ordinal=ordinal)
            self.event(c,actor,'hold.created',reason,at,hold=hold,reservation=parent,details={'expiresAt':hold['expires_at']})
            return 'hold',hold['id'],{'id':hold['id'],'version':hold['version'],'expiresAt':hold['expires_at']}
        return self.command(user,'hold.create',data,change)

    def release(self,user,identifier,data):
        shape(data,{'version','reason','requestKey'},{'version','reason','requestKey'})
        def change(c,actor,at,reason):
            hold=self.load_hold(c,actor,identifier);expected(data,hold)
            if hold['state']!='active':raise ApiError(409,'이미 소비/종료한 점유입니다.')
            occ=c.execute('SELECT * FROM booking_occupancies WHERE hold_id=?',(identifier,)).fetchone()
            update(c,'booking_occupancies',occ,{'active':0,'checked_at':at});hold=update(c,'slot_holds',hold,{'state':'expired' if hold['expires_at']<=at else 'released','updated_at':at})
            self.event(c,actor,'hold.'+hold['state'],reason,at,hold=hold)
            return 'hold',identifier,{'id':identifier,'state':hold['state'],'version':hold['version']}
        return self.command(user,'hold.release:'+identifier,data,change)

    def usable_hold(self,c,actor,identifier,version_data,at):
        hold=self.load_hold(c,actor,identifier);expected(version_data,hold)
        if hold['member_id']!=actor['id']:raise ApiError(403,'본인의 요청으로 진행해주세요.')
        occ=c.execute('SELECT * FROM booking_occupancies WHERE hold_id=?',(identifier,)).fetchone()
        if hold['state']!='active' or hold['expires_at']<=at or not occ or not occ['active']:raise ApiError(409,'임시 점유 기한이 끝났거나 이미 요청했습니다.')
        quote=decoded(hold);self.candidate(c,hold['product_id'],{'productVersion':quote['candidateProduct']['version'],'productRevisionId':quote['candidateProduct']['revisionId'],'calendarVersion':quote['candidateProduct']['availability']['calendarVersion'],'calendarRevisionId':quote['candidateProduct']['availability']['calendarRevisionId'],'slotId':hold['slot_id'],'slotVersion':1},at)
        profile=c.execute('SELECT * FROM coach_profiles WHERE id=?',(hold['coach_id'],)).fetchone()
        if profile['owner_user_id']!=hold['owner_id']:raise ApiError(409,'담당 코치 기준이 달라졌습니다.')
        return hold,occ,quote

    def request(self,user,identifier,data):
        shape(data,{'version','reason','requestKey','legacyInquiryId','reservationVersion'},{'version','reason','requestKey'})
        def change(c,actor,at,reason):
            hold,occ,quote=self.usable_hold(c,actor,identifier,data,at);policy=quote['policy'];product=quote['product']
            if hold['reservation_id']:
                reservation=self.load_reservation(c,actor,hold['reservation_id']);expected(data,reservation,'reservationVersion')
                if 'legacyInquiryId' in data:raise ApiError(400,'추가 회차에는 문의를 다시 연결하지 않습니다.')
                if reservation['state']!='confirmed' or reservation['valid_until']<=at:raise ApiError(409,'현재 유효한 예약을 확인해주세요.')
                session=c.execute('SELECT * FROM booking_sessions WHERE reservation_id=? AND ordinal=?',(reservation['id'],hold['session_ordinal'])).fetchone()
                if not session or session['state'] not in ('unscheduled','no_show') or session['state']=='no_show' and reservation['no_show_consumes']:raise ApiError(409,'해당 회차가 이미 진행 중입니다.')
                end=deadline(at,policy['coachSeconds'],occ['occupied_from'])
                session=update(c,'booking_sessions',session,{'state':'requested','slot_id':hold['slot_id'],'occupancy_id':occ['id'],'performed_at':None,'updated_at':at})
                reservation=update(c,'coaching_reservations',reservation,{'updated_at':at})
            else:
                if 'reservationVersion' in data:raise ApiError(400,'새 예약에는 기존 예약 판본을 지정하지 않습니다.')
                inquiry=data.get('legacyInquiryId')
                if inquiry is not None:
                    inquiry=text_field(data,'legacyInquiryId',maximum=100);old=c.execute('SELECT * FROM bookings WHERE id=? AND user_id=? AND coach_id=?',(inquiry,actor['id'],hold['coach_id'])).fetchone()
                    if not old:raise ApiError(404,'본인의 같은 코치 문의가 아닙니다.')
                    if c.execute('SELECT 1 FROM coaching_reservations WHERE legacy_inquiry_id=?',(inquiry,)).fetchone():raise ApiError(409,'이미 연결한 문의입니다.')
                rid=new_id('coachingreservation');payment='not_required' if product['price']==0 else 'unpaid';state='awaiting_payment' if payment=='unpaid' and product['bookingSequence']=='payment_then_coach' else 'awaiting_coach';end=deadline(at,policy['paymentSeconds'] if state=='awaiting_payment' else policy['coachSeconds'],occ['occupied_from'])
                insert(c,'coaching_reservations',{'id':rid,'member_id':actor['id'],'coach_id':hold['coach_id'],'owner_id':hold['owner_id'],'product_id':hold['product_id'],'policy_id':hold['policy_id'],'legacy_inquiry_id':inquiry,'quote':encode(quote),'quote_hash':digest(quote),'scope':'technical','price':product['price'],'currency':'KRW','payment_order_ref':new_id('coachingorder'),'sequence':product['bookingSequence'],'state':state,'payment_state':payment,'session_count':product['sessionCount'],'no_show_consumes':int(policy['noShowConsumes']),'deadline_at':end,'created_at':at,'updated_at':at})
                reservation=self.row(c,'coaching_reservations',rid);session=None
                for ordinal in range(1,product['sessionCount']+1):
                    sid=new_id('bookingsession');insert(c,'booking_sessions',{'id':sid,'reservation_id':rid,'ordinal':ordinal,'state':'requested' if ordinal==1 else 'unscheduled','slot_id':hold['slot_id'] if ordinal==1 else None,'occupancy_id':occ['id'] if ordinal==1 else None,'created_at':at,'updated_at':at})
                    if ordinal==1:session=self.row(c,'booking_sessions',sid)
            update(c,'booking_occupancies',occ,{'reservation_id':reservation['id'],'session_id':session['id'],'kind':'request','expires_at':end,'checked_at':at})
            hold=update(c,'slot_holds',hold,{'state':'consumed','updated_at':at})
            self.event(c,actor,'reservation.requested',reason,at,reservation=reservation,hold=hold,session=session)
            return 'reservation',reservation['id'],{'id':reservation['id'],'version':reservation['version'],'sessionId':session['id'],'state':reservation['state'],'paymentState':reservation['payment_state']}
        return self.command(user,'reservation.request:'+identifier,data,change)

    def respond(self,user,identifier,data):return self.flow.respond(user,identifier,data)
    def perform(self,user,identifier,data):return self.flow.perform(user,identifier,data)
    def cancel(self,user,identifier,data):return self.flow.cancel(user,identifier,data)
    def reschedule(self,user,identifier,data):return self.flow.reschedule(user,identifier,data)
    def observe_payment(self,identifier,reference,kind='paid'):return self.flow.observe_payment(identifier,reference,kind)
    def expire(self,limit=100):return self.flow.expire(limit)
    def expire_target(self,user,kind,identifier,data):return self.flow.expire_target(user,kind,identifier,data)
