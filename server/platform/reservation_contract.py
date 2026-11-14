"""Reservation facts/eligibility. Explicit technical policies are not commercial acceptance."""
from datetime import timedelta
import json
import re

from ..core import ApiError, text_field
from .coaching_contract import shape, integer, instant, stamp, encode, digest

CHOICE_FIELDS = frozenset({'productVersion','productRevisionId','calendarVersion','calendarRevisionId','slotId','slotVersion'})
POLICY_FIELDS = frozenset({'holdSeconds','coachSeconds','paymentSeconds','rescheduleBeforeHours','cancelBeforeHours','noShowConsumes','refundOnCancellation','rescheduleRequiresCoach','validityAnchor','revisionRule'})


def effective_state(row,at,kind='reservation'):
    if kind=='hold':return 'expired' if row['state']=='active' and row['expires_at']<=at else row['state']
    if row['state'] in ('awaiting_coach','awaiting_payment') and row['deadline_at']<=at:return 'exception' if row['payment_state']=='paid' else 'expired'
    return row['state']


def policy_payload(value):
    shape(value,POLICY_FIELDS,POLICY_FIELDS)
    result={k:integer(value,k,30,604800) for k in ['holdSeconds','coachSeconds','paymentSeconds']}
    if result['holdSeconds']>1800:raise ApiError(400,'임시 점유 기한은 최대1800초입니다.')
    for k in ['rescheduleBeforeHours','cancelBeforeHours']:result[k]=integer(value,k,0,720)
    for k in ['noShowConsumes','refundOnCancellation','rescheduleRequiresCoach']:
        if type(value[k]) is not bool:raise ApiError(400,k+'는 명시한 참/거짓이어야 합니다.')
        result[k]=value[k]
    if value['validityAnchor']!='confirmed_at' or value['revisionRule']!='revalidate_until_confirmation':raise ApiError(400,'현재 기술 정책의 유효/재확인 기준을 확인해주세요.')
    return {**result,'validityAnchor':value['validityAnchor'],'revisionRule':value['revisionRule']}


def request_key(data):
    key=text_field(data,'requestKey',minimum=8,maximum=128)
    if not re.fullmatch(r'[A-Za-z0-9_-]+',key):raise ApiError(400,'원 요청 키를 확인해주세요.')
    return key


def expected(data,row,key='version'):
    if type(data.get(key)) is not int or data[key]!=row['version']:raise ApiError(409,'다른 판본의 예약/점유입니다. 현재 원문을 다시 확인해주세요.')


def decoded(row,key='quote'):
    value=json.loads(row[key])
    hash_key='quote_hash' if key=='quote' else 'payload_hash'
    if digest(value)!=row[hash_key]:raise ApiError(409,'저장한 예약/정책 원문을 확인할 수 없습니다.')
    return value


def deadline(now,seconds,occupied_from):
    value=min(instant(now)+timedelta(seconds=seconds),instant(occupied_from))
    if value<=instant(now):raise ApiError(409,'진행 기한과 준비 시간이 남아 있는 후보부터 선택해주세요.')
    return stamp(value)


def occupied(c,coach_id,a,b,now):
    if c.execute('PRAGMA user_version').fetchone()[0]<29:return False
    return bool(c.execute('SELECT 1 FROM booking_occupancies WHERE coach_id=? AND active=1 AND (expires_at IS NULL OR expires_at>?) AND occupied_from<? AND occupied_until>? LIMIT 1',(coach_id,now,b,a)).fetchone())


def future_context(c,coaching,pid,choice,now):
    root,revision,payload,profile=coaching.slots.current(c,pid);schedule=coaching.slots.calendar(c,profile)
    slot=c.execute('SELECT * FROM coaching_slots WHERE id=? AND product_id=? AND coach_id=?',(text_field(choice,'slotId',maximum=100),pid,profile['id'])).fetchone()
    if not slot:raise ApiError(404,'이 상품/코치의 후보가 아닙니다.')
    if not schedule or type(choice.get('productVersion')) is not int or type(choice.get('calendarVersion')) is not int or type(choice.get('slotVersion')) is not int or choice['slotVersion']!=1 or choice['productVersion']!=root['public_version'] or choice['productRevisionId']!=revision['id'] or choice['calendarVersion']!=schedule[0]['public_version'] or choice['calendarRevisionId']!=schedule[1]['id'] or slot['product_revision_id']!=revision['id'] or slot['calendar_revision_id']!=schedule[1]['id']:
        raise ApiError(409,'상품·일정의 현재 공개 판본을 다시 확인해주세요.')
    earliest,latest=coaching.slots.boundaries(schedule[2],instant(now))
    if not latest or slot['starts_at']<earliest or slot['ends_at']>latest or slot['occupied_from']<now:raise ApiError(409,'후보의 선택·준비 기한이 지났습니다.')
    return root,revision,payload,profile,slot
