"""Coaching content and time contracts. No reservation, payment or legal approval."""

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..access import canonical_time
from ..core import ApiError, text_field
from .common import require_role
from .profiles import available, owner_eligible

UTC = timezone.utc


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def shape(data, allowed, required=()):
    if not isinstance(data, dict) or set(data)-set(allowed) or set(required)-set(data):
        raise ApiError(400, '요청 항목을 확인해주세요.')
    return data


def integer(data, key, low, high, step=1):
    value = data.get(key)
    if type(value) is not int or not low <= value <= high or value % step:
        raise ApiError(400, key+' 범위와 정수 단위를 확인해주세요.')
    return value


def strings(data, key, low, high, length):
    value = data.get(key)
    if not isinstance(value, list) or not low <= len(value) <= high or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= length for v in value):
        raise ApiError(400, key+' 항목을 확인해주세요.')
    result = [v.strip() for v in value]
    if len(set(result)) != len(result):
        raise ApiError(400, key+' 중복 항목을 제거해주세요.')
    return result


def zone(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 64 or not re.fullmatch(r'[A-Za-z0-9_+/-]+', value):
        raise ApiError(400, 'IANA 시간대 이름을 확인해주세요.')
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ApiError(400, '지원하는 IANA 시간대를 입력해주세요.')


def day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ApiError(400, '날짜는 YYYY-MM-DD 형식으로 입력해주세요.')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ApiError(400, '유효한 날짜를 입력해주세요.')


def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def stamp(value):
    return value.astimezone(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')


def local_unique(value, tz):
    # zoneinfo accepts nonexistent wall times. Round-trip both folds; never
    # silently choose a missing or ambiguous local instant when minting slots.
    choices = {}
    for fold in (0, 1):
        aware = value.replace(tzinfo=tz, fold=fold)
        utc = aware.astimezone(UTC)
        if utc.astimezone(tz).replace(tzinfo=None) == value:
            choices[utc] = aware
    return next(iter(choices.values())) if len(choices) == 1 else None


PRODUCT_FIELDS = {'title','description','topics','audience','outcomes','method','deliverySummary','durationMinutes','sessionCount','price','currency','preparation','bufferBeforeMinutes','bufferAfterMinutes','bookingSequence','cancellationSummary','rescheduleSummary','noShowSummary','coachAbsenceSummary','fulfillmentSummary','sessionValidityDays'}


def product_payload(data):
    shape(data, PRODUCT_FIELDS, PRODUCT_FIELDS)
    result = {}
    for key, low, high in (
        ('title',3,120),('description',20,5000),('audience',10,1500),
        ('deliverySummary',10,2000),('preparation',0,3000),
        ('cancellationSummary',10,2000),('rescheduleSummary',10,2000),
        ('noShowSummary',10,2000),('coachAbsenceSummary',10,2000),('fulfillmentSummary',10,2000),
    ):
        result[key] = text_field(data, key, minimum=low, maximum=high, optional=low==0)
    result['topics'] = strings(data,'topics',1,8,80)
    result['outcomes'] = strings(data,'outcomes',1,10,300)
    for key, low, high, step in (
        ('durationMinutes',15,240,5),('sessionCount',1,24,1),('price',0,10000000,1),
        ('bufferBeforeMinutes',0,180,5),('bufferAfterMinutes',0,180,5),('sessionValidityDays',1,365,1),
    ):
        result[key] = integer(data,key,low,high,step)
    for key, choices in [('currency',{'KRW'}),('method',{'online_live','in_person'}),('bookingSequence',{'coach_then_payment','payment_then_coach'})]:
        if not isinstance(data[key],str) or data[key] not in choices:
            raise ApiError(400, key+' 값을 확인해주세요.')
        result[key] = data[key]
    return result


CALENDAR_FIELDS = {'timeZone','effectiveFrom','effectiveUntil','weekly','timeOff','leadTimeHours','horizonDays','stepMinutes'}


def calendar_payload(data):
    shape(data, CALENDAR_FIELDS, CALENDAR_FIELDS)
    tz = zone(data['timeZone'])
    start,end = day(data['effectiveFrom']),day(data['effectiveUntil'])
    if not 0 <= (end-start).days <= 366:
        raise ApiError(400, '일정 적용 기간은 순서대로 최대367일입니다.')
    weekly = data['weekly']
    if not isinstance(weekly,list) or len(weekly)>28:
        raise ApiError(400, '주간 가능 시간은 최대28구간입니다.')
    normalized=[]
    for item in weekly:
        shape(item,{'weekday','start','end','endsNextDay'},{'weekday','start','end'})
        wd=integer(item,'weekday',0,6)
        for key in ('start','end'):
            if not isinstance(item[key],str) or not (re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',item[key]) or key=='end' and item[key]=='24:00'):
                raise ApiError(400, '주간 시간은 HH:MM 형식입니다.')
        next_day=item.get('endsNextDay',False)
        if type(next_day) is not bool or item['end']=='24:00' and next_day or (not next_day and item['end']<=item['start']) or (next_day and item['end']>item['start']):
            raise ApiError(400, '자정을 넘으면 endsNextDay=true와 다음 날 종료를 명시해주세요. 각 구간은 최대24시간입니다.')
        normalized.append({'weekday':wd,'start':item['start'],'end':item['end'],'endsNextDay':next_day})
    normalized.sort(key=lambda v:(v['weekday'],v['start'],v['end']))
    intervals=[]
    for row in normalized:
        minute=lambda value:int(value[:2])*60+int(value[3:])
        a=row['weekday']*1440+minute(row['start']);b=row['weekday']*1440+minute(row['end'])+(1440 if row['endsNextDay'] else 0)
        intervals.extend([(a,b),(a+10080,b+10080)])
    intervals.sort()
    if any(a[1]>b[0] for a,b in zip(intervals,intervals[1:])):
        raise ApiError(400, '다음 날과 주 경계를 포함해 주간 가능 시간이 겹칠 수 없습니다.')
    off=data['timeOff']
    if not isinstance(off,list) or len(off)>100:
        raise ApiError(400, '휴무/플레이 제외 구간은 최대100개입니다.')
    blocks=[]
    for item in off:
        shape(item,{'startsAt','endsAt','reason','originalStartsAt','originalEndsAt'},{'startsAt','endsAt','reason'})
        a,b=canonical_time(item['startsAt'],'휴무 시작'),canonical_time(item['endsAt'],'휴무 종료')
        if a>=b or instant(b)-instant(a)>timedelta(days=367):
            raise ApiError(400,'휴무 시작/종료 순서와 기간을 확인해주세요.')
        original_a,original_b=item.get('originalStartsAt',item['startsAt']),item.get('originalEndsAt',item['endsAt'])
        if canonical_time(original_a,'원 휴무 시작')!=a or canonical_time(original_b,'원 휴무 종료')!=b:
            raise ApiError(400,'원 휴무 시각과 변환된 시각이 다릅니다.')
        if any(instant(value).microsecond for value in (item['startsAt'],item['endsAt'],original_a,original_b)):
            raise ApiError(400,'휴무 시각은 소수 초 없이 초 단위로 입력해주세요.')
        blocks.append({'startsAt':a,'endsAt':b,'originalStartsAt':original_a,'originalEndsAt':original_b,'reason':text_field(item,'reason',minimum=3,maximum=500)})
    return {'timeZone':tz.key,'effectiveFrom':start.isoformat(),'effectiveUntil':end.isoformat(),
            'weekly':normalized,'timeOff':sorted(blocks,key=lambda v:(v['startsAt'],v['endsAt'],v['reason'])),
            'leadTimeHours':integer(data,'leadTimeHours',0,720),'horizonDays':integer(data,'horizonDays',1,180),
            'stepMinutes':integer(data,'stepMinutes',5,240,5)}


def active(c,user,operator=False):
    row=c.execute("SELECT * FROM users WHERE id=? AND status='active'",(user['id'],)).fetchone() if user else None
    if not row:
        raise ApiError(401,'로그인 상태를 다시 확인해주세요.')
    if operator:
        require_role(c,row,'coach')
    return row


def context_matches(c,profile,revision):
    return bool(profile and revision and available(c,profile) and owner_eligible(c,profile['owner_user_id'])
                and profile['owner_user_id']==revision['owner_id'] and profile['public_revision_id']==revision['profile_revision_id']
                and profile['public_version']==revision['profile_version'])


def bounded_query(query,allowed):
    if set(query)-set(allowed) or any(len(v)!=1 or not isinstance(v[0],str) for v in query.values()):
        raise ApiError(400,'조회 항목과 중복 값을 확인해주세요.')
    return {k:v[0] for k,v in query.items()}
