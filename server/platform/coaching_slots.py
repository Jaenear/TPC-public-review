"""Mint immutable candidate facts explicitly; every read rechecks current context."""

from datetime import datetime, time, timedelta
import hashlib
import json

from ..core import ApiError, now_iso, text_field
from .coaching_contract import (
    UTC, active, shape, context_matches, bounded_query, zone, day, instant,
    stamp, local_unique, integer,
)
from .reservation_contract import occupied

PUBLIC_JOIN = """ FROM coaching_products p
 JOIN coaching_product_revisions r ON r.id=p.public_revision_id AND r.product_id=p.id
 JOIN coach_profiles cp ON cp.id=p.coach_id
 JOIN users u ON u.id=cp.owner_user_id
 WHERE p.state='active' AND r.status='approved' AND cp.state='active'
 AND cp.public_revision_id=r.profile_revision_id AND cp.public_version=r.profile_version
 AND cp.owner_user_id=r.owner_id AND u.status='active'
 AND (u.role='admin' OR EXISTS(SELECT 1 FROM user_roles ur WHERE ur.user_id=u.id AND ur.role='coach'))
"""


class CoachingSlots:
    def __init__(self,service):
        self.service=service
        self.app=service.app

    def current(self,c,pid):
        row=c.execute('SELECT p.*'+PUBLIC_JOIN+' AND p.id=?',(pid,)).fetchone()
        if not row:raise ApiError(404,'현재 공개 중인 코칭 상품을 찾을 수 없습니다.')
        revision=self.service.revision(c,'product',pid,row['public_revision_id'])
        payload=self.service.payload('product',revision)
        profile=c.execute('SELECT * FROM coach_profiles WHERE id=?',(row['coach_id'],)).fetchone()
        return row,revision,payload,profile

    def calendar(self,c,profile):
        root=self.service.root(c,'calendar',profile['id'])
        if not root or root['state']!='active' or not root['public_revision_id']:return None
        revision=self.service.revision(c,'calendar',profile['id'],root['public_revision_id'])
        if revision['status']!='approved' or not context_matches(c,profile,revision):return None
        return root,revision,self.service.payload('calendar',revision)

    def product_view(self,c,row,revision,payload,profile):
        schedule=self.calendar(c,profile)
        coach_revision=c.execute('SELECT payload FROM coach_profile_revisions WHERE id=?',(profile['public_revision_id'],)).fetchone()
        coach_payload=json.loads(coach_revision['payload']);name=coach_payload['name']
        return {'id':row['id'],'coachId':profile['id'],'coachName':name,'coachPilot':coach_payload['pilot'],'coachVersion':profile['public_version'],
                'version':row['public_version'],'revisionId':revision['id'],**payload,'reviewedAt':revision['reviewed_at'],
                'availability':{'calendarVersion':schedule[0]['public_version'] if schedule else None,
                                'calendarRevisionId':schedule[1]['id'] if schedule else None,'candidatesReady':bool(schedule)},
                'reservationEnabled':False,'paymentEnabled':False,'commercialAcceptance':'pending'}

    def product(self,pid):
        with self.app.db.connect() as c:
            c.execute('BEGIN');values=self.current(c,pid)
            return {'product':self.product_view(c,*values)}

    def products(self,query):
        q=bounded_query(query,{'coachId','page','q'})
        try:page=int(q.get('page','1'))
        except ValueError:raise ApiError(400,'페이지 번호를 확인해주세요.')
        if not 1<=page<=10000 or len(q.get('q',''))>100:raise ApiError(400,'페이지/검색 범위를 확인해주세요.')
        clause=PUBLIC_JOIN;args=[]
        if q.get('coachId'):clause+=' AND p.coach_id=?';args.append(q['coachId'])
        if q.get('q'):clause+=' AND INSTR(lower(r.payload),lower(?))>0';args.append(q['q'])
        with self.app.db.connect() as c:
            c.execute('BEGIN')
            total=c.execute('SELECT COUNT(*)'+clause,args).fetchone()[0]
            rows=c.execute('SELECT p.*'+clause+' ORDER BY p.coach_id,p.id LIMIT 20 OFFSET ?',args+[(page-1)*20]).fetchall()
            products=[self.product_view(c,*self.current(c,r['id'])) for r in rows]
            return {'products':products,'page':page,'pageSize':20,'total':total,'asOf':now_iso()}

    def window(self,q,now):
        tz=zone(q.get('timeZone','Asia/Seoul'))
        start=day(q['fromDate']) if 'fromDate' in q else now.astimezone(tz).date()
        try:end=day(q['toDate']) if 'toDate' in q else start+timedelta(days=14)
        except OverflowError:raise ApiError(400,'조회 날짜 범위를 확인해주세요.')
        if not 1<=(end-start).days<=31:raise ApiError(400,'조회 범위는 시작일 포함·종료일 제외 최대31일입니다.')
        a,b=local_unique(datetime.combine(start,time()),tz),local_unique(datetime.combine(end,time()),tz)
        if a is None or b is None:raise ApiError(400,'조회 경계가 중복되거나 없는 현지 시각입니다. 다른 날짜를 선택해주세요.')
        return tz,start,end,stamp(a),stamp(b)

    def slot_view(self,row,tz):
        return {'id':row['id'],'version':1,'productId':row['product_id'],'coachId':row['coach_id'],
                'productRevisionId':row['product_revision_id'],'calendarRevisionId':row['calendar_revision_id'],
                'startsAt':row['starts_at'],'endsAt':row['ends_at'],
                'originalStartsAt':row['original_starts_at'],'originalEndsAt':row['original_ends_at'],
                'originalTimeZone':row['time_zone'],'displayTimeZone':tz.key,
                'displayStartsAt':instant(row['starts_at']).astimezone(tz).isoformat(timespec='seconds'),
                'displayEndsAt':instant(row['ends_at']).astimezone(tz).isoformat(timespec='seconds'),
                'kind':'candidate','reservationEnabled':False}

    def boundaries(self,payload,now):
        tz=zone(payload['timeZone'])
        earliest=stamp(now+timedelta(hours=payload['leadTimeHours']))
        last=min(day(payload['effectiveUntil']),now.astimezone(tz).date()+timedelta(days=payload['horizonDays']))
        end=local_unique(datetime.combine(last+timedelta(days=1),time()),tz)
        return earliest,stamp(end) if end else None

    def listing(self,query):
        q=bounded_query(query,{'productId','fromDate','toDate','timeZone','page','pageSize'})
        pid=q.get('productId','')
        if not pid:raise ApiError(400,'슬롯을 조회할 상품을 선택해주세요.')
        try:page,size=int(q.get('page','1')),int(q.get('pageSize','50'))
        except ValueError:raise ApiError(400,'페이지 범위를 확인해주세요.')
        if not 1<=page<=10000 or not 1<=size<=100:raise ApiError(400,'페이지 범위를 확인해주세요.')
        now=instant(now_iso());tz,start,end,a,b=self.window(q,now)
        with self.app.db.connect() as c:
            c.execute('BEGIN');product,revision,payload,profile=self.current(c,pid);schedule=self.calendar(c,profile)
            result={'slots':[],'page':page,'pageSize':size,'total':0,'asOf':stamp(now),'fromDate':start.isoformat(),'toDate':end.isoformat(),
                    'timeZone':tz.key,'productVersion':product['public_version'],'productRevisionId':revision['id'],
                    'calendarVersion':schedule[0]['public_version'] if schedule else None,'calendarRevisionId':schedule[1]['id'] if schedule else None,
                    'reservationEnabled':False}
            if not schedule:return result
            earliest,latest=self.boundaries(schedule[2],now)
            if latest is None:return result
            clause=' FROM coaching_slots WHERE product_id=? AND coach_id=? AND product_revision_id=? AND calendar_revision_id=? AND starts_at>=? AND starts_at<? AND starts_at>=? AND ends_at<=? AND occupied_from>=?'
            args=[pid,profile['id'],revision['id'],schedule[1]['id'],a,b,earliest,latest,stamp(now)]
            if c.execute('PRAGMA user_version').fetchone()[0]>=29:
                clause+=' AND NOT EXISTS(SELECT 1 FROM booking_occupancies o WHERE o.coach_id=coaching_slots.coach_id AND o.active=1 AND (o.expires_at IS NULL OR o.expires_at>?) AND o.occupied_from<coaching_slots.occupied_until AND o.occupied_until>coaching_slots.occupied_from)'
                args.append(stamp(now))
            result['total']=c.execute('SELECT COUNT(*)'+clause,args).fetchone()[0]
            rows=c.execute('SELECT *'+clause+' ORDER BY starts_at,id LIMIT ? OFFSET ?',args+[size,(page-1)*size]).fetchall()
            result['slots']=[self.slot_view(r,tz) for r in rows]
            return result

    def selection(self,user,pid,data):
        fields={'productVersion','productRevisionId','calendarVersion','calendarRevisionId','slotId','slotVersion'}
        shape(data,fields,fields)
        with self.app.db.connect() as c:
            c.execute('BEGIN');active(c,user)
            root,revision,payload,profile=self.current(c,pid);schedule=self.calendar(c,profile)
            row=c.execute('SELECT * FROM coaching_slots WHERE id=? AND product_id=? AND coach_id=?',(text_field(data,'slotId',maximum=100),pid,profile['id'])).fetchone()
            if not row:raise ApiError(404,'이 상품/코치의 슬롯을 찾을 수 없습니다.')
            if not schedule or type(data['productVersion']) is not int or type(data['calendarVersion']) is not int or type(data['slotVersion']) is not int or data['slotVersion']!=1 or data['productVersion']!=root['public_version'] or data['productRevisionId']!=revision['id'] or data['calendarVersion']!=schedule[0]['public_version'] or data['calendarRevisionId']!=schedule[1]['id'] or row['product_revision_id']!=revision['id'] or row['calendar_revision_id']!=schedule[1]['id']:
                raise ApiError(409,'상품·가격 또는 가능 시간이 바뀌었습니다. 최신 공개본에서 다시 선택해주세요.')
            now=instant(now_iso());earliest,latest=self.boundaries(schedule[2],now)
            if latest is None or row['starts_at']<earliest or row['ends_at']>latest or row['occupied_from']<stamp(now):
                raise ApiError(409,'선택 가능한 기한이 지난 슬롯입니다. 최신 일정을 확인해주세요.')
            if occupied(c,profile['id'],row['occupied_from'],row['occupied_until'],stamp(now)):
                raise ApiError(409,'같은 코치의 준비 시간을 포함한 시간이 이미 점유되어 있습니다.')
            return {'selection':{'product':self.product_view(c,root,revision,payload,profile),'slot':self.slot_view(row,zone('Asia/Seoul')),
                                 'productVersion':root['public_version'],'calendarVersion':schedule[0]['public_version'],
                                 'checkedAt':stamp(now),'kind':'candidate_validation','reserved':False,'paymentStarted':False,
                                 'revalidationRequired':True}}

    def generate(self,user,pid,data):
        required={'version','publicVersion','calendarVersion','fromDate','toDate','reason','requestKey'}
        shape(data,required,required)
        def mutate(c,actor,profile,root):
            public,revision,payload,_=self.current(c,pid)
            schedule=self.calendar(c,profile)
            if not schedule or type(data['publicVersion']) is not int or type(data['calendarVersion']) is not int or data['publicVersion']!=public['public_version'] or data['calendarVersion']!=schedule[0]['public_version']:
                raise ApiError(409,'현재 검수된 상품과 일정을 다시 확인해주세요.')
            rules=schedule[2];tz=zone(rules['timeZone']);start,end=day(data['fromDate']),day(data['toDate']);now=instant(now_iso())
            today=now.astimezone(tz).date()
            if not 1<=(end-start).days<=31 or start<today or (end-today).days>rules['horizonDays']+1:
                raise ApiError(400,'생성 범위는 코치 현지 오늘 이후·공개 horizon 안의 최대31일입니다.')
            before=timedelta(minutes=payload['bufferBeforeMinutes']);after=timedelta(minutes=payload['bufferAfterMinutes'])
            duration=timedelta(minutes=payload['durationMinutes']);step=timedelta(minutes=rules['stepMinutes'])
            earliest,latest=self.boundaries(rules,now);candidates=[];omitted=[];current=start-timedelta(days=1)
            blocks=[(instant(v['startsAt']),instant(v['endsAt'])) for v in rules['timeOff']]
            while current<end:
                if current<=day(rules['effectiveUntil']):
                    for interval in rules['weekly']:
                        if interval['weekday']!=current.weekday():continue
                        a=datetime.combine(current,time.fromisoformat(interval['start']))
                        b=datetime.combine(current+timedelta(days=1) if interval['endsNextDay'] or interval['end']=='24:00' else current,
                                           time() if interval['end']=='24:00' else time.fromisoformat(interval['end']))
                        left,right=local_unique(a,tz),local_unique(b,tz)
                        if left is None or right is None:
                            omitted.append({'date':current.isoformat(),'start':interval['start'],'end':interval['end'],'code':'ambiguous_or_missing_boundary'});continue
                        lo,hi=left.astimezone(UTC),right.astimezone(UTC);cursor=a+before
                        while cursor<b:
                            local=local_unique(cursor,tz)
                            if local is None:
                                omitted.append({'date':current.isoformat(),'start':cursor.time().isoformat(timespec='minutes'),'code':'ambiguous_or_missing_start'});cursor+=step;continue
                            u=local.astimezone(UTC);v=u+duration;occupied_a,occupied_b=u-before,v+after
                            if start<=local.date()<end and day(rules['effectiveFrom'])<=local.date()<=day(rules['effectiveUntil']) and occupied_a>=lo and occupied_a>=now and occupied_b<=hi and stamp(u)>=earliest and latest and stamp(v)<=latest and not any(occupied_a<y and occupied_b>x for x,y in blocks):
                                key=revision['id']+'\0'+schedule[1]['id']+'\0'+stamp(u)
                                sid='cslot_'+hashlib.sha256(key.encode()).hexdigest()[:32]
                                candidates.append((sid,pid,profile['id'],revision['id'],schedule[1]['id'],stamp(u),stamp(v),stamp(occupied_a),stamp(occupied_b),local.isoformat(timespec='seconds'),v.astimezone(tz).isoformat(timespec='seconds'),tz.key,now_iso()))
                                if len(candidates)>5000:raise ApiError(400,'한 번의 슬롯 생성은 최대5000개입니다. 날짜 범위를 줄여주세요.')
                            cursor+=step
                current+=timedelta(days=1)
            created=0
            for candidate in candidates:
                cursor=c.execute('INSERT INTO coaching_slots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(product_revision_id,calendar_revision_id,starts_at) DO NOTHING',candidate);created+=cursor.rowcount
            self.service.bump(c,'product',pid)
            return {'entityId':pid,'revisionId':revision['id'],'version':root['version']+1,'publicVersion':public['public_version'],
                    'calendarVersion':schedule[0]['public_version'],'calendarRevisionId':schedule[1]['id'],
                    'fromDate':start.isoformat(),'toDate':end.isoformat(),'timeZone':tz.key,'candidateCount':len(candidates),
                    'createdCount':created,'existingCount':len(candidates)-created,'omittedLocalTimes':omitted,
                    'kind':'candidate_generation','reserved':False}
        return self.service.command(user,'product',pid,'product.generate',data,mutate)
