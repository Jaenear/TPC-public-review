"""Reviewed coaching products/calendars and actor-scoped command receipts."""

import json
import re

from ..core import ApiError, new_id, now_iso, text_field
from .common import audit, require_role, version
from .profiles import managed, available, owner_eligible
from .coaching_contract import (
    active, shape, encode, digest, product_payload, calendar_payload,
    context_matches, bounded_query, integer,
)

KINDS = {
    'product': ('coaching_products','coaching_product_revisions','product_id','id',product_payload,'cproductrevision'),
    'calendar': ('coaching_calendars','coaching_availability_revisions','coach_id','coach_id',calendar_payload,'cavailability'),
}


class CoachingService:
    def __init__(self, app):
        self.app = app
        from .coaching_slots import CoachingSlots
        self.slots = CoachingSlots(self)

    def profile(self, c, user, coach_id):
        actor, profile = managed(c,user,coach_id)
        return actor, profile

    def root(self,c,kind,entity_id):
        table,_,_,key,_,_=KINDS[kind]
        return c.execute('SELECT * FROM '+table+' WHERE '+key+'=?',(entity_id,)).fetchone()

    def load(self,c,user,kind,entity_id):
        root=self.root(c,kind,entity_id)
        if not root:
            raise ApiError(404,'코칭 상품 또는 일정을 찾을 수 없습니다.')
        actor,profile=self.profile(c,user,root['coach_id'])
        return actor,profile,root

    def revision(self,c,kind,entity_id,rid):
        _,table,parent,_,_,_=KINDS[kind]
        row=c.execute('SELECT * FROM '+table+' WHERE id=? AND '+parent+'=?',(rid,entity_id)).fetchone()
        if not row:
            raise ApiError(404,'이 상품/일정의 변경안을 찾을 수 없습니다.')
        return row

    def payload(self,kind,row):
        try:
            value=json.loads(row['payload'])
            normalized=KINDS[kind][4](value)
            if normalized!=value or digest(value)!=row['payload_hash']:
                raise ValueError('content mismatch')
            return value
        except (ValueError,TypeError,KeyError,ApiError):
            raise ApiError(409,'검수 원문의 무결성을 다시 확인해주세요.')

    def revision_view(self,kind,row,admin):
        value={
            'id':row['id'],'status':row['status'],'payload':self.payload(kind,row),
            'payloadHash':row['payload_hash'],'version':row['version'],
            'basePublicVersion':row['base_public_version'],'profileRevisionId':row['profile_revision_id'],
            'profilePublicVersion':row['profile_version'],'reason':row['reason'],'reviewNote':row['review_note'],
            'createdAt':row['created_at'],'updatedAt':row['updated_at'],
            'submittedAt':row['submitted_at'],'reviewedAt':row['reviewed_at'],
        }
        if admin:
            value['approvalReference']=row['approval_reference']
        return value

    def profile_context(self,profile):
        return {'coachId':profile['id'],'profileVersion':profile['version'],
                'profilePublicVersion':profile['public_version'],'profileRevisionId':profile['public_revision_id']}

    def detail_in(self,c,actor,profile,kind,root):
        table,revisions,parent,key,_,_=KINDS[kind]
        entity_id=root[key]
        history=c.execute('SELECT * FROM '+revisions+' WHERE '+parent+'=? ORDER BY rowid DESC LIMIT 40',(entity_id,)).fetchall()
        current=next((r for r in history if r['id']==root['public_revision_id']),None)
        if root['public_revision_id'] and current is None:
            current=self.revision(c,kind,entity_id,root['public_revision_id'])
        root_view={'id':entity_id,'coachId':profile['id'],'state':root['state'],'version':root['version'],
                   'publicVersion':root['public_version'],'publicRevisionId':root['public_revision_id'],
                   'publicContextCurrent':context_matches(c,profile,current),
                   'createdAt':root['created_at'],'updatedAt':root['updated_at']}
        event_key='product_id' if kind=='product' else 'calendar_coach_id'
        events=c.execute('SELECT * FROM coaching_events WHERE '+event_key+'=? ORDER BY rowid DESC LIMIT 80',(entity_id,)).fetchall()
        return {kind:root_view,'context':self.profile_context(profile),
                'revisions':[self.revision_view(kind,r,actor['role']=='admin') for r in history],
                'events':[{'id':r['id'],'action':r['action'],'revisionId':r['revision_id'],'version':r['entity_version'],
                           'note':r['note'],'details':json.loads(r['details']),'createdAt':r['created_at']} for r in events],
                'scope':{'kind':'candidate_catalog','reservationEnabled':False,'paymentEnabled':False,'commercialAcceptance':'pending'}}

    def detail(self,user,kind,entity_id):
        with self.app.db.connect() as c:
            c.execute('BEGIN')
            actor,profile,root=self.load(c,user,kind,entity_id)
            return self.detail_in(c,actor,profile,kind,root)

    def calendar(self,user,coach_id):
        with self.app.db.connect() as c:
            c.execute('BEGIN')
            actor,profile=self.profile(c,user,coach_id)
            root=self.root(c,'calendar',coach_id)
            return self.detail_in(c,actor,profile,'calendar',root) if root else {'calendar':None,'context':self.profile_context(profile)}

    def listing(self,user,query):
        q=bounded_query(query,{'coachId','page'})
        try:page=int(q.get('page','1'))
        except ValueError:raise ApiError(400,'페이지 번호를 확인해주세요.')
        if not 1<=page<=10000:raise ApiError(400,'페이지 번호를 확인해주세요.')
        with self.app.db.connect() as c:
            c.execute('BEGIN');actor=active(c,user,True)
            conditions=[];args=[]
            if actor['role']!='admin':conditions.append('p.owner_user_id=?');args.append(actor['id'])
            if q.get('coachId'):
                self.profile(c,actor,q['coachId']);conditions.append('x.coach_id=?');args.append(q['coachId'])
            clause=' FROM coaching_products x JOIN coach_profiles p ON p.id=x.coach_id'+(' WHERE '+' AND '.join(conditions) if conditions else '')
            total=c.execute('SELECT COUNT(*)'+clause,args).fetchone()[0]
            rows=c.execute('SELECT x.*'+clause+' ORDER BY x.created_at DESC,x.id LIMIT 20 OFFSET ?',args+[(page-1)*20]).fetchall()
            return {'products':[{'id':r['id'],'coachId':r['coach_id'],'state':r['state'],'version':r['version'],
                                 'publicVersion':r['public_version'],'publicRevisionId':r['public_revision_id']} for r in rows],
                    'page':page,'pageSize':20,'total':total}

    def event(self,c,actor,kind,entity_id,action,rid,note,details=None):
        root=self.root(c,kind,entity_id)
        c.execute('INSERT INTO coaching_events VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (new_id('coachingevent'),entity_id if kind=='product' else None,entity_id if kind=='calendar' else None,
                   actor['id'],action,rid,root['version'],note,encode(details or {}),now_iso()))
        audit(c,actor,'coaching.'+kind+'.'+action,'coaching_'+kind,entity_id,{'revisionId':rid,'version':root['version']})

    def bump(self,c,kind,entity_id):
        table,_,_,key,_,_=KINDS[kind]
        c.execute('UPDATE '+table+' SET version=version+1,updated_at=? WHERE '+key+'=?',(now_iso(),entity_id))

    def command(self,user,kind,target,name,data,mutation,*,initial=False,coach_id=None):
        # Connections and all operation state remain request-local; the service
        # is shared by ThreadingHTTPServer and must never store a live cursor.
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if initial:
                actor,profile=self.profile(c,user,coach_id or target);root=self.root(c,kind,target) if kind=='calendar' else None
            else:
                actor,profile,root=self.load(c,user,kind,target)
            key=text_field(data,'requestKey',minimum=8,maximum=128)
            if not re.fullmatch(r'[A-Za-z0-9_-]+',key):raise ApiError(400,'원 요청 키를 확인해주세요.')
            try:request_hash=digest({'command':name,'target':target,'body':{k:v for k,v in data.items() if k!='requestKey'}})
            except (ValueError,TypeError):raise ApiError(400,'유효한 JSON 요청 값을 입력해주세요.')
            old=c.execute('SELECT * FROM coaching_commands WHERE actor_id=? AND request_key=?',(actor['id'],key)).fetchone()
            if old:
                if old['command']!=name or old['entity_type']!=kind or old['request_hash']!=request_hash:
                    raise ApiError(409,'같은 원 요청 키를 다른 작업에 재사용할 수 없습니다.')
                actor,profile,current=self.load(c,actor,kind,old['entity_id'])
                return {**self.detail_in(c,actor,profile,kind,current),'receipt':self.receipt_view(old),'replayed':True}
            if not (initial and root is None):version(data,root)
            elif kind=='calendar' and (type(data.get('version')) is not int or data['version']!=0):
                raise ApiError(409,'처음 만드는 일정의 기준 버전은0입니다.')
            info=mutation(c,actor,profile,root)
            entity_id=info['entityId'];rid=info.get('revisionId')
            self.event(c,actor,kind,entity_id,name,rid,text_field(data,'reason',minimum=3,maximum=1000),info)
            receipt_id=new_id('coachingcommand')
            c.execute('INSERT INTO coaching_commands VALUES(?,?,?,?,?,?,?,?,?)',
                      (receipt_id,actor['id'],key,name,kind,entity_id,request_hash,encode(info),now_iso()))
            current=self.root(c,kind,entity_id)
            result={**self.detail_in(c,actor,profile,kind,current),'receipt':self.receipt_view(c.execute('SELECT * FROM coaching_commands WHERE id=?',(receipt_id,)).fetchone()),'replayed':False}
            return result

    def receipt_view(self,row):
        return {'id':row['id'],'requestKey':row['request_key'],'command':row['command'],'entityType':row['entity_type'],
                'entityId':row['entity_id'],'result':json.loads(row['result']),'createdAt':row['created_at']}

    def receipt(self,user,key):
        with self.app.db.connect() as c:
            c.execute('BEGIN');actor=active(c,user,True)
            row=c.execute('SELECT * FROM coaching_commands WHERE actor_id=? AND request_key=?',(actor['id'],key)).fetchone()
            if not row:raise ApiError(404,'본인의 원 요청 영수증을 찾을 수 없습니다.')
            actor,profile,root=self.load(c,actor,row['entity_type'],row['entity_id'])
            return {**self.detail_in(c,actor,profile,row['entity_type'],root),'receipt':self.receipt_view(row),'replayed':True}

    def check_profile(self,c,profile,data):
        if type(data.get('profileVersion')) is not int or data['profileVersion']!=profile['version']:
            raise ApiError(409,'코치 담당/소개 기준이 바뀌었습니다. 현재 프로필을 다시 확인해주세요.')
        if not available(c,profile) or not owner_eligible(c,profile['owner_user_id']):
            raise ApiError(409,'현재 담당자와 공개 소개가 있는 활성 코치부터 확인해주세요.')

    def insert_revision(self,c,kind,root,actor,profile,payload,reason):
        _,table,parent,key,normalize,prefix=KINDS[kind]
        entity_id=root[key]
        if c.execute('SELECT 1 FROM '+table+' WHERE '+parent+"=? AND status IN ('draft','submitted')",(entity_id,)).fetchone():
            raise ApiError(409,'진행 중인 변경안을 먼저 저장·회수하거나 검수해주세요.')
        value=normalize(payload);rid=new_id(prefix);at=now_iso()
        c.execute('INSERT INTO '+table+'(id,'+parent+',status,payload,payload_hash,base_public_version,profile_revision_id,profile_version,owner_id,created_by,reason,created_at,updated_at) VALUES(?,?,\'draft\',?,?,?,?,?,?,?,?,?,?)',
                  (rid,entity_id,encode(value),digest(value),root['public_version'],profile['public_revision_id'],profile['public_version'],profile['owner_user_id'],actor['id'],reason,at,at))
        return rid

    def create(self,user,data):
        shape(data,{'coachId','profileVersion','payload','reason','requestKey'},{'coachId','profileVersion','payload','reason','requestKey'})
        coach_id=text_field(data,'coachId',maximum=100)
        def mutate(c,actor,profile,root):
            self.check_profile(c,profile,data)
            if c.execute('SELECT COUNT(*) FROM coaching_products WHERE coach_id=?',(coach_id,)).fetchone()[0]>=200:
                raise ApiError(409,'코치별 상품 한도를 확인해주세요.')
            pid=new_id('cproduct');at=now_iso()
            c.execute('INSERT INTO coaching_products(id,coach_id,created_by,created_at,updated_at) VALUES(?,?,?,?,?)',(pid,coach_id,actor['id'],at,at))
            root=self.root(c,'product',pid)
            rid=self.insert_revision(c,'product',root,actor,profile,data['payload'],text_field(data,'reason',minimum=3,maximum=1000))
            return {'entityId':pid,'revisionId':rid,'version':1,'publicVersion':0}
        return self.command(user,'product',coach_id,'product.create',data,mutate,initial=True,coach_id=coach_id)

    def new_revision(self,user,kind,entity_id,data):
        shape(data,{'version','profileVersion','payload','reason','requestKey'},{'version','profileVersion','payload','reason','requestKey'})
        def mutate(c,actor,profile,root):
            self.check_profile(c,profile,data)
            if root is None:
                at=now_iso();c.execute('INSERT INTO coaching_calendars(coach_id,created_at,updated_at) VALUES(?,?,?)',(entity_id,at,at));root=self.root(c,kind,entity_id)
            else:self.bump(c,kind,entity_id)
            rid=self.insert_revision(c,kind,root,actor,profile,data['payload'],text_field(data,'reason',minimum=3,maximum=1000))
            return {'entityId':entity_id,'revisionId':rid,'version':self.root(c,kind,entity_id)['version'],'publicVersion':root['public_version']}
        return self.command(user,kind,entity_id,kind+'.draft',data,mutate,initial=kind=='calendar')

    def change(self,user,kind,entity_id,rid,data,save=False):
        required={'version','revisionVersion','reason','requestKey'}|({'payload','profileVersion'} if save else {'action'})
        allowed=required|({'approvalReference','reviewNote'} if not save else set())
        shape(data,allowed,required)
        action='save' if save else data['action']
        if not isinstance(action,str) or action not in {'save','submit','withdraw','approve','reject'}:raise ApiError(400,'변경안 처리 종류를 확인해주세요.')
        def mutate(c,actor,profile,root):
            row=self.revision(c,kind,entity_id,rid)
            if type(data['revisionVersion']) is not int or data['revisionVersion']!=row['version']:
                raise ApiError(409,'다른 변경안 판본입니다. 현재 원문을 다시 확인해주세요.')
            table=KINDS[kind][1];at=now_iso()
            if save:
                if row['status']!='draft':raise ApiError(409,'검수 중이거나 끝난 원문은 수정할 수 없습니다.')
                self.check_profile(c,profile,data);payload=KINDS[kind][4](data['payload'])
                c.execute('UPDATE '+table+' SET payload=?,payload_hash=?,base_public_version=?,profile_revision_id=?,profile_version=?,owner_id=?,reason=?,version=version+1,updated_at=? WHERE id=?',
                          (encode(payload),digest(payload),root['public_version'],profile['public_revision_id'],profile['public_version'],profile['owner_user_id'],text_field(data,'reason',minimum=3,maximum=1000),at,rid))
            else:
                if action in ('approve','reject'):require_role(c,actor,'admin')
                expected='draft' if action=='submit' else 'submitted'
                if row['status']!=expected:raise ApiError(409,'현재 변경안 상태에서 처리할 수 없습니다.')
                if action in ('submit','approve'):
                    self.payload(kind,row)
                    if row['base_public_version']!=root['public_version'] or not context_matches(c,profile,row):
                        raise ApiError(409,'공개 상품/일정 또는 담당 소개가 바뀌었습니다. 회수 후 현재 기준으로 다시 저장해주세요.')
                status={'submit':'submitted','withdraw':'draft','approve':'approved','reject':'rejected'}[action]
                note=text_field(data,'reviewNote',maximum=2000,optional=True) if action in ('approve','reject') else ''
                ref=text_field(data,'approvalReference',minimum=3,maximum=300) if action=='approve' else ''
                c.execute('UPDATE '+table+' SET status=?,version=version+1,reason=?,review_note=?,approval_reference=?,reviewed_by=?,submitted_at=?,reviewed_at=?,updated_at=? WHERE id=?',
                          (status,text_field(data,'reason',minimum=3,maximum=1000),note,ref,actor['id'] if action in ('approve','reject') else None,
                           at if action=='submit' else row['submitted_at'],at if action in ('approve','reject') else None,at,rid))
                if action=='approve':
                    root_table,_,_,root_key,_,_=KINDS[kind]
                    c.execute('UPDATE '+root_table+' SET public_revision_id=?,public_version=public_version+1,state=\'active\' WHERE '+root_key+'=?',(rid,entity_id))
            self.bump(c,kind,entity_id)
            updated=self.root(c,kind,entity_id)
            return {'entityId':entity_id,'revisionId':rid,'version':updated['version'],'publicVersion':updated['public_version'],'revisionVersion':row['version']+1}
        return self.command(user,kind,entity_id,kind+'.'+action+':'+rid,data,mutate)

    def state(self,user,kind,entity_id,data):
        shape(data,{'version','state','reason','requestKey'},{'version','state','reason','requestKey'})
        if data['state'] not in ('active','paused'):raise ApiError(400,'활성/일시 중지 상태를 확인해주세요.')
        def mutate(c,actor,profile,root):
            if root['state']==data['state']:raise ApiError(409,'이미 같은 상태입니다. 현재 상태를 확인해주세요.')
            if data['state']=='active':
                require_role(c,actor,'admin')
                if not root['public_revision_id'] or not context_matches(c,profile,self.revision(c,kind,entity_id,root['public_revision_id'])):
                    raise ApiError(409,'현재 코치 기준에 맞는 공개 검수본부터 확인해주세요.')
            table,_,_,key,_,_=KINDS[kind]
            c.execute('UPDATE '+table+' SET state=? WHERE '+key+'=?',(data['state'],entity_id));self.bump(c,kind,entity_id)
            return {'entityId':entity_id,'version':root['version']+1,'publicVersion':root['public_version'],'state':data['state']}
        return self.command(user,kind,entity_id,kind+'.state',data,mutate)

    def history(self,user,kind,entity_id,query):
        q=bounded_query(query,{'page'})
        try:page=int(q.get('page','1'))
        except ValueError:raise ApiError(400,'페이지 번호를 확인해주세요.')
        if not 1<=page<=10000:raise ApiError(400,'페이지 번호를 확인해주세요.')
        with self.app.db.connect() as c:
            c.execute('BEGIN');actor,_,_=self.load(c,user,kind,entity_id)
            _,table,parent,_,_,_=KINDS[kind]
            rows=c.execute('SELECT * FROM '+table+' WHERE '+parent+'=? ORDER BY rowid DESC LIMIT 20 OFFSET ?',(entity_id,(page-1)*20)).fetchall()
            return {'revisions':[self.revision_view(kind,r,actor['role']=='admin') for r in rows], 'page':page,'pageSize':20,
                    'total':c.execute('SELECT COUNT(*) FROM '+table+' WHERE '+parent+'=?',(entity_id,)).fetchone()[0]}

    def route(self,h,path,method,query):
        if not path.startswith(('/api/coaching/','/api/studio/coaching/')):return None
        user=h.user
        if path=='/api/coaching/products' and method=='GET':return 200,self.slots.products(query),{}
        match=re.fullmatch(r'/api/coaching/products/(cproduct_[a-f0-9]{32})',path)
        if match and method=='GET':return 200,self.slots.product(match[1]),{}
        if path=='/api/coaching/slots' and method=='GET':return 200,self.slots.listing(query),{}
        match=re.fullmatch(r'/api/coaching/products/(cproduct_[a-f0-9]{32})/selection',path)
        if match and method=='POST':return 200,self.slots.selection(user,match[1],h.body()),{}
        if path=='/api/studio/coaching/products':
            if method=='GET':return 200,self.listing(user,query),{}
            if method=='POST':return 201,self.create(user,h.body()),{}
        match=re.fullmatch(r'/api/studio/coaching/commands/([A-Za-z0-9_-]{8,128})',path)
        if match and method=='GET':return 200,self.receipt(user,match[1]),{}
        match=re.fullmatch(r'/api/studio/coaching/(products/(cproduct_[a-f0-9]{32})|calendars/([a-z0-9-]+))(?:/(revisions)(?:/([a-z0-9_]+))?|/(state|generate))?',path)
        if match:
            kind='product' if match[2] else 'calendar';entity_id=match[2] or match[3];rid=match[5];action=match[6]
            if not match[4] and not action and method=='GET':return 200,(self.calendar(user,entity_id) if kind=='calendar' else self.detail(user,kind,entity_id)),{}
            if match[4] and not rid:
                if method=='GET':return 200,self.history(user,kind,entity_id,query),{}
                if method=='POST':return 201,self.new_revision(user,kind,entity_id,h.body()),{}
            if rid and method in ('PATCH','POST'):return 200,self.change(user,kind,entity_id,rid,h.body(),save=method=='PATCH'),{}
            if rid and method=='GET':
                with self.app.db.connect() as c:
                    actor,_,_=self.load(c,user,kind,entity_id)
                    return 200,{'revision':self.revision_view(kind,self.revision(c,kind,entity_id,rid),actor['role']=='admin')},{}
            if action=='state' and method=='POST':return 200,self.state(user,kind,entity_id,h.body()),{}
            if action=='generate' and kind=='product' and method=='POST':return 200,self.slots.generate(user,entity_id,h.body()),{}
        raise ApiError(405,'지원하는 코칭 경로와 요청 방식을 확인해주세요.')
