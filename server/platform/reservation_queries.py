"""Bounded, snapshot-consistent reservation projections; no read-side effects."""
import json
import re

from ..core import ApiError
from .coaching_contract import active,bounded_query
from .reservation_contract import decoded,effective_state

HOLD_STATES=frozenset({'active','released','consumed','expired'})
RESERVATION_STATES=frozenset({'awaiting_coach','awaiting_payment','confirmed','completed','cancelled','expired','exception'})
PAYMENT_STATES=frozenset({'not_required','unpaid','paid','refund_pending','refunded','exception'})


def page_query(query,allowed=()):
    q=bounded_query(query,set(allowed)|{'page','pageSize'})
    values=[]
    for k,default,high in [('page','1',10000),('pageSize','20',50)]:
        value=q.get(k,default)
        if not re.fullmatch(r'[1-9][0-9]{0,4}',value) or int(value)>high:raise ApiError(400,'페이지 범위는1–10000, 크기는1–50입니다.')
        values.append(int(value))
    return q,*values


class ReservationQueries:
    def __init__(self,model):self.m=model

    def listing(self,user,kind,query,*,operator=False,admin=False):
        allowed={'state','coachId','productId'}|({'paymentState'} if kind=='reservation' else set())|({'memberId'} if operator or admin else set())
        q,page,size=page_query(query,allowed)
        states=HOLD_STATES if kind=='hold' else RESERVATION_STATES
        if 'state' in q and q['state'] not in states:raise ApiError(400,'현재 예약/점유 상태를 확인해주세요.')
        if 'paymentState' in q and q['paymentState'] not in PAYMENT_STATES:raise ApiError(400,'결제 상태를 확인해주세요.')
        for field in ('coachId','productId','memberId'):
            if field in q and not re.fullmatch(r'[a-z0-9_-]{1,100}',q[field]):raise ApiError(400,'조회 식별자를 확인해주세요.')
        with self.m.app.db.connect() as c:
            c.execute('BEGIN');self.m.require_schema(c);actor=active(c,user,operator);at=self.m.at()
            if admin and actor['role']!='admin':raise ApiError(403,'관리자 점유 큐입니다.')
            conditions=[];args=[]
            if not operator and not admin:conditions.append('t.member_id=?');args.append(actor['id'])
            elif actor['role']!='admin':conditions.append('t.owner_id=? AND cp.owner_user_id=?');args.extend([actor['id'],actor['id']])
            for name,column in [('coachId','coach_id'),('productId','product_id'),('memberId','member_id'),('paymentState','payment_state')]:
                if name in q:conditions.append('t.'+column+'=?');args.append(q[name])
            effective="CASE WHEN t.state='active' AND t.expires_at<=? THEN 'expired' ELSE t.state END" if kind=='hold' else "CASE WHEN t.state IN ('awaiting_coach','awaiting_payment') AND t.deadline_at<=? THEN CASE WHEN t.payment_state='paid' THEN 'exception' ELSE 'expired' END ELSE t.state END"
            if 'state' in q:conditions.append('('+effective+')=?');args.extend([at,q['state']])
            table='slot_holds' if kind=='hold' else 'coaching_reservations'
            clause=' FROM '+table+' t JOIN coach_profiles cp ON cp.id=t.coach_id'+(' WHERE '+' AND '.join(conditions) if conditions else '')
            total=c.execute('SELECT COUNT(*)'+clause,args).fetchone()[0]
            rows=c.execute('SELECT t.*'+clause+' ORDER BY t.created_at DESC,t.id DESC LIMIT ? OFFSET ?',args+[size,(page-1)*size]).fetchall()
            groups={}
            if kind=='reservation' and rows:
                identifiers=[r['id'] for r in rows]
                for s in c.execute('SELECT s.*,o.expires_at AS occupancy_expires_at,o.active AS occupancy_active,cs.starts_at,cs.ends_at,cs.time_zone FROM booking_sessions s LEFT JOIN booking_occupancies o ON o.id=s.occupancy_id LEFT JOIN coaching_slots cs ON cs.id=s.slot_id WHERE s.reservation_id IN ('+','.join('?' for _ in identifiers)+') ORDER BY s.ordinal',identifiers):groups.setdefault(s['reservation_id'],[]).append(s)
            items=[]
            for r in rows:
                quote=decoded(r);p=quote['product'];item={'id':r['id'],'memberId':r['member_id'],'coachId':r['coach_id'],'productId':r['product_id'],'policyId':r['policy_id'],'productTitle':p['title'],'price':p['price'],'currency':p['currency'],'state':effective_state(r,at,kind),'storedState':r['state'],'version':r['version'],'createdAt':r['created_at'],'updatedAt':r['updated_at']}
                if kind=='hold':item.update(slot=quote['slot'],expiresAt=r['expires_at'])
                else:
                    sessions=groups.get(r['id'],[])
                    item.update(paymentState=r['payment_state'],deadlineAt=r['deadline_at'],confirmedAt=r['confirmed_at'],validUntil=r['valid_until'],schedulingExpired=bool(r['valid_until'] and r['valid_until']<=at),sessionCount=r['session_count'],consumedSessionCount=sum(s['state']=='completed' or s['state']=='no_show' and r['no_show_consumes'] for s in sessions),sessions=[{'id':s['id'],'ordinal':s['ordinal'],'state':'request_expired' if s['state']=='requested' and s['occupancy_expires_at'] and s['occupancy_expires_at']<=at else s['state'],'storedState':s['state'],'version':s['version'],'slotId':s['slot_id'],'startsAt':s['starts_at'],'endsAt':s['ends_at'],'originalTimeZone':s['time_zone'],'deadlineAt':s['occupancy_expires_at'] if s['occupancy_active'] else None} for s in sessions])
                items.append(item)
            return {'holds' if kind=='hold' else 'reservations':items,'page':page,'pageSize':size,'total':total,'asOf':at,'queue':'admin' if admin or operator and actor['role']=='admin' else 'assigned' if operator else 'own'}

    def policies(self,user,pid,query):
        _,page,size=page_query(query)
        with self.m.app.db.connect() as c:
            c.execute('BEGIN');self.m.require_schema(c);active(c,user);at=self.m.at();root,revision,_,_=self.m.coaching.slots.current(c,pid)
            clause=" FROM reservation_policies WHERE product_id=? AND product_revision_id=? AND scope='technical'";args=[pid,revision['id']]
            total=c.execute('SELECT COUNT(*)'+clause,args).fetchone()[0];rows=c.execute('SELECT *'+clause+' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',args+[size,(page-1)*size]).fetchall()
            return {'policies':[{'id':r['id'],'productId':r['product_id'],'productRevisionId':r['product_revision_id'],'productVersion':r['product_version'],'scope':r['scope'],'payload':decoded(r,'payload'),'payloadHash':r['payload_hash'],'createdAt':r['created_at']} for r in rows],'productId':pid,'productVersion':root['public_version'],'productRevisionId':revision['id'],'page':page,'pageSize':size,'total':total,'asOf':at}

    def events(self,user,identifier,query):
        _,page,size=page_query(query)
        with self.m.app.db.connect() as c:
            c.execute('BEGIN');self.m.require_schema(c);actor=active(c,user);r=self.m.load_reservation(c,actor,identifier);at=self.m.at()
            total=c.execute('SELECT COUNT(*) FROM booking_events WHERE reservation_id=?',(identifier,)).fetchone()[0];rows=c.execute('SELECT * FROM booking_events WHERE reservation_id=? ORDER BY rowid DESC LIMIT ? OFFSET ?',(identifier,size,(page-1)*size)).fetchall()
            return {'reservationId':identifier,'reservationVersion':r['version'],'events':[{'id':e['id'],'action':e['action'],'version':e['version'],'sessionId':e['session_id'],'reason':e['reason'],'details':json.loads(e['details']),'createdAt':e['created_at']} for e in rows],'page':page,'pageSize':size,'total':total,'asOf':at}
