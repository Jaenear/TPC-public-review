"""Private hand-review workflow, current ownership and immutable command effects."""
import json

from ..core import ApiError, new_id, now_iso, text_field
from .common import roles
from .profiles import available, owner_eligible
from .hand_review_contract import (active, shape, encode, digest, expected,
                                   request_key, hand_payload, legacy_snapshot,
                                   legacy_projection, LEGACY_STATES, STATES)


def insert(c, table, values):
    names = list(values)
    c.execute('INSERT INTO '+table+'('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')', [values[n] for n in names])


class HandReviews:
    def __init__(self, app):
        from .hand_review_queries import HandReviewQueries
        from .hand_review_attachments import HandReviewAttachments
        self.app = app
        self.clock = now_iso
        self.reads = HandReviewQueries(self)
        self.attachments = HandReviewAttachments(self)

    def require_schema(self, c):
        if c.execute('PRAGMA user_version').fetchone()[0] < 30:
            raise ApiError(409, '리뷰 원장 준비를 확인해주세요.')

    def assignment(self, c, head):
        return c.execute('SELECT * FROM hand_review_assignments WHERE id=?', (head['assignment_id'],)).fetchone() if head and head['assignment_id'] else None

    def assigned(self, c, actor, review, head):
        assignment = self.assignment(c, head)
        if not assignment or actor['id'] == review['user_id'] or actor['id'] != assignment['owner_id'] or 'coach' not in roles(c, actor):
            return False
        profile = c.execute('SELECT * FROM coach_profiles WHERE id=?', (assignment['coach_id'],)).fetchone()
        return bool(available(c, profile) and profile['owner_user_id'] == assignment['owner_id'] and owner_eligible(c, assignment['owner_id']))

    def scope(self, c, user, rid):
        self.require_schema(c); actor = active(c, user)
        review = c.execute('SELECT * FROM reviews WHERE id=?', (rid,)).fetchone()
        head = c.execute('SELECT * FROM hand_review_heads WHERE review_id=?', (rid,)).fetchone()
        if not review or not (actor['id'] == review['user_id'] or actor['role'] == 'admin' or self.assigned(c, actor, review, head)):
            raise ApiError(404, '현재 접근할 수 있는 핸드 리뷰가 없습니다.')
        return actor, review, head

    def member_active(self, c, review):
        if not c.execute("SELECT 1 FROM users WHERE id=? AND status='active'", (review['user_id'],)).fetchone():
            raise ApiError(409, '회원의 현재 상태를 먼저 확인해주세요.')

    def operator(self, c, actor, review, head):
        if actor['id'] == review['user_id'] or not (actor['role'] == 'admin' or self.assigned(c, actor, review, head)):
            raise ApiError(403, '현재 담당 코치 또는 운영자의 검토가 필요합니다.')

    def managed(self, head):
        if not head:
            raise ApiError(409, '원문을 확인하고 새 리뷰 관리 구조에 명시적으로 연결해주세요.')
        return head

    def bump(self, c, head, **changes):
        values = {**changes, 'version': head['version']+1, 'updated_at': self.clock()}
        result = c.execute('UPDATE hand_review_heads SET '+','.join(n+'=?' for n in values)+' WHERE review_id=? AND version=?', list(values.values())+[head['review_id'],head['version']])
        if result.rowcount != 1:
            raise ApiError(409, '리뷰가 변경되었습니다. 현재 원문을 확인해주세요.')
        return c.execute('SELECT * FROM hand_review_heads WHERE review_id=?', (head['review_id'],)).fetchone()

    def effect(self, head, **extra):
        return {'version': head['version'],'state': head['state'],'revisionId': head['revision_id'],
                'assignmentId': head['assignment_id'],'responseId': head['response_id'], **extra}

    def command(self, user, operation, rid, data, apply):
        key = request_key(data.get('requestKey'))
        try:
            hashed = digest({'operation':operation,'reviewId':rid,'data':data})
            encode(data).encode('utf-8')
        except (ValueError,TypeError,UnicodeError,RecursionError):
            raise ApiError(400, '유효한 JSON 값과 문자열을 입력해주세요.')
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE'); self.require_schema(c); actor = active(c, user)
            saved = c.execute('SELECT * FROM hand_review_commands WHERE actor_id=? AND request_key=?', (actor['id'],key)).fetchone()
            if saved:
                if saved['command'] != operation or saved['request_hash'] != hashed:
                    raise ApiError(409, '이 원 요청 키는 다른 내용에 사용되었습니다.')
                self.scope(c, actor, saved['review_id'])
                return self.response(c, actor, saved, True)
            target, effect, reason = apply(c, actor)
            insert(c, 'hand_review_events', {'id':new_id('hrevent'),'review_id':target,'version':effect['version'],
                   'action':operation,'actor_id':actor['id'],'reason':reason,'payload':encode(effect),'created_at':self.clock()})
            cid = new_id('hrcommand')
            insert(c, 'hand_review_commands', {'id':cid,'actor_id':actor['id'],'request_key':key,'command':operation,
                   'request_hash':hashed,'review_id':target,'result':encode(effect),'created_at':self.clock()})
            return self.response(c, actor, c.execute('SELECT * FROM hand_review_commands WHERE id=?',(cid,)).fetchone(), False)

    def response(self, c, actor, saved, replayed):
        return {**self.reads.detail_in(c,actor,saved['review_id']),
                'receipt':{'requestKey':saved['request_key'],'command':saved['command'],'reviewId':saved['review_id'],
                           'result':json.loads(saved['result']),'createdAt':saved['created_at']},'replayed':replayed}

    def receipt(self, user, key):
        request_key(key)
        with self.app.db.connect() as c:
            c.execute('BEGIN'); self.require_schema(c); actor = active(c,user)
            row = c.execute('SELECT * FROM hand_review_commands WHERE actor_id=? AND request_key=?',(actor['id'],key)).fetchone()
            if not row: raise ApiError(404,'본인의 원 요청 결과가 없습니다.')
            self.scope(c,actor,row['review_id'])
            return self.response(c,actor,row,True)

    def revision(self, c, actor, rid, kind, payload, reason, identifier=None):
        revision = identifier or new_id('hrrevision')
        number = c.execute('SELECT COALESCE(MAX(revision_no),0)+1 FROM hand_review_revisions WHERE review_id=?',(rid,)).fetchone()[0]
        insert(c,'hand_review_revisions',{'id':revision,'review_id':rid,'revision_no':number,'kind':kind,'payload':encode(payload),
               'payload_hash':digest(payload),'author_id':actor['id'],'reason':reason,'created_at':self.clock()})
        if kind == 'structured':
            for ordinal,a in enumerate(payload['actions'],1):
                insert(c,'hand_review_actions',{'revision_id':revision,'ordinal':ordinal,'street':a['street'],'position':a['position'],
                       'action':a['action'],'amount_milli_bb':a['amountMilliBB'],'note':a['note']})
            for aid in payload['attachmentIds']:
                self.attachments.require_reference(c,rid,aid)
                insert(c,'hand_review_revision_attachments',{'review_id':rid,'revision_id':revision,'attachment_id':aid})
        return revision

    def create(self, user, data):
        shape(data,{'payload','reason','requestKey'},{'payload','reason','requestKey'})
        payload = hand_payload(data['payload']); reason = text_field(data,'reason',minimum=5,maximum=2000)
        def apply(c,actor):
            if payload['attachmentIds']: raise ApiError(400,'먼저 리뷰를 제출한 뒤 이 리뷰에 자료를 연결해주세요.')
            rid = new_id('rev'); at = self.clock(); original = legacy_projection(payload)
            insert(c,'reviews',{'id':rid,'user_id':actor['id'],**original,'status':'접수','reply':'','created_at':at,'updated_at':at})
            row = c.execute('SELECT * FROM reviews WHERE id=?',(rid,)).fetchone()
            snapshot = legacy_snapshot(row); revision = new_id('hrrevision')
            insert(c,'hand_review_heads',{'review_id':rid,'baseline':encode(snapshot),'baseline_hash':digest(snapshot),
                   'revision_id':revision,'state':'submitted','version':1,'created_at':at,'updated_at':at})
            # Deferred same-review FK permits a fully linked initial revision.
            self.revision(c,actor,rid,'structured',payload,reason,revision)
            head = c.execute('SELECT * FROM hand_review_heads WHERE review_id=?',(rid,)).fetchone()
            return rid,self.effect(head),reason
        return self.command(user,'create',None,data,apply)

    def adopt(self, user, rid, data):
        shape(data,{'legacyHash','reason','requestKey'},{'legacyHash','reason','requestKey'})
        reason = text_field(data,'reason',minimum=5,maximum=2000)
        def apply(c,actor):
            actor,row,head = self.scope(c,actor,rid)
            if head: raise ApiError(409,'이미 연결된 리뷰입니다. 현재 판본을 확인해주세요.')
            snapshot = legacy_snapshot(row)
            if data['legacyHash'] != digest(snapshot): raise ApiError(409,'기존 원문이 변경되었습니다. 다시 확인해주세요.')
            state = LEGACY_STATES.get(row['status'])
            if not state: raise ApiError(409,'기존 리뷰의 진행 기준을 운영자가 확인해야 합니다.')
            revision = new_id('hrrevision'); response = new_id('hrresponse') if row['reply'] or state=='answered' else None
            at = self.clock()
            insert(c,'hand_review_heads',{'review_id':rid,'baseline':encode(snapshot),'baseline_hash':digest(snapshot),
                   'revision_id':revision,'response_id':response,'state':state,'version':1,'created_at':at,'updated_at':at})
            self.revision(c,actor,rid,'legacy',snapshot,reason,revision)
            if response:
                insert(c,'hand_review_responses',{'id':response,'review_id':rid,'revision_id':revision,'kind':'legacy',
                       'body':row['reply'],'created_at':at})
            head = c.execute('SELECT * FROM hand_review_heads WHERE review_id=?',(rid,)).fetchone()
            return rid,self.effect(head),reason
        return self.command(user,'adopt',rid,data,apply)

    def change(self, user, rid, action, data):
        fields = {'version','reason','requestKey'} | ({'payload'} if action=='revise' else {'coachId'} if action=='assign' else {'body'} if action=='answer' else set())
        shape(data,fields,fields); reason = text_field(data,'reason',minimum=5,maximum=2000)
        payload = hand_payload(data['payload']) if action=='revise' else None
        body = text_field(data,'body',minimum=5,maximum=10000) if action=='answer' else None
        def apply(c,actor):
            actor,row,head = self.scope(c,actor,rid); self.managed(head); expected(data,head)
            if head['state'] in ('answered','cancelled') and action!='unassign': raise ApiError(409,'종료된 리뷰는 다시 진행할 수 없습니다.')
            changes = {}
            if action=='revise':
                if actor['id'] != row['user_id']: raise ApiError(403,'본인의 제출 내용만 보완할 수 있습니다.')
                if head['state'] not in ('submitted','needs_information'): raise ApiError(409,'진행 중인 검토는 보완 요청을 먼저 확인해주세요.')
                self.member_active(c,row)
                changes = {'revision_id':self.revision(c,actor,rid,'structured',payload,reason),'state':'submitted','response_id':None}
            elif action in ('assign','unassign'):
                if actor['role']!='admin': raise ApiError(403,'담당 배정은 운영자가 처리합니다.')
                if action=='assign': self.member_active(c,row)
                assignment = None
                if action=='assign':
                    coach = data['coachId']
                    profile = c.execute('SELECT * FROM coach_profiles WHERE id=?',(coach,)).fetchone() if isinstance(coach,str) else None
                    if not available(c,profile) or not profile['owner_user_id'] or not owner_eligible(c,profile['owner_user_id']) or profile['owner_user_id']==row['user_id']:
                        raise ApiError(409,'현재 검토 가능한 다른 담당 코치를 선택해주세요.')
                    assignment = new_id('hrassignment')
                    insert(c,'hand_review_assignments',{'id':assignment,'review_id':rid,'coach_id':coach,'owner_id':profile['owner_user_id'],
                           'profile_revision_id':profile['public_revision_id'],'profile_version':profile['public_version'],
                           'actor_id':actor['id'],'reason':reason,'created_at':self.clock()})
                elif not head['assignment_id']: raise ApiError(409,'현재 배정된 담당자가 없습니다.')
                changes = {'assignment_id':assignment,'state':'submitted' if head['state']=='in_review' else head['state']}
            elif action=='cancel':
                if actor['id']!=row['user_id'] and actor['role']!='admin': self.operator(c,actor,row,head)
                changes = {'state':'cancelled'}
            else:
                self.operator(c,actor,row,head); self.member_active(c,row)
                if action=='start':
                    if head['state']!='submitted': raise ApiError(409,'접수 상태의 리뷰만 검토를 시작할 수 있습니다.')
                    changes = {'state':'in_review'}
                elif action=='request-information':
                    if head['state'] not in ('submitted','in_review'): raise ApiError(409,'현재 보완 요청 가능한 상태를 확인해주세요.')
                    changes = {'state':'needs_information','response_id':None}
                elif action=='answer':
                    if head['state']!='in_review': raise ApiError(409,'현재 검토 중인 원문을 확인한 뒤 답변해주세요.')
                    response = new_id('hrresponse')
                    insert(c,'hand_review_responses',{'id':response,'review_id':rid,'revision_id':head['revision_id'],'assignment_id':head['assignment_id'],
                           'kind':'text','body':body,'author_id':actor['id'],'created_at':self.clock()})
                    changes = {'state':'answered','response_id':response}
                else: raise ApiError(400,'리뷰 처리 종류를 확인해주세요.')
            updated = self.bump(c,head,**changes)
            return rid,self.effect(updated),reason
        return self.command(user,action,rid,data,apply)

    def legacy_guard(self, c, row):
        if c.execute('PRAGMA user_version').fetchone()[0] < 30: return
        if c.execute('SELECT 1 FROM hand_review_heads WHERE review_id=?',(row['id'],)).fetchone():
            raise ApiError(409,'구조화된 리뷰입니다. 현재 원문과 판본을 확인한 뒤 전용 리뷰 기능에서 처리해주세요.')

    def information_request(self, c, head):
        if not head or head['state']!='needs_information': return None
        event = c.execute("SELECT reason,created_at FROM hand_review_events WHERE review_id=? AND action='request-information' ORDER BY version DESC LIMIT 1",(head['review_id'],)).fetchone()
        return {'reason':event['reason'],'createdAt':event['created_at']} if event else None

    def project_legacy(self, c, row):
        result = dict(row)
        if c.execute('PRAGMA user_version').fetchone()[0] < 30: return result
        head = c.execute('SELECT * FROM hand_review_heads WHERE review_id=?',(row['id'],)).fetchone()
        if not head: return result
        revision = c.execute('SELECT * FROM hand_review_revisions WHERE id=?',(head['revision_id'],)).fetchone()
        if revision['kind']=='structured': result.update(legacy_projection(json.loads(revision['payload'])))
        result['status'] = STATES[head['state']]; result['updated_at'] = head['updated_at']
        response = c.execute('SELECT body FROM hand_review_responses WHERE id=?',(head['response_id'],)).fetchone() if head['response_id'] else None
        result['reply'] = response['body'] if response else ''
        result['reviewContext'] = {'managed':True,'version':head['version'],'state':head['state'],'revisionId':head['revision_id'],
                                   'informationRequest':self.information_request(c,head)}
        return result
