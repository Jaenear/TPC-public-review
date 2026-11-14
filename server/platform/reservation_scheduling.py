"""Private scheduling options bind one current reservation/session; GET never writes."""
import re

from ..core import ApiError
from .coaching_contract import active, bounded_query, instant
from .reservation_contract import decoded, expected
from .reservation_capabilities import reservation_controls


def positive(query,key,maximum=2147483647):
    value=query.get(key,'')
    if not re.fullmatch(r'[1-9][0-9]{0,9}',value) or int(value)>maximum:raise ApiError(400,'회차와 현재 판본·페이지를 확인해주세요.')
    return int(value)


class ReservationScheduling:
    def __init__(self,model):self.m=model

    def candidates(self,user,identifier,query):
        with self.m.app.db.connect() as c:
            c.execute('BEGIN');self.m.require_schema(c);actor=active(c,user);at=self.m.at();r=self.m.load_reservation(c,actor,identifier)
            q=bounded_query(query,{'version','sessionOrdinal','sessionVersion','mode','fromDate','toDate','timeZone','page','pageSize'})
            if q.get('mode') not in ('request','reschedule'):raise ApiError(400,'후속 요청 또는 일정 변경을 선택해주세요.')
            mode=q['mode'];version=positive(q,'version');ordinal=positive(q,'sessionOrdinal',100);session_version=positive(q,'sessionVersion');page=positive({'page':q.get('page','1')},'page',10000)
            if q.get('pageSize','50')!='50':raise ApiError(400,'회차 후보는50개씩 조회합니다.')
            expected({'version':version},r)
            s=self.m.flow.session(c,r,{'sessionOrdinal':ordinal,'sessionVersion':session_version})
            if mode=='request' and actor['id']!=r['member_id']:raise ApiError(403,'후속 회차는 본인 계정으로 요청해주세요.')
            if mode=='reschedule':
                if actor['role']=='admin' or actor['id']!=r['member_id']:self.m.operator(c,actor,r)
                elif decoded(r)['policy']['rescheduleRequiresCoach']:raise ApiError(403,'이 정책의 변경은 담당 코치가 처리합니다.')
            sessions=c.execute('SELECT * FROM booking_sessions WHERE reservation_id=? ORDER BY ordinal',(identifier,)).fetchall()
            cap=reservation_controls(self.m,c,actor,r,sessions,at)['sessions'][ordinal-1]
            if not cap['canSchedule' if mode=='request' else 'canReschedule']:raise ApiError(409,'현재 회원·상품 조건/기한과 회차 진행 상태를 먼저 확인해주세요.')
            # The target exemption comes solely from this authorized session.
            # No public query can supply or widen an occupancy exclusion.
            exclude=None
            previous=None
            if mode=='reschedule':
                old=self.m.row(c,'booking_occupancies',s['occupancy_id'])
                if not old['active'] or old['kind']!='confirmed' or old['reservation_id']!=r['id'] or old['session_id']!=s['id'] or old['slot_id']!=s['slot_id']:raise ApiError(409,'기존 확정 회차의 현재 점유를 확인해주세요.')
                exclude=old['id']
                previous=self.m.row(c,'coaching_slots',s['slot_id'])
            slots=self.m.coaching.slots;root,revision,payload,profile=slots.current(c,r['product_id']);schedule=slots.calendar(c,profile)
            original=decoded(r)['product']
            if profile['owner_user_id']!=r['owner_id'] or any(payload[k]!=original[k] for k in ('durationMinutes','method')):raise ApiError(409,'현재 제공 조건이 원 예약과 달라 운영 검토가 필요합니다.')
            tz,start,end,a,b=slots.window(q,instant(at));context={'reservationId':r['id'],'reservationVersion':r['version'],'sessionOrdinal':s['ordinal'],'sessionVersion':s['version'],'mode':mode,'policyId':r['policy_id'],'validUntil':r['valid_until'],'originalSlotId':s['slot_id'] if mode=='reschedule' else None}
            result={'slots':[],'page':page,'pageSize':50,'total':0,'asOf':at,'fromDate':start.isoformat(),'toDate':end.isoformat(),'timeZone':tz.key,'productVersion':root['public_version'],'productRevisionId':revision['id'],'calendarVersion':schedule[0]['public_version'] if schedule else None,'calendarRevisionId':schedule[1]['id'] if schedule else None,'reservationEnabled':False,'schedulingContext':context}
            if not schedule:return result
            earliest,latest=slots.boundaries(schedule[2],instant(at))
            if latest is None:return result
            # Reuse the public window/boundaries/time projection. This private
            # predicate additionally caps the purchased validity and exempts
            # only the old target while preserving every other coach range.
            clause=' FROM coaching_slots WHERE product_id=? AND coach_id=? AND product_revision_id=? AND calendar_revision_id=? AND starts_at>=? AND starts_at<? AND starts_at>=? AND ends_at<=? AND occupied_from>=?'
            args=[r['product_id'],profile['id'],revision['id'],schedule[1]['id'],a,b,earliest,min(latest,r['valid_until']),at]
            if previous:clause+=' AND NOT(starts_at=? AND ends_at=?)';args.extend([previous['starts_at'],previous['ends_at']])
            clause+=' AND NOT EXISTS(SELECT 1 FROM booking_occupancies o WHERE o.coach_id=coaching_slots.coach_id AND o.active=1 AND (o.expires_at IS NULL OR o.expires_at>?) AND o.occupied_from<coaching_slots.occupied_until AND o.occupied_until>coaching_slots.occupied_from'+(' AND o.id!=?' if exclude else '')+')';args.append(at)
            if exclude:args.append(exclude)
            result['total']=c.execute('SELECT COUNT(*)'+clause,args).fetchone()[0]
            rows=c.execute('SELECT *'+clause+' ORDER BY starts_at,id LIMIT 50 OFFSET ?',args+[(page-1)*50]).fetchall();result['slots']=[slots.slot_view(row,tz) for row in rows]
            return result
