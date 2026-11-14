"""One-snapshot private review projections; reading never adopts or expires data."""
import json
import re

from ..core import ApiError
from .common import roles
from .hand_review_contract import active, bounded_query, digest, legacy_snapshot, LEGACY_STATES, STATES


def page(query):
    value = query.get('page','1')
    if not re.fullmatch(r'[1-9][0-9]{0,4}',value) or int(value)>10000:
        raise ApiError(400,'페이지 번호를 확인해주세요.')
    return int(value)


class HandReviewQueries:
    def __init__(self, model): self.m = model

    def scope(self):
        return {'kind':'hand_review_api','privateMaterialsOnly':True,'solverConnected':False,
                'commercialPaymentConnected':False,'attachmentsConfigured':self.m.attachments.configured(),
                'attachmentPolicy':self.m.attachments.public_policy()}

    def detail_in(self, c, user, rid):
        actor,row,head = self.m.scope(c,user,rid)
        own = actor['id']==row['user_id']; admin = actor['role']=='admin'
        operator = (admin or self.m.assigned(c,actor,row,head)) and not own
        member_active = bool(c.execute("SELECT 1 FROM users WHERE id=? AND status='active'",(row['user_id'],)).fetchone())
        current = None; assignment = self.m.assignment(c,head); response = None
        if head:
            current = c.execute('SELECT * FROM hand_review_revisions WHERE id=?',(head['revision_id'],)).fetchone()
            if head['response_id']:
                saved = c.execute('SELECT * FROM hand_review_responses WHERE id=?',(head['response_id'],)).fetchone()
                response = {'id':saved['id'],'reviewId':saved['review_id'],'revisionId':saved['revision_id'],
                            'assignmentId':saved['assignment_id'],'kind':saved['kind'],'body':saved['body'],
                            'authorId':saved['author_id'],'createdAt':saved['created_at']}
        state = head['state'] if head else LEGACY_STATES.get(row['status'],'legacy_unknown')
        terminal = state in ('answered','cancelled')
        review = {'id':rid,'memberId':row['user_id'],'managed':bool(head),'version':head['version'] if head else None,'state':state,
                  'legacyHash':head['baseline_hash'] if head else digest(legacy_snapshot(row)),
                  'original':json.loads(head['baseline']) if head else legacy_snapshot(row),
                  'revision':self.revision_view(current) if current else None,
                  'assignment':{'id':assignment['id'],'coachId':assignment['coach_id'],'ownerId':assignment['owner_id'],
                                'profileRevisionId':assignment['profile_revision_id'],'profileVersion':assignment['profile_version'],
                                'actorId':assignment['actor_id'],'reason':assignment['reason'],'createdAt':assignment['created_at']} if assignment else None,
                  'response':response,'informationRequest':self.m.information_request(c,head),
                  'createdAt':head['created_at'] if head else row['created_at'],
                  'updatedAt':head['updated_at'] if head else row['updated_at']}
        member = c.execute('SELECT name FROM users WHERE id=?',(row['user_id'],)).fetchone()
        review['memberLabel'] = member['name'] if member and member['name'] else '수강생'
        if assignment:
            profile = c.execute('SELECT payload FROM coach_profile_revisions WHERE id=?',(assignment['profile_revision_id'],)).fetchone()
            try: saved = json.loads(profile['payload']) if profile else {}
            except (TypeError,ValueError): saved = {}
            name = saved.get('name') if isinstance(saved,dict) else None
            review['assignment']['name'] = name if isinstance(name,str) and name else '담당 코치'
        controls = {'isMember':own,'isAdmin':admin,'isOperator':operator,'canAdopt':bool(not head and state!='legacy_unknown' and (own or admin)),
                    'canRevise':bool(head and own and member_active and state in ('submitted','needs_information')),
                    'canAssign':bool(head and admin and member_active and not terminal),
                    'canUnassign':bool(head and admin and assignment),
                    'canStart':bool(head and operator and member_active and state=='submitted'),
                    'canRequestInformation':bool(head and operator and member_active and state in ('submitted','in_review')),
                    'canAnswer':bool(head and operator and member_active and state=='in_review'),
                    'canCancel':bool(head and not terminal and (own or operator or admin)),
                    'canUpload':bool(head and own and state in ('submitted','needs_information') and self.m.attachments.configured())}
        return {'scope':self.scope(),'asOf':self.m.clock(),'review':review,'capabilities':controls,
                'attachments':self.m.attachments.list_in(c,actor,row,head),
                'attachmentPage':{'page':1,'pageSize':20,'total':self.m.attachments.total_in(c,actor,row,head)}}

    def revision_view(self, row):
        return {'id':row['id'],'number':row['revision_no'],'kind':row['kind'],'payload':json.loads(row['payload']),
                'hash':row['payload_hash'],'authorId':row['author_id'],'reason':row['reason'],'createdAt':row['created_at'],
                'validationScope':'recorded_cards_stacks_actions' if row['kind']=='structured' else 'unparsed_legacy_narrative'}

    def detail(self, user, rid):
        with self.m.app.db.connect() as c:
            c.execute('BEGIN'); return self.detail_in(c,user,rid)

    def history(self, user, rid, kind, query):
        q = bounded_query(query,{'page'}); number = page(q)
        table = 'hand_review_revisions' if kind=='revisions' else 'hand_review_events'
        order = 'revision_no' if kind=='revisions' else 'version'
        with self.m.app.db.connect() as c:
            c.execute('BEGIN'); actor,review,head = self.m.scope(c,user,rid)
            total = c.execute('SELECT COUNT(*) FROM '+table+' WHERE review_id=?',(rid,)).fetchone()[0]
            rows = c.execute('SELECT * FROM '+table+' WHERE review_id=? ORDER BY '+order+' DESC LIMIT 20 OFFSET ?',(rid,(number-1)*20)).fetchall()
            values = [self.revision_view(r) if kind=='revisions' else {'id':r['id'],'version':r['version'],'action':r['action'],
                      'actorId':r['actor_id'],'reason':r['reason'],'result':json.loads(r['payload']),'createdAt':r['created_at']} for r in rows]
            return {'scope':self.scope(),'reviewId':rid,'version':head['version'] if head else None,'asOf':self.m.clock(),
                    kind:values,'total':total,'page':number,'pageSize':20}

    def listing(self, user, queue, query):
        q = bounded_query(query,{'page','status','managed','q','coachId'}); number = page(q)
        status = q.get('status','all'); managed = q.get('managed','all'); text = q.get('q','')
        if status not in {*STATES,'all','legacy_unknown'} or managed not in ('all','yes','no') or len(text)>120:
            raise ApiError(400,'조회 상태와 검색 범위를 확인해주세요.')
        if 'coachId' in q and not re.fullmatch(r'[a-z0-9_-]{1,100}',q['coachId']): raise ApiError(400,'담당 코치를 확인해주세요.')
        with self.m.app.db.connect() as c:
            c.execute('BEGIN'); self.m.require_schema(c); actor = active(c,user)
            if queue=='manage' and actor['role']!='admin': raise ApiError(403,'운영자만 전체 리뷰를 조회할 수 있습니다.')
            if queue=='studio' and actor['role']!='admin' and 'coach' not in roles(c,actor): raise ApiError(403,'현재 코치 권한이 필요합니다.')
            clause = ' FROM reviews r LEFT JOIN hand_review_heads h ON h.review_id=r.id LEFT JOIN hand_review_revisions v ON v.id=h.revision_id LEFT JOIN hand_review_assignments a ON a.id=h.assignment_id LEFT JOIN coach_profiles p ON p.id=a.coach_id WHERE 1=1'
            args = []
            if queue=='member': clause+=' AND r.user_id=?'; args.append(actor['id'])
            elif queue=='studio':
                clause+=' AND h.review_id IS NOT NULL'
                if actor['role']!='admin':
                    clause+=" AND r.user_id!=? AND a.owner_id=? AND p.owner_user_id=a.owner_id AND p.state='active' AND p.public_revision_id IS NOT NULL";args.extend([actor['id'],actor['id']])
            state = "COALESCE(h.state,CASE r.status WHEN '접수' THEN 'submitted' WHEN '검토 중' THEN 'in_review' WHEN '답변 완료' THEN 'answered' WHEN '취소' THEN 'cancelled' ELSE 'legacy_unknown' END)"
            title = "CASE WHEN v.kind='structured' THEN json_extract(v.payload,'$.title') ELSE r.title END"
            if status!='all': clause+=' AND '+state+'=?'; args.append(status)
            if managed!='all': clause+=' AND h.review_id IS '+('NOT NULL' if managed=='yes' else 'NULL')
            if text: clause+=' AND instr(lower('+title+'),lower(?))>0'; args.append(text)
            if 'coachId' in q: clause+=' AND a.coach_id=?'; args.append(q['coachId'])
            total = c.execute('SELECT COUNT(*)'+clause,args).fetchone()[0]
            rows = c.execute("SELECT r.id,r.user_id,(SELECT name FROM users WHERE id=r.user_id) AS member_label,r.created_at,COALESCE(h.updated_at,r.updated_at) AS updated_at,h.version,h.revision_id,a.coach_id,"+state+' AS current_state,'+title+' AS current_title'+clause+' ORDER BY updated_at DESC,r.id DESC LIMIT 20 OFFSET ?',args+[(number-1)*20]).fetchall()
            result = [{'id':r['id'],'memberId':r['user_id'],'title':r['current_title'],'state':r['current_state'],'version':r['version'],
                       'memberLabel':r['member_label'] or '수강생',
                       'managed':r['version'] is not None,'revisionId':r['revision_id'],'coachId':r['coach_id'],
                       'createdAt':r['created_at'],'updatedAt':r['updated_at']} for r in rows]
            return {'scope':self.scope(),'asOf':self.m.clock(),'queue':queue,'reviews':result,'total':total,'page':number,'pageSize':20}
