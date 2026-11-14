"""Review-only bytes and current reference ACL; operating policy is injected."""
import hashlib
import re
import socket
import time
from urllib.parse import quote, unquote

from ..core import ApiError, new_id, text_field
from .discussion_attachments import AttachmentProfile, FORMATS, validate_body
from .hand_review_contract import shape, expected, request_key, digest, bounded_query
from .hand_review_queries import page
from .hand_reviews import insert


class HandReviewAttachments:
    def __init__(self, model):
        self.m = model
        self.profile = None
        self.scanner = None
        self.clock = time.time

    def configured(self):
        return isinstance(self.profile,AttachmentProfile) and callable(getattr(self.scanner,'inspect',None))

    def policy(self):
        if not self.configured():
            raise ApiError(409,'첨부 형식·보관 기준(DEC-09)과 검사 연결을 준비 중입니다. 리뷰 본문은 이용할 수 있습니다.')
        return self.profile

    def public_policy(self):
        if not self.configured(): return None
        profile = self.profile
        return {'formats':[{'suffix':suffix,'mediaType':FORMATS[suffix]} for suffix in profile.suffixes],
                'maxBytes':profile.max_bytes,'maxTotalBytes':profile.max_total_bytes,
                'maxPending':profile.max_pending,'pendingSeconds':profile.pending_seconds,
                'policyHash':profile.identity()}

    def asset(self, c, rid, aid):
        row = c.execute('SELECT a.*,s.state,s.version,s.inspection_reference,p.expires_at FROM hand_review_attachments a JOIN hand_review_attachment_states s ON s.attachment_id=a.id LEFT JOIN hand_review_attachment_payloads p ON p.attachment_id=a.id WHERE a.review_id=? AND a.id=?',(rid,aid)).fetchone()
        if not row: raise ApiError(404,'현재 리뷰의 첨부 원문이 없습니다.')
        return row

    def approved(self, row):
        return bool(row['state']=='approved' and row['expires_at'] is not None and self.configured() and row['policy_hash']==self.profile.identity())

    def referenced(self, c, head, aid):
        return bool(head and c.execute('SELECT 1 FROM hand_review_revision_attachments WHERE revision_id=? AND attachment_id=?',(head['revision_id'],aid)).fetchone())

    def metadata(self, c, actor, review, head, row):
        referenced = self.referenced(c,head,row['id'])
        own = actor['id']==review['user_id']; admin = actor['role']=='admin'
        state = 'expired' if row['state']=='pending' and row['expires_at'] is not None and row['expires_at']<=int(self.clock()) else row['state']
        download = self.approved(row) and (own or admin or referenced)
        value = {'id':row['id'],'reviewId':row['review_id'],'filename':row['filename'],'mediaType':row['media_type'],
                 'size':row['size'],'sha256':row['sha256'],'state':state,'storedState':row['state'],'version':row['version'],
                 'referenced':referenced,'canDownload':bool(download),'href':'/api/hand-reviews/'+row['review_id']+'/attachments/'+row['id']+'/file' if download else None,
                 'pendingExpiresAt':row['expires_at'] if row['state']=='pending' else None,'createdAt':row['created_at']}
        pending = row['state']=='pending'; live = row['expires_at'] is not None and row['expires_at']>int(self.clock())
        value['canReference'] = bool(own and head and head['state'] in ('submitted','needs_information') and self.approved(row))
        value['actions'] = {
            'inspect':bool(admin and pending and live and head and head['state'] not in ('answered','cancelled') and self.configured() and row['policy_hash']==self.profile.identity()),
            'block':bool(admin and row['state']=='approved'),
            'delete':bool(admin and row['state'] not in ('deleted','expired')),
            'cancel':bool((own or admin) and pending),
            'expire':bool((own or admin) and pending and row['expires_at'] is not None and not live)}
        if admin: value['inspectionReference']=row['inspection_reference']
        return value

    def rows_clause(self, actor, review, head):
        args = [review['id']]; clause = ''
        if actor['id']!=review['user_id'] and actor['role']!='admin':
            clause=' AND a.id IN(SELECT attachment_id FROM hand_review_revision_attachments WHERE revision_id=?)';args.append(head['revision_id'] if head else None)
        return clause,args

    def total_in(self, c, actor, review, head):
        if not head: return 0
        clause,args = self.rows_clause(actor,review,head)
        return c.execute('SELECT COUNT(*) FROM hand_review_attachments a WHERE a.review_id=?'+clause,args).fetchone()[0]

    def list_in(self, c, actor, review, head, number=1):
        if not head: return []
        clause,args = self.rows_clause(actor,review,head)
        rows = c.execute('SELECT a.*,s.state,s.version,s.inspection_reference,p.expires_at FROM hand_review_attachments a JOIN hand_review_attachment_states s ON s.attachment_id=a.id LEFT JOIN hand_review_attachment_payloads p ON p.attachment_id=a.id WHERE a.review_id=?'+clause+' ORDER BY a.created_at DESC,a.id DESC LIMIT 20 OFFSET ?',args+[(number-1)*20]).fetchall()
        return [self.metadata(c,actor,review,head,r) for r in rows]

    def listing(self, user, rid, query):
        q = bounded_query(query,{'page'});number = page(q)
        with self.m.app.db.connect() as c:
            c.execute('BEGIN');actor,review,head = self.m.scope(c,user,rid)
            return {'scope':self.m.reads.scope(),'reviewId':rid,'version':head['version'] if head else None,'asOf':self.m.clock(),
                    'attachments':self.list_in(c,actor,review,head,number),'total':self.total_in(c,actor,review,head),'page':number,'pageSize':20}

    def detail(self, user, rid, aid):
        with self.m.app.db.connect() as c:
            c.execute('BEGIN');actor,review,head = self.m.scope(c,user,rid);self.m.managed(head);row = self.asset(c,rid,aid)
            if actor['id']!=review['user_id'] and actor['role']!='admin' and not self.referenced(c,head,aid): raise ApiError(404,'현재 연결된 자료가 아닙니다.')
            return {'scope':self.m.reads.scope(),'asOf':self.m.clock(),'reviewId':rid,'version':head['version'],'attachment':self.metadata(c,actor,review,head,row)}

    def require_reference(self, c, rid, aid):
        row = self.asset(c,rid,aid)
        if not self.approved(row): raise ApiError(409,'현재 검수를 통과한 이 리뷰의 자료만 연결할 수 있습니다.')

    def upload_allowed(self, c, actor, review, head):
        self.m.managed(head)
        if actor['id']!=review['user_id']: raise ApiError(403,'본인의 리뷰에만 자료를 제출할 수 있습니다.')
        if head['state'] not in ('submitted','needs_information'): raise ApiError(409,'자료를 보완할 수 있는 현재 상태를 확인해주세요.')
        self.m.member_active(c,review)

    def upload(self, h, user, rid):
        with self.m.app.db.connect() as c:
            actor,review,head = self.m.scope(c,user,rid);self.upload_allowed(c,actor,review,head)
        profile = self.policy()
        required = ('Content-Length','Content-Type','X-File-Name','X-Request-Key','X-Review-Version')
        if any(len(h.headers.get_all(n,[]))!=1 for n in required) or h.headers.get('Transfer-Encoding'):
            raise ApiError(400,'첨부 크기·형식·파일명·원 키·현재 판본을 한 번씩 전송해주세요.')
        length = h.headers['Content-Length']; version = h.headers['X-Review-Version']
        if not re.fullmatch(r'[1-9][0-9]{0,9}',length) or int(length)>profile.max_bytes: raise ApiError(413,'허용된 첨부 크기를 확인해주세요.')
        if not re.fullmatch(r'[1-9][0-9]{0,9}',version): raise ApiError(400,'현재 리뷰 판본을 확인해주세요.')
        version = int(version); length = int(length); key = request_key(h.headers['X-Request-Key'])
        encoded = h.headers['X-File-Name']
        try:
            if len(encoded)>1200 or re.search(r'%(?![0-9A-Fa-f]{2})',encoded): raise ValueError()
            filename = unquote(encoded,errors='strict')
        except (UnicodeError,ValueError): raise ApiError(400,'첨부 파일명을 확인해주세요.')
        if not filename or filename!=filename.strip() or len(filename)>255 or any(ch in filename for ch in '/\\') or any(ord(ch)<32 or ord(ch)==127 for ch in filename):
            raise ApiError(400,'첨부 파일명에 경로나 제어 문자를 사용할 수 없습니다.')
        suffix = '.'+filename.rsplit('.',1)[-1].lower(); mime = h.headers['Content-Type'].strip().lower()
        if suffix not in profile.suffixes or mime!=FORMATS[suffix]: raise ApiError(415,'허용된 TXT/JSON 파일 형식을 확인해주세요.')
        h.connection.settimeout(15)
        try: body = h.rfile.read(length)
        except (OSError,socket.timeout): raise ApiError(400,'파일 전송이 중단되었습니다.')
        if len(body)!=length: raise ApiError(400,'파일 전송이 끝나지 않았습니다.')
        validate_body(body,suffix); sha = hashlib.sha256(body).hexdigest()
        data = {'version':version,'filename':filename,'mediaType':mime,'size':length,'sha256':sha,'policyHash':profile.identity(),'requestKey':key}
        def apply(c,actor):
            actor,review,head = self.m.scope(c,actor,rid);self.upload_allowed(c,actor,review,head);expected(data,head)
            if self.policy()!=profile: raise ApiError(409,'첨부 기준이 바뀌었습니다. 다시 확인해주세요.')
            quota = c.execute("SELECT COALESCE(SUM(CASE WHEN s.state='pending' AND p.expires_at>? THEN 1 ELSE 0 END),0),COALESCE(SUM(p.size),0) FROM hand_review_attachments a JOIN hand_review_attachment_states s ON s.attachment_id=a.id JOIN hand_review_attachment_payloads p ON p.attachment_id=a.id WHERE a.uploader_id=?",(int(self.clock()),actor['id'])).fetchone()
            if quota[0]>=profile.max_pending or quota[1]+length>profile.max_total_bytes: raise ApiError(409,'첨부 보관 한도입니다. 지난 임시 자료를 정리해주세요.')
            aid = new_id('hrattachment'); at = self.m.clock()
            insert(c,'hand_review_attachments',{'id':aid,'review_id':rid,'uploader_id':actor['id'],'filename':filename,'media_type':mime,
                   'size':length,'sha256':sha,'policy_hash':profile.identity(),'created_at':at})
            insert(c,'hand_review_attachment_states',{'attachment_id':aid,'state':'pending','version':1,'updated_at':at})
            insert(c,'hand_review_attachment_payloads',{'attachment_id':aid,'review_id':rid,'body':body,'size':length,'sha256':sha,'expires_at':int(self.clock())+profile.pending_seconds})
            head = self.m.bump(c,head)
            return rid,self.m.effect(head,attachmentId=aid,attachmentVersion=1,attachmentState='pending'),'검사 대기 원본 수신'
        return self.m.command(user,'attachment.upload',rid,data,apply)

    def operation_allowed(self, actor, review, action):
        if action in ('inspect','block','delete') and actor['role']!='admin': raise ApiError(403,'첨부 검수와 차단은 운영자가 처리합니다.')
        if action in ('cancel','expire') and actor['role']!='admin' and actor['id']!=review['user_id']: raise ApiError(403,'본인의 임시 자료만 정리할 수 있습니다.')

    def operate(self, user, rid, aid, action, data):
        shape(data,{'version','attachmentVersion','reason','requestKey'},{'version','attachmentVersion','reason','requestKey'})
        reason = text_field(data,'reason',minimum=5,maximum=2000); request_key(data['requestKey'])
        prepared = {**data,'attachmentId':aid}; operation = 'attachment.'+action; scan = None; profile = None; scanner = None; source_sha = None
        if action=='inspect':
            profile = self.policy(); scanner = self.scanner
            hashed = digest({'operation':operation,'reviewId':rid,'data':prepared})
            with self.m.app.db.connect() as c:
                c.execute('BEGIN');actor,review,head = self.m.scope(c,user,rid);self.m.managed(head);self.operation_allowed(actor,review,action)
                saved = c.execute('SELECT * FROM hand_review_commands WHERE actor_id=? AND request_key=?',(actor['id'],data['requestKey'])).fetchone()
                if saved:
                    if saved['command']!=operation or saved['request_hash']!=hashed: raise ApiError(409,'같은 원 키로 다른 검수를 요청할 수 없습니다.')
                    return self.m.response(c,actor,saved,True)
                expected(data,head); row = self.asset(c,rid,aid); expected({'version':data['attachmentVersion']},row)
                payload = c.execute('SELECT body FROM hand_review_attachment_payloads WHERE attachment_id=?',(aid,)).fetchone()
                if head['state'] in ('answered','cancelled') or row['state']!='pending' or not payload or row['expires_at']<=int(self.clock()) or row['policy_hash']!=profile.identity():
                    raise ApiError(409,'현재 검사 가능한 대기 원본을 확인해주세요.')
                body = payload['body']; source_sha = hashlib.sha256(body).hexdigest()
                if source_sha!=row['sha256'] or len(body)!=row['size']: raise ApiError(409,'검사 원본의 무결성을 확인해주세요.')
                context = {'filename':row['filename'],'mediaType':row['media_type'],'sha256':source_sha,'policyReference':profile.reference,
                           'inspectionKey':digest({'actor':actor['id'],'key':data['requestKey'],'asset':aid,'sha256':source_sha})}
            try: scan = scanner.inspect(body,context)
            except Exception: raise ApiError(503,'첨부 검사를 완료하지 못했습니다. 대기 상태를 유지합니다.')
            if not isinstance(scan,dict) or scan.get('status') not in ('approved','rejected') or not isinstance(scan.get('reference'),str) or not 5<=len(scan['reference'])<=200 or any(ord(ch)<32 for ch in scan['reference']):
                raise ApiError(503,'신뢰한 검사 결과를 확인할 수 없습니다.')
            try: scan['reference'].encode('utf-8')
            except UnicodeError: raise ApiError(503,'검사 참조의 문자열 형식을 확인할 수 없습니다.')
        def apply(c,actor):
            actor,review,head = self.m.scope(c,actor,rid);self.m.managed(head);self.operation_allowed(actor,review,action);expected(data,head)
            row = self.asset(c,rid,aid);expected({'version':data['attachmentVersion']},row)
            state = row['state']; reference = row['inspection_reference']
            if action=='inspect':
                payload = c.execute('SELECT body FROM hand_review_attachment_payloads WHERE attachment_id=?',(aid,)).fetchone()
                if self.policy()!=profile or self.scanner is not scanner or state!='pending' or not payload or row['expires_at']<=int(self.clock()) or head['state'] in ('answered','cancelled') or hashlib.sha256(payload['body']).hexdigest()!=source_sha:
                    raise ApiError(409,'검사 중 원문·권한·첨부 기준이 바뀌었습니다.')
                state = scan['status']; reference = scan['reference']
            elif action=='block':
                if state!='approved': raise ApiError(409,'승인된 자료만 차단할 수 있습니다.')
                state = 'blocked'
            elif action=='delete':
                if state in ('deleted','expired'): raise ApiError(409,'이미 정리된 자료입니다.')
                state = 'deleted'
            elif action=='cancel':
                if state!='pending': raise ApiError(409,'대기 중인 임시 자료만 취소할 수 있습니다.')
                state = 'deleted'
            elif action=='expire':
                if state!='pending' or row['expires_at'] is None or row['expires_at']>int(self.clock()): raise ApiError(409,'보관 기한이 지난 대기 자료만 정리할 수 있습니다.')
                state = 'expired'
            else: raise ApiError(400,'첨부 처리 종류를 확인해주세요.')
            c.execute('UPDATE hand_review_attachment_states SET state=?,version=version+1,inspection_reference=?,updated_at=? WHERE attachment_id=? AND version=?',(state,reference,self.m.clock(),aid,row['version']))
            if state in ('deleted','expired','rejected'): c.execute('DELETE FROM hand_review_attachment_payloads WHERE attachment_id=?',(aid,))
            head = self.m.bump(c,head)
            return rid,self.m.effect(head,attachmentId=aid,attachmentVersion=row['version']+1,attachmentState=state),reason
        return self.m.command(user,operation,rid,prepared,apply)

    def file(self, h, user, rid, aid):
        with self.m.app.db.connect() as c:
            c.execute('BEGIN'); actor,review,head = self.m.scope(c,user,rid);self.m.managed(head)
            row = self.asset(c,rid,aid)
            if not self.metadata(c,actor,review,head,row)['canDownload']: raise ApiError(404,'현재 열람할 수 있는 검수 자료가 없습니다.')
            payload = c.execute('SELECT body FROM hand_review_attachment_payloads WHERE attachment_id=?',(aid,)).fetchone()
            if not payload or len(payload['body'])!=row['size'] or hashlib.sha256(payload['body']).hexdigest()!=row['sha256']: raise ApiError(409,'자료 원본의 무결성을 확인할 수 없습니다.')
            body = payload['body']; mime = row['media_type']; filename = row['filename']
        h.send_response(200);h.send_header('Content-Type',mime);h.send_header('Content-Length',str(len(body)))
        h.send_header('Content-Disposition','attachment; filename="review-material"; filename*=UTF-8\'\''+quote(filename,safe=''))
        h.send_header('Cache-Control','private, no-store');h.send_header('Vary','Cookie');h.security_headers();h.end_headers()
        if h.command!='HEAD': h.wfile.write(body)
