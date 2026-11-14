"""Versioned additive migrations. Existing account and learning rows are retained."""

import sqlite3
from .refund_records import SCHEMA_V24
from .reconciliation_records import SCHEMA_V25
from .reconciliation_runtime_records import SCHEMA_V26
from .reconciliation_desk_records import SCHEMA_V27
from .coaching_records import SCHEMA_V28
from .reservation_records import SCHEMA_V29
from .hand_review_records import SCHEMA_V30
from .mail_records import SCHEMA_V31
from .identity_records import SCHEMA_V32
from .identity_recovery_records import SCHEMA_V33

LATEST_VERSION = 33

SCHEMA_V3 = """
ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE users ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE user_roles(user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,role TEXT NOT NULL CHECK(role IN ('coach','moderator')),PRIMARY KEY(user_id,role));
CREATE TABLE courses(
 id TEXT PRIMARY KEY,title TEXT NOT NULL,subtitle TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',
 category TEXT NOT NULL,level TEXT NOT NULL,price INTEGER NOT NULL CHECK(price>=0),duration TEXT NOT NULL DEFAULT '',
 coach_id TEXT NOT NULL DEFAULT '',instructor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
 accent TEXT NOT NULL DEFAULT 'green',art TEXT NOT NULL DEFAULT 'range',status TEXT NOT NULL DEFAULT 'draft',
 version INTEGER NOT NULL DEFAULT 1,pilot INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
);
CREATE TABLE course_sections(id TEXT PRIMARY KEY,course_id TEXT NOT NULL REFERENCES courses(id),title TEXT NOT NULL,sort_order INTEGER NOT NULL DEFAULT 0);
CREATE TABLE course_lessons(
 course_id TEXT NOT NULL REFERENCES courses(id),id TEXT NOT NULL,section_id TEXT NOT NULL REFERENCES course_sections(id),
 title TEXT NOT NULL,duration TEXT NOT NULL DEFAULT '',free INTEGER NOT NULL DEFAULT 0,content TEXT NOT NULL DEFAULT '[]',
 takeaway TEXT NOT NULL DEFAULT '',quiz TEXT,video_asset_id TEXT,status TEXT NOT NULL DEFAULT 'draft',
 sort_order INTEGER NOT NULL DEFAULT 0,version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 PRIMARY KEY(course_id,id)
);
CREATE TABLE media_assets(id TEXT PRIMARY KEY,course_id TEXT NOT NULL REFERENCES courses(id),uploader_id TEXT REFERENCES users(id) ON DELETE SET NULL,filename TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE enrollment_access(enrollment_id TEXT PRIMARY KEY REFERENCES enrollments(id) ON DELETE CASCADE,status TEXT NOT NULL DEFAULT 'active',source TEXT NOT NULL DEFAULT 'legacy',source_id TEXT,reason TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL);
INSERT INTO enrollment_access(enrollment_id,updated_at) SELECT id,created_at FROM enrollments;
CREATE TABLE lesson_watch(user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,course_id TEXT NOT NULL,lesson_id TEXT NOT NULL,seconds REAL NOT NULL DEFAULT 0,updated_at TEXT NOT NULL,PRIMARY KEY(user_id,course_id,lesson_id));
CREATE TABLE questions(id TEXT PRIMARY KEY,course_id TEXT NOT NULL REFERENCES courses(id),lesson_id TEXT,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,title TEXT NOT NULL,body TEXT NOT NULL,visibility TEXT NOT NULL DEFAULT 'course',status TEXT NOT NULL DEFAULT 'open',accepted_answer_id TEXT,version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX questions_course ON questions(course_id,created_at);
CREATE TABLE answers(id TEXT PRIMARY KEY,question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,body TEXT NOT NULL,official INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
CREATE TABLE boards(id TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',read_access TEXT NOT NULL DEFAULT 'public',write_access TEXT NOT NULL DEFAULT 'member');
CREATE TABLE posts(id TEXT PRIMARY KEY,board_id TEXT NOT NULL REFERENCES boards(id),user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,title TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'published',pinned INTEGER NOT NULL DEFAULT 0,locked INTEGER NOT NULL DEFAULT 0,version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX posts_board ON posts(board_id,status,created_at);
CREATE TABLE comments(id TEXT PRIMARY KEY,post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,body TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'published',created_at TEXT NOT NULL);
CREATE TABLE post_likes(post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,PRIMARY KEY(post_id,user_id));
CREATE TABLE reports(id TEXT PRIMARY KEY,post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',resolution TEXT NOT NULL DEFAULT '',resolved_by TEXT REFERENCES users(id) ON DELETE SET NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(post_id,user_id));
CREATE TABLE notifications(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,title TEXT NOT NULL,body TEXT NOT NULL DEFAULT '',href TEXT NOT NULL,read_at TEXT,created_at TEXT NOT NULL);
CREATE INDEX notifications_owner ON notifications(user_id,read_at,created_at);
CREATE TABLE audit_logs(id TEXT PRIMARY KEY,actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,action TEXT NOT NULL,target_type TEXT NOT NULL,target_id TEXT NOT NULL,details TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);
CREATE TABLE purchase_orders(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,course_id TEXT NOT NULL REFERENCES courses(id),title TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),currency TEXT NOT NULL DEFAULT 'KRW',mode TEXT NOT NULL CHECK(mode IN ('test','live')),status TEXT NOT NULL DEFAULT 'created',payment_key TEXT UNIQUE,provider_status TEXT,refund_reason TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,paid_at TEXT);
CREATE INDEX purchase_owner ON purchase_orders(user_id,created_at);
CREATE TABLE refund_requests(id TEXT PRIMARY KEY,order_id TEXT NOT NULL UNIQUE REFERENCES purchase_orders(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'requested',resolution TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE payment_events(id TEXT PRIMARY KEY,order_id TEXT NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,event_type TEXT NOT NULL,status TEXT NOT NULL,provider_status TEXT,created_at TEXT NOT NULL);
INSERT INTO boards(id,title,description,read_access,write_access) VALUES
 ('announcements','공지사항','캠프와 강의 운영 소식을 확인하세요.','public','staff'),
 ('general','자유 이야기','플레이 경험과 학습 루틴을 함께 나눕니다.','public','member'),
 ('study','스터디 모임','함께 공부할 주제와 동료를 찾아보세요.','member','member'),
 ('hand-discussion','핸드 토론','한 핸드의 서로 다른 관점을 나누는 회원 게시판입니다.','member','member');
INSERT INTO schema_migrations VALUES(3,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=3;
"""

SCHEMA_V4 = """
CREATE TABLE lesson_revisions(
 id TEXT PRIMARY KEY,course_id TEXT NOT NULL,lesson_id TEXT NOT NULL,
 author_id TEXT REFERENCES users(id) ON DELETE SET NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','approved','rejected','cancelled')),
 base_version INTEGER NOT NULL,base_snapshot TEXT NOT NULL,payload TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '',review_note TEXT NOT NULL DEFAULT '',
 reviewer_id TEXT REFERENCES users(id) ON DELETE SET NULL,
 version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,submitted_at TEXT,reviewed_at TEXT,
 FOREIGN KEY(course_id,lesson_id) REFERENCES course_lessons(course_id,id)
);
CREATE UNIQUE INDEX one_active_lesson_revision ON lesson_revisions(course_id,lesson_id) WHERE status IN ('draft','submitted');
CREATE INDEX revisions_queue ON lesson_revisions(status,updated_at);
CREATE TABLE lesson_history(
 id TEXT PRIMARY KEY,course_id TEXT NOT NULL,lesson_id TEXT NOT NULL,lesson_version INTEGER NOT NULL,
 snapshot TEXT NOT NULL,actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
 source TEXT NOT NULL CHECK(source IN ('baseline','direct','revision')),
 revision_id TEXT REFERENCES lesson_revisions(id),reason TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,
 UNIQUE(course_id,lesson_id,lesson_version),
 FOREIGN KEY(course_id,lesson_id) REFERENCES course_lessons(course_id,id)
);
CREATE TABLE revision_events(
 id TEXT PRIMARY KEY,revision_id TEXT NOT NULL REFERENCES lesson_revisions(id),
 actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,action TEXT NOT NULL,status TEXT NOT NULL,
 version INTEGER NOT NULL,note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX one_publication_per_revision ON lesson_history(revision_id) WHERE revision_id IS NOT NULL;
CREATE INDEX revision_event_order ON revision_events(revision_id,version);
INSERT INTO schema_migrations VALUES(4,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=4;
"""


SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS rate_limit_config(
 id INTEGER PRIMARY KEY CHECK(id=1),key_secret BLOB NOT NULL CHECK(length(key_secret)=32)
);
INSERT OR IGNORE INTO rate_limit_config VALUES(1,randomblob(32));
CREATE TABLE IF NOT EXISTS rate_limit_events(
 id INTEGER PRIMARY KEY,key_hash TEXT NOT NULL,expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_limit_key_expiry ON rate_limit_events(key_hash,expires_at);
CREATE INDEX IF NOT EXISTS rate_limit_expiry ON rate_limit_events(expires_at);
INSERT OR IGNORE INTO schema_migrations VALUES(5,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=5;
"""


SCHEMA_V6 = """
CREATE TABLE IF NOT EXISTS enrollment_grants(
 id TEXT PRIMARY KEY,
 enrollment_id TEXT NOT NULL REFERENCES enrollments(id) ON DELETE CASCADE,
 source TEXT NOT NULL CHECK(source IN ('legacy','free','pilot','payment','manual')),
 source_id TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked')),
 starts_at TEXT,expires_at TEXT,
 reason TEXT NOT NULL DEFAULT '',revocation_reason TEXT NOT NULL DEFAULT '',revoked_at TEXT,
 created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
 updated_by TEXT REFERENCES users(id) ON DELETE SET NULL,
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(enrollment_id,source,source_id),
 CHECK(starts_at IS NULL OR expires_at IS NULL OR expires_at>starts_at)
);
CREATE INDEX IF NOT EXISTS grants_enrollment ON enrollment_grants(enrollment_id,status,starts_at,expires_at);
CREATE INDEX IF NOT EXISTS grants_source ON enrollment_grants(source,source_id);
INSERT OR IGNORE INTO enrollment_grants(
 id,enrollment_id,source,source_id,status,reason,revocation_reason,revoked_at,created_at,updated_at
)
SELECT 'grant_migrated_'||e.id,e.id,
 CASE WHEN a.source IN ('legacy','free','pilot','payment','manual') THEN a.source ELSE 'legacy' END,
 COALESCE(a.source_id,''),
 CASE WHEN a.status IS NULL OR a.status='active' THEN 'active' ELSE 'revoked' END,
 CASE WHEN a.status='revoked' THEN '' ELSE COALESCE(a.reason,'') END,
 CASE WHEN a.status='revoked' THEN a.reason ELSE '' END,
 CASE WHEN a.status='revoked' THEN a.updated_at ELSE NULL END,
 e.created_at,COALESCE(a.updated_at,e.created_at)
FROM enrollments e LEFT JOIN enrollment_access a ON a.enrollment_id=e.id;
INSERT OR IGNORE INTO schema_migrations VALUES(6,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=6;
"""


SCHEMA_V7 = """
CREATE TABLE policy_versions(
 id TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('terms','privacy','refund','course_terms','content_rights')),
 mode TEXT NOT NULL CHECK(mode IN ('test','live')),course_id TEXT REFERENCES courses(id),course_version INTEGER,
 edition INTEGER NOT NULL CHECK(edition>0),title TEXT NOT NULL,summary TEXT NOT NULL DEFAULT '',body TEXT NOT NULL,
 effective_at TEXT,expires_at TEXT,status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','withdrawn')),
 content_hash TEXT NOT NULL,approval_reference TEXT NOT NULL DEFAULT '',published_at TEXT,withdrawn_at TEXT,
 withdrawal_reason TEXT NOT NULL DEFAULT '',created_by TEXT REFERENCES users(id),updated_by TEXT REFERENCES users(id),
 version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 CHECK((kind IN ('course_terms','content_rights') AND course_id IS NOT NULL AND course_version IS NOT NULL) OR (kind IN ('terms','privacy','refund') AND course_id IS NULL AND course_version IS NULL)),
 CHECK(effective_at IS NULL OR expires_at IS NULL OR expires_at>effective_at),
 CHECK(status='draft' OR (published_at IS NOT NULL AND effective_at IS NOT NULL)),
 CHECK(status='draft' OR mode='test' OR length(approval_reference)>=5)
);
CREATE UNIQUE INDEX policy_group_edition ON policy_versions(kind,mode,COALESCE(course_id,''),edition);
CREATE INDEX policy_effective ON policy_versions(kind,mode,course_id,edition DESC,status,effective_at);
CREATE TRIGGER policy_immutable BEFORE UPDATE ON policy_versions
WHEN (OLD.status='draft' AND NEW.status NOT IN ('draft','published')) OR
 (OLD.status!='draft' AND (OLD.status='withdrawn' OR NEW.status!='withdrawn' OR
 NEW.id IS NOT OLD.id OR NEW.kind IS NOT OLD.kind OR NEW.mode IS NOT OLD.mode OR NEW.course_id IS NOT OLD.course_id OR
 NEW.course_version IS NOT OLD.course_version OR NEW.edition IS NOT OLD.edition OR NEW.title IS NOT OLD.title OR
 NEW.summary IS NOT OLD.summary OR NEW.body IS NOT OLD.body OR NEW.effective_at IS NOT OLD.effective_at OR
 NEW.expires_at IS NOT OLD.expires_at OR NEW.content_hash IS NOT OLD.content_hash OR NEW.approval_reference IS NOT OLD.approval_reference OR
 NEW.published_at IS NOT OLD.published_at OR NEW.created_at IS NOT OLD.created_at OR NEW.created_by IS NOT OLD.created_by))
BEGIN SELECT RAISE(ABORT,'Published policy content is immutable'); END;
CREATE TRIGGER policy_no_delete BEFORE DELETE ON policy_versions WHEN OLD.status!='draft'
BEGIN SELECT RAISE(ABORT,'Published policy history cannot be deleted'); END;
CREATE TABLE consents(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),policy_version_id TEXT NOT NULL REFERENCES policy_versions(id),
 context TEXT NOT NULL CHECK(context IN ('account','purchase')),order_id TEXT REFERENCES purchase_orders(id),
 content_hash TEXT NOT NULL,accepted_at TEXT NOT NULL,method TEXT NOT NULL DEFAULT 'explicit',
 CHECK((context='account' AND order_id IS NULL) OR (context='purchase' AND order_id IS NOT NULL))
);
CREATE UNIQUE INDEX one_account_consent ON consents(user_id,policy_version_id) WHERE context='account';
CREATE UNIQUE INDEX one_purchase_consent ON consents(order_id,policy_version_id) WHERE context='purchase';
CREATE INDEX consent_owner ON consents(user_id,accepted_at);
CREATE TRIGGER consent_owner_matches BEFORE INSERT ON consents WHEN NEW.order_id IS NOT NULL AND
 NEW.user_id IS NOT (SELECT user_id FROM purchase_orders WHERE id=NEW.order_id)
BEGIN SELECT RAISE(ABORT,'Consent owner must match the order'); END;
CREATE TRIGGER consent_no_update BEFORE UPDATE ON consents BEGIN SELECT RAISE(ABORT,'Consent history is immutable'); END;
CREATE TRIGGER consent_no_delete BEFORE DELETE ON consents BEGIN SELECT RAISE(ABORT,'Consent history cannot be deleted'); END;
CREATE TABLE order_policy_sets(
 order_id TEXT PRIMARY KEY REFERENCES purchase_orders(id),fingerprint TEXT NOT NULL,
 course_snapshot TEXT NOT NULL,sealed_at TEXT NOT NULL,payment_deadline TEXT NOT NULL
);
CREATE TABLE order_policy_documents(
 order_id TEXT NOT NULL REFERENCES order_policy_sets(order_id),policy_version_id TEXT NOT NULL REFERENCES policy_versions(id),
 kind TEXT NOT NULL CHECK(kind IN ('terms','privacy','refund','course_terms','content_rights')),
 visibility TEXT NOT NULL CHECK(visibility IN ('public','operator')),content_hash TEXT NOT NULL,
 PRIMARY KEY(order_id,kind),CHECK((kind='content_rights' AND visibility='operator') OR (kind!='content_rights' AND visibility='public'))
);
CREATE TABLE payment_policy_checks(
 order_id TEXT PRIMARY KEY REFERENCES purchase_orders(id),checked_at TEXT NOT NULL,
 classification TEXT NOT NULL CHECK(classification IN ('versioned','legacy_inflight')),fingerprint TEXT NOT NULL DEFAULT ''
);
CREATE TABLE legacy_payment_attempts(
 order_id TEXT PRIMARY KEY REFERENCES purchase_orders(id),recorded_at TEXT NOT NULL,prior_status TEXT NOT NULL
);
INSERT INTO legacy_payment_attempts SELECT id,strftime('%Y-%m-%dT%H:%M:%SZ','now'),status FROM purchase_orders
 WHERE status IN ('pending','confirming') AND payment_key IS NOT NULL AND length(payment_key)>=6;
CREATE TRIGGER order_policy_set_no_update BEFORE UPDATE ON order_policy_sets BEGIN SELECT RAISE(ABORT,'Order conditions are immutable'); END;
CREATE TRIGGER order_policy_set_no_delete BEFORE DELETE ON order_policy_sets BEGIN SELECT RAISE(ABORT,'Order conditions cannot be deleted'); END;
CREATE TRIGGER order_policy_document_no_update BEFORE UPDATE ON order_policy_documents BEGIN SELECT RAISE(ABORT,'Order policy references are immutable'); END;
CREATE TRIGGER order_policy_document_no_delete BEFORE DELETE ON order_policy_documents BEGIN SELECT RAISE(ABORT,'Order policy references cannot be deleted'); END;
CREATE TRIGGER payment_policy_check_no_update BEFORE UPDATE ON payment_policy_checks BEGIN SELECT RAISE(ABORT,'Payment policy checks are immutable'); END;
CREATE TRIGGER payment_policy_check_no_delete BEFORE DELETE ON payment_policy_checks BEGIN SELECT RAISE(ABORT,'Payment policy checks cannot be deleted'); END;
CREATE TRIGGER legacy_payment_attempt_no_update BEFORE UPDATE ON legacy_payment_attempts BEGIN SELECT RAISE(ABORT,'Legacy attempt markers are immutable'); END;
CREATE TRIGGER legacy_payment_attempt_no_delete BEFORE DELETE ON legacy_payment_attempts BEGIN SELECT RAISE(ABORT,'Legacy attempt markers cannot be deleted'); END;
INSERT INTO schema_migrations VALUES(7,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=7;
"""


SCHEMA_V8 = """
CREATE TABLE coach_profiles(
 id TEXT PRIMARY KEY,owner_user_id TEXT UNIQUE REFERENCES users(id),
 state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','inactive')),
 public_revision_id TEXT REFERENCES coach_profile_revisions(id),public_version INTEGER NOT NULL DEFAULT 0,
 version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 CHECK(public_version>=0),CHECK(version>=1)
);
CREATE TABLE coach_profile_revisions(
 id TEXT PRIMARY KEY,coach_id TEXT NOT NULL REFERENCES coach_profiles(id),author_id TEXT REFERENCES users(id),
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','approved','rejected','cancelled')),
 base_public_version INTEGER NOT NULL,base_profile_version INTEGER NOT NULL,
 payload TEXT NOT NULL CHECK(json_valid(payload)),payload_hash TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '',review_note TEXT NOT NULL DEFAULT '',approval_reference TEXT NOT NULL DEFAULT '',
 reviewer_id TEXT REFERENCES users(id),version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,submitted_at TEXT,reviewed_at TEXT,published_at TEXT
);
CREATE UNIQUE INDEX coach_one_open_revision ON coach_profile_revisions(coach_id) WHERE status IN ('draft','submitted','rejected');
CREATE INDEX coach_revisions_by_profile ON coach_profile_revisions(coach_id,created_at);
CREATE TRIGGER coach_revision_frozen BEFORE UPDATE ON coach_profile_revisions
 WHEN OLD.status IN ('approved','cancelled') OR (OLD.status='submitted' AND (
 NEW.payload IS NOT OLD.payload OR NEW.payload_hash IS NOT OLD.payload_hash OR
 NEW.base_public_version IS NOT OLD.base_public_version OR NEW.base_profile_version IS NOT OLD.base_profile_version OR
 NEW.author_id IS NOT OLD.author_id OR NEW.coach_id IS NOT OLD.coach_id OR NEW.id IS NOT OLD.id OR NEW.reason IS NOT OLD.reason))
 BEGIN SELECT RAISE(ABORT,'Submitted and approved profile text is immutable'); END;
CREATE TRIGGER coach_revision_no_delete BEFORE DELETE ON coach_profile_revisions
 BEGIN SELECT RAISE(ABORT,'Profile revision history cannot be deleted'); END;
CREATE TRIGGER coach_public_pointer BEFORE UPDATE OF public_revision_id ON coach_profiles
 WHEN NEW.public_revision_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM coach_profile_revisions r WHERE r.id=NEW.public_revision_id AND r.coach_id=NEW.id AND r.status='approved')
 BEGIN SELECT RAISE(ABORT,'Public profile must reference its approved revision'); END;
CREATE TABLE coach_profile_events(
 id TEXT PRIMARY KEY,coach_id TEXT NOT NULL REFERENCES coach_profiles(id),revision_id TEXT REFERENCES coach_profile_revisions(id),
 actor_id TEXT REFERENCES users(id),action TEXT NOT NULL,note TEXT NOT NULL,revision_version INTEGER,created_at TEXT NOT NULL
);
CREATE INDEX coach_events_by_profile ON coach_profile_events(coach_id,created_at);
CREATE TRIGGER coach_event_no_update BEFORE UPDATE ON coach_profile_events BEGIN SELECT RAISE(ABORT,'Profile events are immutable'); END;
CREATE TRIGGER coach_event_no_delete BEFORE DELETE ON coach_profile_events BEGIN SELECT RAISE(ABORT,'Profile events cannot be deleted'); END;
CREATE TABLE coach_verifications(
 id TEXT PRIMARY KEY,coach_id TEXT NOT NULL REFERENCES coach_profiles(id),revision_id TEXT NOT NULL REFERENCES coach_profile_revisions(id),
 kind TEXT NOT NULL CHECK(kind IN ('identity','experience','teaching')),label TEXT NOT NULL,public_note TEXT NOT NULL,
 approval_reference TEXT NOT NULL,checked_by TEXT NOT NULL REFERENCES users(id),checked_at TEXT NOT NULL,expires_at TEXT,
 revoked_at TEXT,revocation_reason TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 1,
 CHECK(expires_at IS NULL OR expires_at>checked_at)
);
CREATE INDEX coach_verification_scope ON coach_verifications(coach_id,revision_id);
CREATE TRIGGER coach_verification_frozen BEFORE UPDATE ON coach_verifications WHEN OLD.revoked_at IS NOT NULL OR
 NEW.id IS NOT OLD.id OR NEW.coach_id IS NOT OLD.coach_id OR NEW.revision_id IS NOT OLD.revision_id OR NEW.kind IS NOT OLD.kind OR
 NEW.label IS NOT OLD.label OR NEW.public_note IS NOT OLD.public_note OR NEW.approval_reference IS NOT OLD.approval_reference OR
 NEW.checked_by IS NOT OLD.checked_by OR NEW.checked_at IS NOT OLD.checked_at OR NEW.expires_at IS NOT OLD.expires_at OR NEW.revoked_at IS NULL
 BEGIN SELECT RAISE(ABORT,'Verification facts can only be revoked'); END;
CREATE TRIGGER coach_verification_no_delete BEFORE DELETE ON coach_verifications BEGIN SELECT RAISE(ABORT,'Verification facts cannot be deleted'); END;
CREATE TABLE booking_coach_snapshots(
 booking_id TEXT PRIMARY KEY REFERENCES bookings(id),coach_id TEXT NOT NULL REFERENCES coach_profiles(id),
 revision_id TEXT NOT NULL REFERENCES coach_profile_revisions(id),public_version INTEGER NOT NULL,coach_name TEXT NOT NULL,recorded_at TEXT NOT NULL
);
CREATE TRIGGER booking_coach_snapshot_no_update BEFORE UPDATE ON booking_coach_snapshots BEGIN SELECT RAISE(ABORT,'Booking profile snapshots are immutable'); END;
CREATE TRIGGER booking_coach_snapshot_no_delete BEFORE DELETE ON booking_coach_snapshots BEGIN SELECT RAISE(ABORT,'Booking profile snapshots cannot be deleted'); END;
CREATE TABLE booking_assignments(
 booking_id TEXT PRIMARY KEY REFERENCES bookings(id),coach_id TEXT NOT NULL REFERENCES coach_profiles(id),
 revision_id TEXT NOT NULL REFERENCES coach_profile_revisions(id),version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL
);
CREATE INDEX booking_assignment_coach ON booking_assignments(coach_id);
CREATE TABLE booking_handoffs(
 id TEXT PRIMARY KEY,booking_id TEXT NOT NULL REFERENCES bookings(id),from_coach_id TEXT NOT NULL,
 to_coach_id TEXT NOT NULL REFERENCES coach_profiles(id),to_revision_id TEXT NOT NULL REFERENCES coach_profile_revisions(id),
 actor_id TEXT NOT NULL REFERENCES users(id),reason TEXT NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(booking_id,version)
);
CREATE TRIGGER booking_handoff_no_update BEFORE UPDATE ON booking_handoffs BEGIN SELECT RAISE(ABORT,'Booking handoff history is immutable'); END;
CREATE TRIGGER booking_handoff_no_delete BEFORE DELETE ON booking_handoffs BEGIN SELECT RAISE(ABORT,'Booking handoff history cannot be deleted'); END;
-- Preserve the three existing example profiles; no person or verification is invented.
INSERT INTO coach_profiles(id,created_at,updated_at) VALUES('live-coach',strftime('%Y-%m-%dT%H:%M:%SZ','now'),strftime('%Y-%m-%dT%H:%M:%SZ','now'));
INSERT INTO coach_profile_revisions(id,coach_id,status,base_public_version,base_profile_version,payload,payload_hash,reason,approval_reference,created_at,updated_at,published_at) VALUES('profilebaseline_live_coach','live-coach','approved',0,1,'{"accent":"green","acceptsInquiry":true,"bio":"국내 라이브 필드의 상황을 중심으로, 액션의 이유와 상대에 대한 가정을 함께 정리하는 코칭 트랙입니다. 현재 코치 영입 전 파일럿 프로필입니다.","claims":[],"initials":"LC","levels":["입문","중급","심화"],"name":"TPC 라이브 코치","pilot":true,"role":"LIVE CASH · HAND REVIEW","samples":[],"specialty":["라이브 캐시","핸드 리뷰","포스트플랍"],"teachingApproach":"신청한 학습 목표와 플레이 상황을 바탕으로 판단의 이유를 함께 정리하는 파일럿 코칭 트랙입니다."}','ab5b6296b5dc26b294df786744139f4094e1a6c5c70eb4da93bc1c3c3b1a8f23','기존 파일럿 소개 보존 · 실제 코치 검증 아님','legacy-pilot-only',strftime('%Y-%m-%dT%H:%M:%SZ','now'),strftime('%Y-%m-%dT%H:%M:%SZ','now'),strftime('%Y-%m-%dT%H:%M:%SZ','now'));
UPDATE coach_profiles SET public_revision_id='profilebaseline_live_coach',public_version=1 WHERE id='live-coach';
INSERT INTO coach_profiles(id,created_at,updated_at) VALUES('mtt-coach',strftime('%Y-%m-%dT%H:%M:%SZ','now'),strftime('%Y-%m-%dT%H:%M:%SZ','now'));
INSERT INTO coach_profile_revisions(id,coach_id,status,base_public_version,base_profile_version,payload,payload_hash,reason,approval_reference,created_at,updated_at,published_at) VALUES('profilebaseline_mtt_coach','mtt-coach','approved',0,1,'{"accent":"gold","acceptsInquiry":true,"bio":"블라인드, 스택 분포, 남은 인원에 따라 달라지는 판단을 복기하는 토너먼트 트랙입니다. 현재 코치 영입 전 파일럿 프로필입니다.","claims":[],"initials":"MT","levels":["입문","중급","심화"],"name":"TPC 토너먼트 코치","pilot":true,"role":"TOURNAMENT · ICM","samples":[],"specialty":["토너먼트","숏스택","ICM"],"teachingApproach":"신청한 학습 목표와 플레이 상황을 바탕으로 판단의 이유를 함께 정리하는 파일럿 코칭 트랙입니다."}','d4afb50f896a14751f42edbcb3a1c12534e5bbe1568bc0eb8b2fae98bd09b292','기존 파일럿 소개 보존 · 실제 코치 검증 아님','legacy-pilot-only',strftime('%Y-%m-%dT%H:%M:%SZ','now'),strftime('%Y-%m-%dT%H:%M:%SZ','now'),strftime('%Y-%m-%dT%H:%M:%SZ','now'));
UPDATE coach_profiles SET public_revision_id='profilebaseline_mtt_coach',public_version=1 WHERE id='mtt-coach';
INSERT INTO coach_profiles(id,created_at,updated_at) VALUES('strategy-coach',strftime('%Y-%m-%dT%H:%M:%SZ','now'),strftime('%Y-%m-%dT%H:%M:%SZ','now'));
INSERT INTO coach_profile_revisions(id,coach_id,status,base_public_version,base_profile_version,payload,payload_hash,reason,approval_reference,created_at,updated_at,published_at) VALUES('profilebaseline_strategy_coach','strategy-coach','approved',0,1,'{"accent":"blue","acceptsInquiry":true,"bio":"레인지를 외우는 데서 한 단계 더 나아가, 포지션과 스택에 맞게 사고하는 학습 트랙입니다. 현재 코치 영입 전 파일럿 프로필입니다.","claims":[],"initials":"ST","levels":["입문","중급","심화"],"name":"TPC 전략 코치","pilot":true,"role":"PREFLOP · STUDY ROUTINE","samples":[],"specialty":["프리플랍","레인지","학습 루틴"],"teachingApproach":"신청한 학습 목표와 플레이 상황을 바탕으로 판단의 이유를 함께 정리하는 파일럿 코칭 트랙입니다."}','6f1f860046c5b8f06640d0962c19101a5ddf16cafdc4faca0f370c2157823c0a','기존 파일럿 소개 보존 · 실제 코치 검증 아님','legacy-pilot-only',strftime('%Y-%m-%dT%H:%M:%SZ','now'),strftime('%Y-%m-%dT%H:%M:%SZ','now'),strftime('%Y-%m-%dT%H:%M:%SZ','now'));
UPDATE coach_profiles SET public_revision_id='profilebaseline_strategy_coach',public_version=1 WHERE id='strategy-coach';
INSERT INTO schema_migrations VALUES(8,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=8;
"""


SCHEMA_V9 = r"""
CREATE TABLE partner_contracts(
 id TEXT PRIMARY KEY,partner_user_id TEXT NOT NULL REFERENCES users(id),mode TEXT NOT NULL CHECK(mode IN ('test','live')),
 title TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL
);
CREATE TRIGGER partner_identity_immutable BEFORE UPDATE ON partner_contracts WHEN OLD.partner_user_id IS NOT NEW.partner_user_id OR OLD.mode IS NOT NEW.mode OR OLD.id IS NOT NEW.id OR OLD.created_by IS NOT NEW.created_by OR OLD.created_at IS NOT NEW.created_at BEGIN SELECT RAISE(ABORT,'Contract party and environment are immutable'); END;
CREATE TRIGGER partner_contract_no_delete BEFORE DELETE ON partner_contracts BEGIN SELECT RAISE(ABORT,'Contract history cannot be deleted'); END;
CREATE TABLE contract_versions(
 id TEXT PRIMARY KEY,contract_id TEXT NOT NULL REFERENCES partner_contracts(id),edition INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','rejected','recorded','withdrawn','cancelled')),
 payload TEXT NOT NULL,payload_hash TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',review_note TEXT NOT NULL DEFAULT '',
 agreement_reference TEXT NOT NULL DEFAULT '',decision_references TEXT NOT NULL DEFAULT '{}',
 author_id TEXT NOT NULL REFERENCES users(id),recorded_by TEXT REFERENCES users(id),recorded_at TEXT,
 version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(contract_id,edition)
);
CREATE UNIQUE INDEX contract_one_open ON contract_versions(contract_id) WHERE status IN ('draft','submitted','rejected');
CREATE TRIGGER contract_version_identity BEFORE UPDATE ON contract_versions WHEN OLD.id IS NOT NEW.id OR OLD.contract_id IS NOT NEW.contract_id OR OLD.edition IS NOT NEW.edition OR OLD.author_id IS NOT NEW.author_id OR OLD.created_at IS NOT NEW.created_at BEGIN SELECT RAISE(ABORT,'Contract version identity is immutable'); END;
CREATE TRIGGER contract_submitted_frozen BEFORE UPDATE ON contract_versions WHEN OLD.status='submitted' AND (OLD.payload IS NOT NEW.payload OR OLD.payload_hash IS NOT NEW.payload_hash OR OLD.reason IS NOT NEW.reason) BEGIN SELECT RAISE(ABORT,'Submitted contract terms are frozen'); END;
CREATE TRIGGER contract_sealed_frozen BEFORE UPDATE ON contract_versions WHEN OLD.status IN ('recorded','withdrawn','cancelled') AND (OLD.payload IS NOT NEW.payload OR OLD.payload_hash IS NOT NEW.payload_hash OR OLD.reason IS NOT NEW.reason OR OLD.agreement_reference IS NOT NEW.agreement_reference OR OLD.decision_references IS NOT NEW.decision_references OR OLD.recorded_by IS NOT NEW.recorded_by OR OLD.recorded_at IS NOT NEW.recorded_at OR NOT (OLD.status='recorded' AND NEW.status='withdrawn')) BEGIN SELECT RAISE(ABORT,'Recorded contract terms are immutable'); END;
CREATE TRIGGER contract_version_no_delete BEFORE DELETE ON contract_versions BEGIN SELECT RAISE(ABORT,'Contract versions cannot be deleted'); END;
CREATE TABLE product_contracts(
 id TEXT PRIMARY KEY,course_id TEXT NOT NULL REFERENCES courses(id),contract_version_id TEXT NOT NULL REFERENCES contract_versions(id),
 mode TEXT NOT NULL CHECK(mode IN ('test','live')),starts_at TEXT NOT NULL,expires_at TEXT,stops_at TEXT,
 course_version INTEGER NOT NULL,reason TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 CHECK(expires_at IS NULL OR starts_at<expires_at)
);
CREATE INDEX product_contract_period ON product_contracts(course_id,mode,starts_at);
CREATE TRIGGER product_contract_frozen BEFORE UPDATE ON product_contracts WHEN OLD.id IS NOT NEW.id OR OLD.course_id IS NOT NEW.course_id OR OLD.contract_version_id IS NOT NEW.contract_version_id OR OLD.mode IS NOT NEW.mode OR OLD.starts_at IS NOT NEW.starts_at OR OLD.expires_at IS NOT NEW.expires_at OR OLD.course_version IS NOT NEW.course_version OR OLD.reason IS NOT NEW.reason OR OLD.created_by IS NOT NEW.created_by OR OLD.created_at IS NOT NEW.created_at OR NEW.version!=OLD.version+1 OR (OLD.stops_at IS NOT NULL AND (NEW.stops_at IS NULL OR NEW.stops_at>OLD.stops_at)) BEGIN SELECT RAISE(ABORT,'Product terms are immutable; termination cannot extend a period'); END;
CREATE TRIGGER product_contract_no_delete BEFORE DELETE ON product_contracts BEGIN SELECT RAISE(ABORT,'Product contract history cannot be deleted'); END;
CREATE TABLE contract_events(
 id TEXT PRIMARY KEY,contract_id TEXT NOT NULL REFERENCES partner_contracts(id),contract_version_id TEXT REFERENCES contract_versions(id),
 binding_id TEXT REFERENCES product_contracts(id),actor_id TEXT NOT NULL REFERENCES users(id),action TEXT NOT NULL,reason TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TRIGGER contract_event_no_update BEFORE UPDATE ON contract_events BEGIN SELECT RAISE(ABORT,'Contract events are immutable'); END;
CREATE TRIGGER contract_event_no_delete BEFORE DELETE ON contract_events BEGIN SELECT RAISE(ABORT,'Contract events cannot be deleted'); END;
CREATE TABLE order_contract_snapshots(
 order_id TEXT PRIMARY KEY REFERENCES purchase_orders(id),binding_id TEXT NOT NULL REFERENCES product_contracts(id),
 contract_version_id TEXT NOT NULL REFERENCES contract_versions(id),payload TEXT NOT NULL,payload_hash TEXT NOT NULL,sealed_at TEXT NOT NULL
);
CREATE TRIGGER order_contract_no_update BEFORE UPDATE ON order_contract_snapshots BEGIN SELECT RAISE(ABORT,'Order contract snapshots are immutable'); END;
CREATE TRIGGER order_contract_no_delete BEFORE DELETE ON order_contract_snapshots BEGIN SELECT RAISE(ABORT,'Order contract snapshots cannot be deleted'); END;
CREATE TABLE contract_legacy_attempts(order_id TEXT PRIMARY KEY REFERENCES purchase_orders(id),marked_at TEXT NOT NULL);
CREATE TRIGGER contract_legacy_no_update BEFORE UPDATE ON contract_legacy_attempts BEGIN SELECT RAISE(ABORT,'Legacy contract classification is immutable'); END;
CREATE TRIGGER contract_legacy_no_delete BEFORE DELETE ON contract_legacy_attempts BEGIN SELECT RAISE(ABORT,'Legacy contract classification cannot be deleted'); END;
INSERT INTO contract_legacy_attempts SELECT id,strftime('%Y-%m-%dT%H:%M:%SZ','now') FROM purchase_orders WHERE status IN ('pending','confirming') AND payment_key IS NOT NULL;
INSERT INTO schema_migrations VALUES(9,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=9;
"""


SCHEMA_V10 = r"""
CREATE TABLE product_prices(
 id TEXT PRIMARY KEY,course_id TEXT NOT NULL REFERENCES courses(id),mode TEXT NOT NULL CHECK(mode IN ('test','live')),edition INTEGER NOT NULL,
 author_id TEXT NOT NULL REFERENCES users(id),status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','rejected','published','withdrawn','cancelled')),
 payload TEXT NOT NULL,payload_hash TEXT NOT NULL,course_version INTEGER NOT NULL,course_snapshot TEXT NOT NULL,
 effective_at TEXT,expires_at TEXT,reason TEXT NOT NULL DEFAULT '',review_note TEXT NOT NULL DEFAULT '',approval_reference TEXT NOT NULL DEFAULT '',
 reviewer_id TEXT REFERENCES users(id),submitted_at TEXT,published_at TEXT,withdrawn_at TEXT,version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(course_id,mode,edition)
);
CREATE UNIQUE INDEX price_one_open ON product_prices(course_id,mode) WHERE status IN ('draft','submitted','rejected');
CREATE UNIQUE INDEX price_start_unique ON product_prices(course_id,mode,effective_at) WHERE status IN ('published','withdrawn');
CREATE INDEX price_current ON product_prices(course_id,mode,effective_at);
CREATE TRIGGER price_identity_immutable BEFORE UPDATE ON product_prices WHEN OLD.id IS NOT NEW.id OR OLD.course_id IS NOT NEW.course_id OR OLD.mode IS NOT NEW.mode OR OLD.edition IS NOT NEW.edition OR OLD.author_id IS NOT NEW.author_id OR OLD.created_at IS NOT NEW.created_at BEGIN SELECT RAISE(ABORT,'Price identity is immutable'); END;
CREATE TRIGGER price_submitted_frozen BEFORE UPDATE ON product_prices WHEN OLD.status='submitted' AND (OLD.payload IS NOT NEW.payload OR OLD.course_version IS NOT NEW.course_version OR OLD.course_snapshot IS NOT NEW.course_snapshot OR OLD.reason IS NOT NEW.reason) BEGIN SELECT RAISE(ABORT,'Submitted price proposal is frozen'); END;
CREATE TRIGGER price_published_frozen BEFORE UPDATE ON product_prices WHEN OLD.status IN ('published','withdrawn','cancelled') AND (OLD.payload IS NOT NEW.payload OR OLD.payload_hash IS NOT NEW.payload_hash OR OLD.course_version IS NOT NEW.course_version OR OLD.course_snapshot IS NOT NEW.course_snapshot OR OLD.effective_at IS NOT NEW.effective_at OR OLD.expires_at IS NOT NEW.expires_at OR OLD.approval_reference IS NOT NEW.approval_reference OR OLD.reviewer_id IS NOT NEW.reviewer_id OR OLD.published_at IS NOT NEW.published_at OR NOT (OLD.status='published' AND NEW.status='withdrawn')) BEGIN SELECT RAISE(ABORT,'Published price terms are immutable'); END;
CREATE TRIGGER price_no_delete BEFORE DELETE ON product_prices BEGIN SELECT RAISE(ABORT,'Price history cannot be deleted'); END;
CREATE TABLE product_sale_controls(
 course_id TEXT NOT NULL REFERENCES courses(id),mode TEXT NOT NULL CHECK(mode IN ('test','live')),state TEXT NOT NULL CHECK(state IN ('active','paused')),
 version INTEGER NOT NULL,reason TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES users(id),updated_at TEXT NOT NULL,PRIMARY KEY(course_id,mode)
);
CREATE TABLE price_events(
 id TEXT PRIMARY KEY,course_id TEXT NOT NULL REFERENCES courses(id),mode TEXT NOT NULL,price_id TEXT REFERENCES product_prices(id),actor_id TEXT NOT NULL REFERENCES users(id),action TEXT NOT NULL,reason TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TRIGGER price_event_no_update BEFORE UPDATE ON price_events BEGIN SELECT RAISE(ABORT,'Price events are immutable'); END;
CREATE TRIGGER price_event_no_delete BEFORE DELETE ON price_events BEGIN SELECT RAISE(ABORT,'Price events cannot be deleted'); END;
CREATE TABLE policy_price_links(
 policy_id TEXT PRIMARY KEY REFERENCES policy_versions(id),price_id TEXT NOT NULL REFERENCES product_prices(id),price_hash TEXT NOT NULL,amount INTEGER NOT NULL,currency TEXT NOT NULL
);
CREATE TRIGGER policy_price_link_frozen_insert BEFORE INSERT ON policy_price_links WHEN (SELECT status FROM policy_versions WHERE id=NEW.policy_id)!='draft' BEGIN SELECT RAISE(ABORT,'Published policy price reference cannot be added'); END;
CREATE TRIGGER policy_price_link_frozen_update BEFORE UPDATE ON policy_price_links WHEN (SELECT status FROM policy_versions WHERE id=OLD.policy_id)!='draft' BEGIN SELECT RAISE(ABORT,'Published policy price reference is immutable'); END;
CREATE TRIGGER policy_price_link_frozen_delete BEFORE DELETE ON policy_price_links WHEN (SELECT status FROM policy_versions WHERE id=OLD.policy_id)!='draft' BEGIN SELECT RAISE(ABORT,'Published policy price reference cannot be deleted'); END;
CREATE TABLE order_price_snapshots(
 order_id TEXT PRIMARY KEY REFERENCES purchase_orders(id),price_id TEXT REFERENCES product_prices(id),classification TEXT NOT NULL CHECK(classification IN ('pilot','versioned')),payload TEXT NOT NULL,payload_hash TEXT NOT NULL,sealed_at TEXT NOT NULL
);
CREATE TRIGGER order_price_no_update BEFORE UPDATE ON order_price_snapshots BEGIN SELECT RAISE(ABORT,'Order price snapshot is immutable'); END;
CREATE TRIGGER order_price_no_delete BEFORE DELETE ON order_price_snapshots BEGIN SELECT RAISE(ABORT,'Order price snapshot cannot be deleted'); END;
CREATE TABLE price_legacy_attempts(order_id TEXT PRIMARY KEY REFERENCES purchase_orders(id),marked_at TEXT NOT NULL);
CREATE TRIGGER price_legacy_no_update BEFORE UPDATE ON price_legacy_attempts BEGIN SELECT RAISE(ABORT,'Legacy price classification is immutable'); END;
CREATE TRIGGER price_legacy_no_delete BEFORE DELETE ON price_legacy_attempts BEGIN SELECT RAISE(ABORT,'Legacy price classification cannot be deleted'); END;
INSERT INTO price_legacy_attempts SELECT id,strftime('%Y-%m-%dT%H:%M:%SZ','now') FROM purchase_orders WHERE status IN ('pending','confirming') AND payment_key IS NOT NULL;
INSERT INTO schema_migrations VALUES(10,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=10;
"""


SCHEMA_V11 = r"""
ALTER TABLE course_lessons ADD COLUMN resources TEXT NOT NULL DEFAULT '[]';
CREATE TABLE learning_resources(
 id TEXT PRIMARY KEY,course_id TEXT NOT NULL REFERENCES courses(id),uploader_id TEXT NOT NULL REFERENCES users(id),
 filename TEXT NOT NULL,mime_type TEXT NOT NULL,size INTEGER NOT NULL CHECK(size>0),sha256 TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','withdrawn')),
 approval_reference TEXT NOT NULL DEFAULT '',review_note TEXT NOT NULL DEFAULT '',reviewer_id TEXT REFERENCES users(id),
 version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,reviewed_at TEXT
);
CREATE INDEX resources_course ON learning_resources(course_id,created_at);
CREATE TRIGGER resource_bytes_immutable BEFORE UPDATE ON learning_resources WHEN OLD.id IS NOT NEW.id OR OLD.course_id IS NOT NEW.course_id OR OLD.uploader_id IS NOT NEW.uploader_id OR OLD.filename IS NOT NEW.filename OR OLD.mime_type IS NOT NEW.mime_type OR OLD.size IS NOT NEW.size OR OLD.sha256 IS NOT NEW.sha256 OR OLD.created_at IS NOT NEW.created_at BEGIN SELECT RAISE(ABORT,'Resource file identity is immutable'); END;
CREATE TRIGGER resource_review_immutable BEFORE UPDATE ON learning_resources WHEN OLD.status IN ('rejected','withdrawn') OR (OLD.status='approved' AND (NEW.status!='withdrawn' OR OLD.approval_reference IS NOT NEW.approval_reference)) BEGIN SELECT RAISE(ABORT,'Resource decision cannot be rewritten'); END;
CREATE TRIGGER resource_no_delete BEFORE DELETE ON learning_resources BEGIN SELECT RAISE(ABORT,'Resource history cannot be deleted'); END;
CREATE TABLE resource_events(id TEXT PRIMARY KEY,resource_id TEXT NOT NULL REFERENCES learning_resources(id),actor_id TEXT NOT NULL REFERENCES users(id),action TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TRIGGER resource_event_no_update BEFORE UPDATE ON resource_events BEGIN SELECT RAISE(ABORT,'Resource events are immutable'); END;
CREATE TRIGGER resource_event_no_delete BEFORE DELETE ON resource_events BEGIN SELECT RAISE(ABORT,'Resource events cannot be deleted'); END;
CREATE TABLE curriculum_changes(
 id TEXT PRIMARY KEY,course_id TEXT NOT NULL REFERENCES courses(id),author_id TEXT NOT NULL REFERENCES users(id),
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','rejected','applied','cancelled')),
 base_version INTEGER NOT NULL,base_snapshot TEXT NOT NULL,payload TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',review_note TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,reviewer_id TEXT REFERENCES users(id),applied_version INTEGER
);
CREATE UNIQUE INDEX curriculum_one_open ON curriculum_changes(course_id) WHERE status IN ('draft','submitted','rejected');
CREATE INDEX curriculum_course ON curriculum_changes(course_id,created_at);
CREATE TRIGGER curriculum_identity_immutable BEFORE UPDATE ON curriculum_changes WHEN OLD.id IS NOT NEW.id OR OLD.course_id IS NOT NEW.course_id OR OLD.author_id IS NOT NEW.author_id OR OLD.created_at IS NOT NEW.created_at BEGIN SELECT RAISE(ABORT,'Curriculum proposal identity is immutable'); END;
CREATE TRIGGER curriculum_submitted_frozen BEFORE UPDATE ON curriculum_changes WHEN OLD.status='submitted' AND (OLD.base_version IS NOT NEW.base_version OR OLD.base_snapshot IS NOT NEW.base_snapshot OR OLD.payload IS NOT NEW.payload OR OLD.reason IS NOT NEW.reason) BEGIN SELECT RAISE(ABORT,'Submitted curriculum is frozen'); END;
CREATE TRIGGER curriculum_final_frozen BEFORE UPDATE ON curriculum_changes WHEN OLD.status IN ('applied','cancelled') BEGIN SELECT RAISE(ABORT,'Applied curriculum is immutable'); END;
CREATE TRIGGER curriculum_no_delete BEFORE DELETE ON curriculum_changes BEGIN SELECT RAISE(ABORT,'Curriculum history cannot be deleted'); END;
CREATE TABLE curriculum_events(id TEXT PRIMARY KEY,change_id TEXT NOT NULL REFERENCES curriculum_changes(id),actor_id TEXT NOT NULL REFERENCES users(id),action TEXT NOT NULL,reason TEXT NOT NULL,details TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);
CREATE TRIGGER curriculum_event_no_update BEFORE UPDATE ON curriculum_events BEGIN SELECT RAISE(ABORT,'Curriculum events are immutable'); END;
CREATE TRIGGER curriculum_event_no_delete BEFORE DELETE ON curriculum_events BEGIN SELECT RAISE(ABORT,'Curriculum events cannot be deleted'); END;
CREATE TABLE lesson_batches(
 id TEXT PRIMARY KEY,course_id TEXT NOT NULL REFERENCES courses(id),author_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,payload_hash TEXT NOT NULL,lesson_ids TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(course_id,author_id,request_key)
);
CREATE TRIGGER lesson_batch_no_update BEFORE UPDATE ON lesson_batches BEGIN SELECT RAISE(ABORT,'Lesson batch receipts are immutable'); END;
CREATE TRIGGER lesson_batch_no_delete BEFORE DELETE ON lesson_batches BEGIN SELECT RAISE(ABORT,'Lesson batch receipts cannot be deleted'); END;
INSERT INTO schema_migrations VALUES(11,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=11;
"""


SCHEMA_V12 = r"""
CREATE TABLE course_reviews(
 id TEXT PRIMARY KEY,course_id TEXT NOT NULL REFERENCES courses(id),author_id TEXT NOT NULL REFERENCES users(id),
 previous_id TEXT REFERENCES course_reviews(id),checklist_version TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','changes_requested','published','withdrawn')),
 base_version INTEGER NOT NULL,snapshot TEXT NOT NULL,snapshot_hash TEXT NOT NULL,
 author_checks TEXT NOT NULL DEFAULT '{}',reviewer_checks TEXT NOT NULL DEFAULT '{}',
 author_note TEXT NOT NULL DEFAULT '',review_note TEXT NOT NULL DEFAULT '',decision_reason TEXT NOT NULL DEFAULT '',reviewer_id TEXT REFERENCES users(id),
 version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,submitted_at TEXT,decided_at TEXT,published_version INTEGER
);
CREATE UNIQUE INDEX course_review_one_open ON course_reviews(course_id) WHERE status IN ('draft','submitted');
CREATE INDEX course_review_course ON course_reviews(course_id,created_at);
CREATE TRIGGER course_review_identity_immutable BEFORE UPDATE ON course_reviews WHEN OLD.id IS NOT NEW.id OR OLD.course_id IS NOT NEW.course_id OR OLD.author_id IS NOT NEW.author_id OR OLD.previous_id IS NOT NEW.previous_id OR OLD.checklist_version IS NOT NEW.checklist_version OR OLD.created_at IS NOT NEW.created_at BEGIN SELECT RAISE(ABORT,'Course review identity is immutable'); END;
CREATE TRIGGER course_review_submitted_frozen BEFORE UPDATE ON course_reviews WHEN OLD.status='submitted' AND (OLD.base_version IS NOT NEW.base_version OR OLD.snapshot IS NOT NEW.snapshot OR OLD.snapshot_hash IS NOT NEW.snapshot_hash OR OLD.author_checks IS NOT NEW.author_checks OR OLD.author_note IS NOT NEW.author_note OR OLD.submitted_at IS NOT NEW.submitted_at) BEGIN SELECT RAISE(ABORT,'Submitted course review is frozen'); END;
CREATE TRIGGER course_review_final_frozen BEFORE UPDATE ON course_reviews WHEN OLD.status IN ('changes_requested','published','withdrawn') BEGIN SELECT RAISE(ABORT,'Course review decision is immutable'); END;
CREATE TRIGGER course_review_state BEFORE UPDATE ON course_reviews WHEN NOT ((OLD.status='draft' AND NEW.status IN ('draft','submitted','withdrawn')) OR (OLD.status='submitted' AND NEW.status IN ('submitted','changes_requested','published','withdrawn'))) OR NEW.version!=OLD.version+1 BEGIN SELECT RAISE(ABORT,'Invalid course review transition'); END;
CREATE TRIGGER course_review_no_delete BEFORE DELETE ON course_reviews BEGIN SELECT RAISE(ABORT,'Course review history cannot be deleted'); END;
CREATE TABLE course_review_events(id TEXT PRIMARY KEY,review_id TEXT NOT NULL REFERENCES course_reviews(id),actor_id TEXT NOT NULL REFERENCES users(id),action TEXT NOT NULL,reason TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TRIGGER course_review_event_no_update BEFORE UPDATE ON course_review_events BEGIN SELECT RAISE(ABORT,'Course review events are immutable'); END;
CREATE TRIGGER course_review_event_no_delete BEFORE DELETE ON course_review_events BEGIN SELECT RAISE(ABORT,'Course review events cannot be deleted'); END;
CREATE TABLE course_review_comments(id TEXT PRIMARY KEY,review_id TEXT NOT NULL REFERENCES course_reviews(id),author_id TEXT NOT NULL REFERENCES users(id),check_id TEXT CHECK(check_id IS NULL OR check_id IN ('objectives','content','preview','assets','rights')),body TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX course_review_comment_page ON course_review_comments(review_id,created_at);
CREATE TRIGGER course_review_comment_no_update BEFORE UPDATE ON course_review_comments BEGIN SELECT RAISE(ABORT,'Course review comments are immutable'); END;
CREATE TRIGGER course_review_comment_no_delete BEFORE DELETE ON course_review_comments BEGIN SELECT RAISE(ABORT,'Course review comments cannot be deleted'); END;
INSERT INTO schema_migrations VALUES(12,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=12;
"""


SCHEMA_V13 = r"""
CREATE TABLE media_uploads(
 id TEXT PRIMARY KEY,course_id TEXT NOT NULL REFERENCES courses(id),uploader_id TEXT NOT NULL REFERENCES users(id),
 request_key TEXT NOT NULL,filename TEXT NOT NULL,size INTEGER NOT NULL CHECK(size BETWEEN 32 AND 104857600),sha256 TEXT NOT NULL,
 offset INTEGER NOT NULL DEFAULT 0 CHECK(offset>=0 AND offset<=size),
 status TEXT NOT NULL DEFAULT 'uploading' CHECK(status IN ('uploading','ready','completed','cancelled')),
 asset_id TEXT UNIQUE REFERENCES media_assets(id),version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,expires_at INTEGER NOT NULL,finished_at TEXT,
 UNIQUE(course_id,uploader_id,request_key),CHECK((status='completed' AND asset_id IS NOT NULL) OR (status!='completed' AND asset_id IS NULL))
);
CREATE INDEX media_upload_course ON media_uploads(course_id,created_at);
CREATE TRIGGER media_upload_identity BEFORE UPDATE ON media_uploads WHEN OLD.id IS NOT NEW.id OR OLD.course_id IS NOT NEW.course_id OR OLD.uploader_id IS NOT NEW.uploader_id OR OLD.request_key IS NOT NEW.request_key OR OLD.filename IS NOT NEW.filename OR OLD.size IS NOT NEW.size OR OLD.sha256 IS NOT NEW.sha256 OR OLD.created_at IS NOT NEW.created_at OR OLD.expires_at IS NOT NEW.expires_at BEGIN SELECT RAISE(ABORT,'Media upload identity is immutable'); END;
CREATE TRIGGER media_upload_final BEFORE UPDATE ON media_uploads WHEN OLD.status IN ('completed','cancelled') BEGIN SELECT RAISE(ABORT,'Media upload receipt is immutable'); END;
CREATE TRIGGER media_upload_state BEFORE UPDATE ON media_uploads WHEN NEW.version!=OLD.version+1 OR NEW.offset<OLD.offset OR NOT ((OLD.status='uploading' AND NEW.status IN ('uploading','ready','cancelled')) OR (OLD.status='ready' AND NEW.status IN ('completed','cancelled'))) OR (NEW.status IN ('ready','completed') AND NEW.offset!=NEW.size) BEGIN SELECT RAISE(ABORT,'Invalid media upload transition'); END;
CREATE TRIGGER media_upload_no_delete BEFORE DELETE ON media_uploads BEGIN SELECT RAISE(ABORT,'Media upload receipts cannot be deleted'); END;
CREATE TABLE media_upload_chunks(upload_id TEXT NOT NULL REFERENCES media_uploads(id),offset INTEGER NOT NULL CHECK(offset>=0),size INTEGER NOT NULL CHECK(size BETWEEN 1 AND 1048576),sha256 TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(upload_id,offset));
CREATE TRIGGER media_chunk_no_update BEFORE UPDATE ON media_upload_chunks BEGIN SELECT RAISE(ABORT,'Media chunk receipts are immutable'); END;
CREATE TRIGGER media_chunk_no_delete BEFORE DELETE ON media_upload_chunks BEGIN SELECT RAISE(ABORT,'Media chunk receipts cannot be deleted'); END;
CREATE TABLE media_upload_events(id TEXT PRIMARY KEY,upload_id TEXT NOT NULL REFERENCES media_uploads(id),actor_id TEXT NOT NULL REFERENCES users(id),action TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TRIGGER media_upload_event_no_update BEFORE UPDATE ON media_upload_events BEGIN SELECT RAISE(ABORT,'Media upload events are immutable'); END;
CREATE TRIGGER media_upload_event_no_delete BEFORE DELETE ON media_upload_events BEGIN SELECT RAISE(ABORT,'Media upload events cannot be deleted'); END;
CREATE TABLE lesson_watch_sources(user_id TEXT NOT NULL,course_id TEXT NOT NULL,lesson_id TEXT NOT NULL,asset_id TEXT REFERENCES media_assets(id),lesson_version INTEGER NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(user_id,course_id,lesson_id),FOREIGN KEY(user_id,course_id,lesson_id) REFERENCES lesson_watch(user_id,course_id,lesson_id) ON DELETE CASCADE);
INSERT INTO schema_migrations VALUES(13,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=13;
"""


SCHEMA_V14 = r"""
CREATE TABLE learning_start_acknowledgements(
 user_id TEXT NOT NULL,course_id TEXT NOT NULL,guide_version TEXT NOT NULL,
 acknowledged_at TEXT NOT NULL,PRIMARY KEY(user_id,course_id,guide_version),
 FOREIGN KEY(user_id,course_id) REFERENCES enrollments(user_id,course_id)
);
CREATE TRIGGER learning_start_ack_no_update BEFORE UPDATE ON learning_start_acknowledgements BEGIN SELECT RAISE(ABORT,'Learning guide acknowledgements are immutable'); END;
CREATE TRIGGER learning_start_ack_no_delete BEFORE DELETE ON learning_start_acknowledgements BEGIN SELECT RAISE(ABORT,'Learning guide acknowledgements cannot be deleted'); END;
INSERT INTO schema_migrations VALUES(14,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=14;
"""


SCHEMA_V15 = r"""
CREATE TABLE media_playback_metadata(
 asset_id TEXT PRIMARY KEY REFERENCES media_assets(id),asset_sha256 TEXT NOT NULL,
 duration_seconds REAL NOT NULL CHECK(duration_seconds>0 AND duration_seconds<=86400),
 source TEXT NOT NULL CHECK(source='mp4_mvhd'),is_sample INTEGER NOT NULL DEFAULT 0 CHECK(is_sample IN (0,1)),
 created_at TEXT NOT NULL
);
CREATE TRIGGER media_playback_metadata_no_update BEFORE UPDATE ON media_playback_metadata BEGIN SELECT RAISE(ABORT,'Original playback metadata is immutable'); END;
CREATE TRIGGER media_playback_metadata_no_delete BEFORE DELETE ON media_playback_metadata BEGIN SELECT RAISE(ABORT,'Original playback metadata cannot be deleted'); END;
CREATE TABLE lesson_video_progress(
 user_id TEXT NOT NULL REFERENCES users(id),course_id TEXT NOT NULL,lesson_id TEXT NOT NULL,
 asset_id TEXT NOT NULL REFERENCES media_assets(id),seconds REAL NOT NULL CHECK(seconds>=0 AND seconds<=86400),
 furthest_seconds REAL NOT NULL CHECK(furthest_seconds>=seconds AND furthest_seconds<=86400),
 version INTEGER NOT NULL CHECK(version>=1),lesson_version INTEGER NOT NULL CHECK(lesson_version>=1),
 last_request_key TEXT,last_request_hash TEXT,stream_id TEXT,stream_sequence INTEGER,updated_at TEXT NOT NULL,
 PRIMARY KEY(user_id,course_id,lesson_id,asset_id),
 FOREIGN KEY(course_id,lesson_id) REFERENCES course_lessons(course_id,id),
 CHECK((last_request_key IS NULL AND last_request_hash IS NULL) OR (last_request_key IS NOT NULL AND last_request_hash IS NOT NULL)),
 CHECK((stream_id IS NULL AND stream_sequence IS NULL) OR (stream_id IS NOT NULL AND stream_sequence>=1))
);
CREATE INDEX lesson_video_member ON lesson_video_progress(user_id,course_id,updated_at);
INSERT INTO schema_migrations VALUES(15,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=15;
"""


SCHEMA_V16 = r"""
CREATE TABLE lesson_quiz_versions(
 course_id TEXT NOT NULL,lesson_id TEXT NOT NULL,quiz_hash TEXT NOT NULL CHECK(length(quiz_hash)=64),
 captured_lesson_version INTEGER NOT NULL CHECK(captured_lesson_version>=1),quiz_json TEXT NOT NULL,created_at TEXT NOT NULL,
 PRIMARY KEY(course_id,lesson_id,quiz_hash),FOREIGN KEY(course_id,lesson_id) REFERENCES course_lessons(course_id,id)
);
CREATE TRIGGER lesson_quiz_version_no_update BEFORE UPDATE ON lesson_quiz_versions BEGIN SELECT RAISE(ABORT,'Quiz versions are immutable'); END;
CREATE TRIGGER lesson_quiz_version_no_delete BEFORE DELETE ON lesson_quiz_versions BEGIN SELECT RAISE(ABORT,'Quiz versions cannot be deleted'); END;
CREATE TABLE lesson_quiz_attempts(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),course_id TEXT NOT NULL,lesson_id TEXT NOT NULL,
 quiz_hash TEXT NOT NULL,lesson_version INTEGER NOT NULL CHECK(lesson_version>=1),
 answer INTEGER NOT NULL CHECK(answer BETWEEN 0 AND 5),correct INTEGER NOT NULL CHECK(correct IN (0,1)),
 request_key TEXT NOT NULL CHECK(length(request_key) BETWEEN 8 AND 120),request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(user_id,course_id,lesson_id,request_key),
 FOREIGN KEY(course_id,lesson_id,quiz_hash) REFERENCES lesson_quiz_versions(course_id,lesson_id,quiz_hash)
);
CREATE INDEX lesson_quiz_owner ON lesson_quiz_attempts(user_id,course_id,lesson_id);
CREATE TRIGGER lesson_quiz_attempt_no_update BEFORE UPDATE ON lesson_quiz_attempts BEGIN SELECT RAISE(ABORT,'Quiz attempts are immutable'); END;
CREATE TRIGGER lesson_quiz_attempt_no_delete BEFORE DELETE ON lesson_quiz_attempts BEGIN SELECT RAISE(ABORT,'Quiz attempts cannot be deleted'); END;
CREATE TABLE lesson_progress_events(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),course_id TEXT NOT NULL,lesson_id TEXT NOT NULL,
 request_key TEXT NOT NULL CHECK(length(request_key) BETWEEN 8 AND 120),request_hash TEXT NOT NULL,
 completed INTEGER NOT NULL CHECK(completed IN (0,1)),changed INTEGER NOT NULL CHECK(changed IN (0,1)),
 result_version INTEGER NOT NULL CHECK(result_version>=0),lesson_version INTEGER NOT NULL CHECK(lesson_version>=1),
 completed_at TEXT,created_at TEXT NOT NULL,
 UNIQUE(user_id,course_id,lesson_id,request_key),
 FOREIGN KEY(course_id,lesson_id) REFERENCES course_lessons(course_id,id),
 CHECK((completed=1 AND completed_at IS NOT NULL) OR (completed=0 AND completed_at IS NULL))
);
CREATE INDEX lesson_progress_version ON lesson_progress_events(user_id,course_id,lesson_id,result_version DESC);
CREATE TRIGGER lesson_progress_event_no_update BEFORE UPDATE ON lesson_progress_events BEGIN SELECT RAISE(ABORT,'Completion receipts are immutable'); END;
CREATE TRIGGER lesson_progress_event_no_delete BEFORE DELETE ON lesson_progress_events BEGIN SELECT RAISE(ABORT,'Completion receipts cannot be deleted'); END;
INSERT INTO schema_migrations VALUES(16,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=16;
"""


SCHEMA_V17 = r"""
CREATE TABLE video_captions(
 id TEXT PRIMARY KEY,course_id TEXT NOT NULL REFERENCES courses(id),media_asset_id TEXT NOT NULL REFERENCES media_assets(id),
 media_sha256 TEXT NOT NULL CHECK(length(media_sha256)=64),uploader_id TEXT REFERENCES users(id),
 filename TEXT NOT NULL,language TEXT NOT NULL,label TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('subtitles','captions')),
 body BLOB NOT NULL,size INTEGER NOT NULL CHECK(size BETWEEN 8 AND 1048576),sha256 TEXT NOT NULL CHECK(length(sha256)=64),
 cue_count INTEGER NOT NULL CHECK(cue_count BETWEEN 1 AND 10000),end_seconds REAL NOT NULL CHECK(end_seconds>0 AND end_seconds<=86400),profile TEXT NOT NULL CHECK(profile='TPC-VTT-1'),
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(uploader_id,course_id,request_key),UNIQUE(id,media_asset_id,language,kind),CHECK(length(body)=size AND typeof(body)='blob')
);
CREATE TRIGGER caption_original_no_update BEFORE UPDATE ON video_captions BEGIN SELECT RAISE(ABORT,'Caption originals are immutable'); END;
CREATE TRIGGER caption_original_no_delete BEFORE DELETE ON video_captions BEGIN SELECT RAISE(ABORT,'Caption originals cannot be deleted'); END;
CREATE TABLE video_caption_states(
 caption_id TEXT PRIMARY KEY,media_asset_id TEXT NOT NULL,language TEXT NOT NULL,kind TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','withdrawn','superseded','cancelled')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1),review_note TEXT NOT NULL DEFAULT '',approval_reference TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,
 FOREIGN KEY(caption_id,media_asset_id,language,kind) REFERENCES video_captions(id,media_asset_id,language,kind)
);
CREATE UNIQUE INDEX one_active_caption ON video_caption_states(media_asset_id,language,kind) WHERE status='approved';
CREATE TRIGGER caption_state_transition BEFORE UPDATE ON video_caption_states WHEN
 NEW.caption_id!=OLD.caption_id OR NEW.media_asset_id!=OLD.media_asset_id OR NEW.language!=OLD.language OR NEW.kind!=OLD.kind OR NEW.version!=OLD.version+1 OR (OLD.status='approved' AND NEW.approval_reference!=OLD.approval_reference) OR (NEW.status='approved' AND length(NEW.approval_reference)<5) OR NOT
 ((OLD.status='pending' AND NEW.status IN ('approved','rejected','cancelled')) OR (OLD.status='approved' AND NEW.status IN ('withdrawn','superseded')))
 BEGIN SELECT RAISE(ABORT,'Invalid caption state transition'); END;
CREATE TRIGGER caption_state_no_delete BEFORE DELETE ON video_caption_states BEGIN SELECT RAISE(ABORT,'Caption state cannot be deleted'); END;
CREATE TABLE video_caption_events(
 id TEXT PRIMARY KEY,caption_id TEXT NOT NULL REFERENCES video_captions(id),actor_id TEXT REFERENCES users(id),
 action TEXT NOT NULL CHECK(action IN ('submitted','approved','rejected','withdrawn','superseded','cancelled','sample_approved')),
 reason TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,result_version INTEGER NOT NULL CHECK(result_version>=1),
 related_caption_id TEXT REFERENCES video_captions(id),created_at TEXT NOT NULL,UNIQUE(caption_id,actor_id,request_key)
);
CREATE INDEX caption_event_history ON video_caption_events(caption_id,created_at);
CREATE TRIGGER caption_event_no_update BEFORE UPDATE ON video_caption_events BEGIN SELECT RAISE(ABORT,'Caption events are immutable'); END;
CREATE TRIGGER caption_event_no_delete BEFORE DELETE ON video_caption_events BEGIN SELECT RAISE(ABORT,'Caption events cannot be deleted'); END;
INSERT INTO schema_migrations VALUES(17,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=17;
"""


SCHEMA_V18 = r"""
CREATE TABLE question_revisions(
 id TEXT PRIMARY KEY,target_id TEXT NOT NULL REFERENCES questions(id),question_id TEXT NOT NULL REFERENCES questions(id),
 version INTEGER NOT NULL CHECK(version>=1),expected_version INTEGER NOT NULL CHECK(expected_version=version-1),
 origin TEXT NOT NULL CHECK(origin IN ('baseline','created','edited')),actor_id TEXT,reason TEXT,
 before_snapshot TEXT CHECK(before_snapshot IS NULL OR json_valid(before_snapshot)),after_snapshot TEXT NOT NULL CHECK(json_valid(after_snapshot)),
 before_sha256 TEXT CHECK(before_sha256 IS NULL OR length(before_sha256)=64),after_sha256 TEXT NOT NULL CHECK(length(after_sha256)=64),
 attachment_changes TEXT NOT NULL CHECK(json_valid(attachment_changes)),recorded_at TEXT NOT NULL,
 UNIQUE(target_id,version),UNIQUE(id,target_id,question_id,version),
 CHECK((version=1 AND origin IN ('baseline','created') AND before_snapshot IS NULL AND before_sha256 IS NULL AND reason IS NULL AND ((origin='baseline' AND actor_id IS NULL) OR (origin='created' AND actor_id IS NOT NULL))) OR (version>1 AND origin='edited' AND before_snapshot IS NOT NULL AND before_sha256 IS NOT NULL AND actor_id IS NOT NULL AND length(reason)>=5))
);
CREATE INDEX question_revision_parent ON question_revisions(question_id,recorded_at,id);
CREATE TRIGGER question_revision_no_update BEFORE UPDATE ON question_revisions BEGIN SELECT RAISE(ABORT,'Discussion revisions are immutable'); END;
CREATE TRIGGER question_revision_no_delete BEFORE DELETE ON question_revisions BEGIN SELECT RAISE(ABORT,'Discussion revisions cannot be deleted'); END;
CREATE TABLE question_content_heads(
 target_id TEXT PRIMARY KEY REFERENCES questions(id),question_id TEXT NOT NULL REFERENCES questions(id),revision_id TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>=1),hidden INTEGER NOT NULL DEFAULT 0 CHECK(hidden IN (0,1)),locked INTEGER NOT NULL DEFAULT 0 CHECK(locked IN (0,1)),
 FOREIGN KEY(revision_id,target_id,question_id,version) REFERENCES question_revisions(id,target_id,question_id,version)
);
CREATE TRIGGER question_head_transition BEFORE UPDATE ON question_content_heads WHEN
 NEW.target_id!=OLD.target_id OR NEW.question_id!=OLD.question_id OR NEW.version NOT IN (OLD.version,OLD.version+1) OR
 (NEW.version=OLD.version AND NEW.revision_id!=OLD.revision_id) OR (NEW.version=OLD.version+1 AND NEW.revision_id=OLD.revision_id)
 BEGIN SELECT RAISE(ABORT,'Invalid discussion head transition'); END;
CREATE TRIGGER question_head_no_delete BEFORE DELETE ON question_content_heads BEGIN SELECT RAISE(ABORT,'Discussion heads cannot be deleted'); END;
CREATE TABLE answer_revisions(
 id TEXT PRIMARY KEY,target_id TEXT NOT NULL REFERENCES answers(id),question_id TEXT NOT NULL REFERENCES questions(id),
 version INTEGER NOT NULL CHECK(version>=1),expected_version INTEGER NOT NULL CHECK(expected_version=version-1),
 origin TEXT NOT NULL CHECK(origin IN ('baseline','created','edited')),actor_id TEXT,reason TEXT,
 before_snapshot TEXT CHECK(before_snapshot IS NULL OR json_valid(before_snapshot)),after_snapshot TEXT NOT NULL CHECK(json_valid(after_snapshot)),
 before_sha256 TEXT CHECK(before_sha256 IS NULL OR length(before_sha256)=64),after_sha256 TEXT NOT NULL CHECK(length(after_sha256)=64),
 attachment_changes TEXT NOT NULL CHECK(json_valid(attachment_changes)),recorded_at TEXT NOT NULL,
 UNIQUE(target_id,version),UNIQUE(id,target_id,question_id,version),
 CHECK((version=1 AND origin IN ('baseline','created') AND before_snapshot IS NULL AND before_sha256 IS NULL AND reason IS NULL AND ((origin='baseline' AND actor_id IS NULL) OR (origin='created' AND actor_id IS NOT NULL))) OR (version>1 AND origin='edited' AND before_snapshot IS NOT NULL AND before_sha256 IS NOT NULL AND actor_id IS NOT NULL AND length(reason)>=5))
);
CREATE INDEX answer_revision_parent ON answer_revisions(question_id,recorded_at,id);
CREATE TRIGGER answer_revision_no_update BEFORE UPDATE ON answer_revisions BEGIN SELECT RAISE(ABORT,'Discussion revisions are immutable'); END;
CREATE TRIGGER answer_revision_no_delete BEFORE DELETE ON answer_revisions BEGIN SELECT RAISE(ABORT,'Discussion revisions cannot be deleted'); END;
CREATE TABLE answer_content_heads(
 target_id TEXT PRIMARY KEY REFERENCES answers(id),question_id TEXT NOT NULL REFERENCES questions(id),revision_id TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>=1),hidden INTEGER NOT NULL DEFAULT 0 CHECK(hidden IN (0,1)),locked INTEGER NOT NULL DEFAULT 0 CHECK(locked IN (0,1)),
 FOREIGN KEY(revision_id,target_id,question_id,version) REFERENCES answer_revisions(id,target_id,question_id,version)
);
CREATE TRIGGER answer_head_transition BEFORE UPDATE ON answer_content_heads WHEN
 NEW.target_id!=OLD.target_id OR NEW.question_id!=OLD.question_id OR NEW.version NOT IN (OLD.version,OLD.version+1) OR
 (NEW.version=OLD.version AND NEW.revision_id!=OLD.revision_id) OR (NEW.version=OLD.version+1 AND NEW.revision_id=OLD.revision_id)
 BEGIN SELECT RAISE(ABORT,'Invalid discussion head transition'); END;
CREATE TRIGGER answer_head_no_delete BEFORE DELETE ON answer_content_heads BEGIN SELECT RAISE(ABORT,'Discussion heads cannot be deleted'); END;
CREATE TRIGGER question_revision_parent BEFORE INSERT ON question_revisions WHEN NEW.question_id!=NEW.target_id BEGIN SELECT RAISE(ABORT,'Wrong question revision parent'); END;
CREATE TRIGGER answer_revision_parent BEFORE INSERT ON answer_revisions WHEN NOT EXISTS(SELECT 1 FROM answers WHERE id=NEW.target_id AND question_id=NEW.question_id) BEGIN SELECT RAISE(ABORT,'Wrong answer revision parent'); END;
CREATE TABLE question_attachments(
 id TEXT PRIMARY KEY,question_id TEXT NOT NULL REFERENCES questions(id),uploader_id TEXT NOT NULL REFERENCES users(id),
 filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 255),storage_key TEXT NOT NULL UNIQUE,
 media_type TEXT NOT NULL,size INTEGER NOT NULL CHECK(size BETWEEN 1 AND 2147483647),sha256 TEXT NOT NULL CHECK(length(sha256)=64 AND sha256 NOT GLOB '*[^a-f0-9]*'),created_at TEXT NOT NULL
);
CREATE INDEX question_attachment_parent ON question_attachments(question_id,created_at,id);
CREATE TRIGGER question_attachment_no_update BEFORE UPDATE ON question_attachments BEGIN SELECT RAISE(ABORT,'Attachment identities are immutable'); END;
CREATE TRIGGER question_attachment_no_delete BEFORE DELETE ON question_attachments BEGIN SELECT RAISE(ABORT,'Attachment identities cannot be deleted'); END;
CREATE TABLE question_attachment_states(
 attachment_id TEXT PRIMARY KEY REFERENCES question_attachments(id),status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','blocked','deleted')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1),inspection_reference TEXT NOT NULL DEFAULT '',reason TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,
 CHECK(status!='approved' OR length(inspection_reference)>=5)
);
CREATE TRIGGER question_attachment_state_transition BEFORE UPDATE ON question_attachment_states WHEN
 NEW.attachment_id!=OLD.attachment_id OR NEW.version!=OLD.version+1 OR NOT
 ((OLD.status='pending' AND NEW.status IN ('approved','rejected','blocked','deleted')) OR (OLD.status='approved' AND NEW.status IN ('blocked','deleted')) OR (OLD.status IN ('rejected','blocked') AND NEW.status='deleted'))
 BEGIN SELECT RAISE(ABORT,'Invalid attachment state transition'); END;
CREATE TRIGGER question_attachment_state_no_delete BEFORE DELETE ON question_attachment_states BEGIN SELECT RAISE(ABORT,'Attachment state cannot be deleted'); END;
INSERT INTO schema_migrations VALUES(18,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=18;
"""


SCHEMA_V19 = r"""
CREATE TABLE discussion_edit_requests(
 id TEXT PRIMARY KEY,actor_id TEXT NOT NULL REFERENCES users(id),actor_role TEXT NOT NULL CHECK(actor_role IN ('admin','coach','member')),
 kind TEXT NOT NULL CHECK(kind IN ('question','answer')),target_id TEXT NOT NULL,question_id TEXT NOT NULL REFERENCES questions(id),
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL CHECK(length(request_hash)=64),expected_version INTEGER NOT NULL CHECK(expected_version>=1),result_version INTEGER NOT NULL CHECK(result_version=expected_version+1),
 question_revision_id TEXT,answer_revision_id TEXT,result TEXT NOT NULL CHECK(json_valid(result)),created_at TEXT NOT NULL,
 UNIQUE(actor_id,request_key),
 FOREIGN KEY(question_revision_id,target_id,question_id,result_version) REFERENCES question_revisions(id,target_id,question_id,version),
 FOREIGN KEY(answer_revision_id,target_id,question_id,result_version) REFERENCES answer_revisions(id,target_id,question_id,version),
 CHECK((kind='question' AND question_revision_id IS NOT NULL AND answer_revision_id IS NULL) OR (kind='answer' AND answer_revision_id IS NOT NULL AND question_revision_id IS NULL))
);
CREATE INDEX discussion_request_revisions ON discussion_edit_requests(question_revision_id,answer_revision_id);
CREATE TRIGGER discussion_request_no_update BEFORE UPDATE ON discussion_edit_requests BEGIN SELECT RAISE(ABORT,'Discussion edit receipts are immutable'); END;
CREATE TRIGGER discussion_request_no_delete BEFORE DELETE ON discussion_edit_requests BEGIN SELECT RAISE(ABORT,'Discussion edit receipts cannot be deleted'); END;
CREATE UNIQUE INDEX question_attachment_payload_identity ON question_attachments(id,question_id,size,sha256);
CREATE TABLE question_attachment_payloads(
 attachment_id TEXT PRIMARY KEY,question_id TEXT NOT NULL REFERENCES questions(id),body BLOB NOT NULL,size INTEGER NOT NULL CHECK(size BETWEEN 1 AND 8388608),sha256 TEXT NOT NULL CHECK(length(sha256)=64),
 policy_reference TEXT NOT NULL CHECK(length(policy_reference)>=5),expires_at INTEGER NOT NULL,created_at TEXT NOT NULL,
 CHECK(typeof(body)='blob' AND length(body)=size),FOREIGN KEY(attachment_id,question_id,size,sha256) REFERENCES question_attachments(id,question_id,size,sha256)
);
CREATE TRIGGER question_attachment_payload_no_update BEFORE UPDATE ON question_attachment_payloads BEGIN SELECT RAISE(ABORT,'Attachment bytes are immutable'); END;
CREATE TRIGGER question_attachment_payload_delete_guard BEFORE DELETE ON question_attachment_payloads WHEN NOT EXISTS(SELECT 1 FROM question_attachment_states WHERE attachment_id=OLD.attachment_id AND status IN ('deleted','rejected')) BEGIN SELECT RAISE(ABORT,'Only rejected/deleted attachment bytes may be purged'); END;
CREATE TABLE question_attachment_operations(
 id TEXT PRIMARY KEY,attachment_id TEXT NOT NULL REFERENCES question_attachments(id),question_id TEXT NOT NULL REFERENCES questions(id),
 actor_id TEXT REFERENCES users(id),actor_role TEXT NOT NULL CHECK(actor_role IN ('admin','coach','member','system')),
 action TEXT NOT NULL CHECK(action IN ('upload','inspect','cancel','block','delete','expired')),
 expected_version INTEGER NOT NULL CHECK(expected_version>=0),result_version INTEGER NOT NULL CHECK(result_version=expected_version+1),result_status TEXT NOT NULL CHECK(result_status IN ('pending','approved','rejected','blocked','deleted')),
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL CHECK(length(request_hash)=64),reason TEXT NOT NULL,result TEXT NOT NULL CHECK(json_valid(result)),created_at TEXT NOT NULL,
 UNIQUE(actor_id,request_key),CHECK((actor_id IS NULL AND actor_role='system' AND action='expired') OR (actor_id IS NOT NULL AND actor_role!='system' AND action!='expired')),
 CHECK((action='upload' AND expected_version=0 AND result_status='pending') OR (action='inspect' AND expected_version>=1 AND result_status IN ('approved','rejected')) OR (action='block' AND expected_version>=1 AND result_status='blocked') OR (action IN ('cancel','delete','expired') AND expected_version>=1 AND result_status='deleted'))
);
CREATE TRIGGER question_attachment_operation_parent BEFORE INSERT ON question_attachment_operations WHEN NOT EXISTS(SELECT 1 FROM question_attachments a JOIN question_attachment_states s ON s.attachment_id=a.id WHERE a.id=NEW.attachment_id AND a.question_id=NEW.question_id AND s.version=NEW.result_version AND s.status=NEW.result_status) BEGIN SELECT RAISE(ABORT,'Attachment operation must match current parent/version/status'); END;
CREATE TRIGGER question_attachment_operation_no_update BEFORE UPDATE ON question_attachment_operations BEGIN SELECT RAISE(ABORT,'Attachment operation receipts are immutable'); END;
CREATE TRIGGER question_attachment_operation_no_delete BEFORE DELETE ON question_attachment_operations BEGIN SELECT RAISE(ABORT,'Attachment operation receipts cannot be deleted'); END;
INSERT INTO schema_migrations VALUES(19,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=19;
"""


SCHEMA_V20 = """
CREATE TABLE moderation_comment_heads(comment_id TEXT PRIMARY KEY REFERENCES comments(id),version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1),owner_deleted INTEGER NOT NULL DEFAULT 0 CHECK(owner_deleted IN (0,1)),origin TEXT NOT NULL CHECK(origin IN ('baseline','created')),observed_at TEXT NOT NULL);
CREATE TABLE moderation_report_observations(report_id TEXT PRIMARY KEY REFERENCES reports(id),post_id TEXT NOT NULL REFERENCES posts(id),origin TEXT NOT NULL CHECK(origin IN ('baseline','created')),snapshot TEXT NOT NULL CHECK(json_valid(snapshot)),content_hash TEXT NOT NULL CHECK(length(content_hash)=64),observed_at TEXT NOT NULL);
CREATE TABLE moderation_policy_versions(id TEXT PRIMARY KEY,mode TEXT NOT NULL CHECK(mode IN ('test','live')),edition TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,approval_reference TEXT NOT NULL DEFAULT '',effective_at TEXT,expires_at TEXT,content_hash TEXT NOT NULL CHECK(length(content_hash)=64),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,UNIQUE(mode,edition),UNIQUE(id,mode),CHECK(mode!='live' OR length(approval_reference)>=5),CHECK(effective_at IS NULL OR expires_at IS NULL OR expires_at>effective_at));
CREATE TABLE moderation_policy_states(policy_id TEXT PRIMARY KEY,mode TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','current','superseded','withdrawn')),version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1),reason TEXT NOT NULL DEFAULT '',published_at TEXT,updated_by TEXT NOT NULL REFERENCES users(id),updated_at TEXT NOT NULL,FOREIGN KEY(policy_id,mode) REFERENCES moderation_policy_versions(id,mode));
CREATE UNIQUE INDEX moderation_current_policy ON moderation_policy_states(mode) WHERE status='current';
CREATE TABLE moderation_actions(id TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('post','comment')),target_id TEXT NOT NULL,post_id TEXT NOT NULL REFERENCES posts(id),target_user_id TEXT NOT NULL REFERENCES users(id),actor_id TEXT NOT NULL REFERENCES users(id),actor_role TEXT NOT NULL CHECK(actor_role IN ('member','moderator','admin')),report_id TEXT REFERENCES reports(id),source_audit_id TEXT NOT NULL REFERENCES audit_logs(id),source_action_id TEXT REFERENCES moderation_actions(id),action TEXT NOT NULL CHECK(action IN ('hide','restore','lock','unlock','pin','unpin','comment_delete','comment_restore','report_dismiss')),origin TEXT NOT NULL CHECK(origin IN ('explicit','legacy_endpoint','appeal')),expected_version INTEGER NOT NULL CHECK(expected_version>=1),result_version INTEGER NOT NULL,reason TEXT NOT NULL CHECK(length(reason)>=5),before_snapshot TEXT NOT NULL CHECK(json_valid(before_snapshot)),after_snapshot TEXT NOT NULL CHECK(json_valid(after_snapshot)),before_hash TEXT NOT NULL CHECK(length(before_hash)=64),after_hash TEXT NOT NULL CHECK(length(after_hash)=64),policy_id TEXT REFERENCES moderation_policy_versions(id),policy_snapshot TEXT CHECK(policy_snapshot IS NULL OR json_valid(policy_snapshot)),created_at TEXT NOT NULL,CHECK((action='report_dismiss' AND result_version=expected_version) OR (action!='report_dismiss' AND result_version=expected_version+1)),CHECK((policy_id IS NULL)=(policy_snapshot IS NULL)));
CREATE INDEX moderation_action_target ON moderation_actions(kind,target_id,created_at,id);
CREATE INDEX moderation_action_owner ON moderation_actions(target_user_id,created_at,id);
CREATE TABLE moderation_appeals(id TEXT PRIMARY KEY,current_decision_id TEXT NOT NULL,action_id TEXT NOT NULL REFERENCES moderation_actions(id),appellant_id TEXT NOT NULL REFERENCES users(id),submission_reason TEXT NOT NULL CHECK(length(submission_reason)>=5),status TEXT NOT NULL DEFAULT 'submitted' CHECK(status IN ('submitted','reviewing','accepted','rejected','withdrawn')),version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1),reviewer_id TEXT REFERENCES users(id),review_snapshot TEXT NOT NULL CHECK(json_valid(review_snapshot)),review_policy_id TEXT REFERENCES moderation_policy_versions(id),review_policy_snapshot TEXT CHECK(review_policy_snapshot IS NULL OR json_valid(review_policy_snapshot)),needs_review INTEGER NOT NULL DEFAULT 0 CHECK(needs_review IN (0,1)),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,CHECK(status NOT IN ('reviewing','accepted','rejected') OR reviewer_id IS NOT NULL),CHECK((review_policy_id IS NULL)=(review_policy_snapshot IS NULL)),FOREIGN KEY(current_decision_id,id,version,status) REFERENCES moderation_decisions(id,appeal_id,result_version,result_status) DEFERRABLE INITIALLY DEFERRED);
CREATE UNIQUE INDEX moderation_one_open_appeal ON moderation_appeals(action_id,appellant_id) WHERE status IN ('submitted','reviewing');
CREATE INDEX moderation_appeal_queue ON moderation_appeals(status,updated_at,id);
CREATE TABLE moderation_decisions(id TEXT PRIMARY KEY,appeal_id TEXT NOT NULL REFERENCES moderation_appeals(id),actor_id TEXT NOT NULL REFERENCES users(id),actor_role TEXT NOT NULL CHECK(actor_role IN ('member','moderator','admin')),action TEXT NOT NULL CHECK(action IN ('submitted','assigned','rechecked','accepted','rejected','withdrawn','conflict')),expected_version INTEGER NOT NULL CHECK(expected_version>=0),result_version INTEGER NOT NULL CHECK(result_version=expected_version+1),result_status TEXT NOT NULL CHECK(result_status IN ('submitted','reviewing','accepted','rejected','withdrawn')),reason TEXT NOT NULL CHECK(length(reason)>=5),before_snapshot TEXT CHECK(before_snapshot IS NULL OR json_valid(before_snapshot)),after_snapshot TEXT NOT NULL CHECK(json_valid(after_snapshot)),before_hash TEXT CHECK(before_hash IS NULL OR length(before_hash)=64),after_hash TEXT NOT NULL CHECK(length(after_hash)=64),resolution_action_id TEXT REFERENCES moderation_actions(id),context TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(context)),created_at TEXT NOT NULL,UNIQUE(appeal_id,result_version),UNIQUE(id,appeal_id,result_version,result_status));
CREATE TABLE moderation_requests(id TEXT PRIMARY KEY,actor_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,request_hash TEXT NOT NULL CHECK(length(request_hash)=64),operation TEXT NOT NULL,target_id TEXT NOT NULL,action_id TEXT REFERENCES moderation_actions(id),decision_id TEXT REFERENCES moderation_decisions(id),policy_id TEXT REFERENCES moderation_policy_versions(id),result TEXT NOT NULL CHECK(json_valid(result)),http_status INTEGER NOT NULL CHECK(http_status IN (200,409)),created_at TEXT NOT NULL,UNIQUE(actor_id,request_key),CHECK((action_id IS NOT NULL)+(decision_id IS NOT NULL)+(policy_id IS NOT NULL)=1));
CREATE TRIGGER moderation_comment_head_version BEFORE UPDATE ON moderation_comment_heads WHEN NEW.comment_id!=OLD.comment_id OR NEW.origin!=OLD.origin OR NEW.observed_at!=OLD.observed_at OR NEW.version!=OLD.version+1 OR NEW.owner_deleted<OLD.owner_deleted BEGIN SELECT RAISE(ABORT,'Comment observation version must advance once'); END;
CREATE TRIGGER moderation_comment_head_no_delete BEFORE DELETE ON moderation_comment_heads BEGIN SELECT RAISE(ABORT,'Comment observation identity is retained'); END;
CREATE TRIGGER moderation_report_parent BEFORE INSERT ON moderation_report_observations WHEN NOT EXISTS(SELECT 1 FROM reports r WHERE r.id=NEW.report_id AND r.post_id=NEW.post_id) OR json_extract(NEW.snapshot,'$.id') IS NOT NEW.post_id OR json_extract(NEW.snapshot,'$.contentHash') IS NOT NEW.content_hash BEGIN SELECT RAISE(ABORT,'Report observation must match its parent'); END;
CREATE TRIGGER moderation_report_no_update BEFORE UPDATE ON moderation_report_observations BEGIN SELECT RAISE(ABORT,'Report observation is immutable'); END;
CREATE TRIGGER moderation_report_no_delete BEFORE DELETE ON moderation_report_observations BEGIN SELECT RAISE(ABORT,'Report observation is retained'); END;
CREATE TRIGGER moderation_policy_no_update BEFORE UPDATE ON moderation_policy_versions BEGIN SELECT RAISE(ABORT,'Policy edition content is immutable'); END;
CREATE TRIGGER moderation_policy_no_delete BEFORE DELETE ON moderation_policy_versions BEGIN SELECT RAISE(ABORT,'Policy edition is retained'); END;
CREATE TRIGGER moderation_policy_state_version BEFORE UPDATE ON moderation_policy_states WHEN NEW.policy_id!=OLD.policy_id OR NEW.mode!=OLD.mode OR NEW.version!=OLD.version+1 BEGIN SELECT RAISE(ABORT,'Policy lifecycle requires its next version'); END;
CREATE TRIGGER moderation_policy_state_no_delete BEFORE DELETE ON moderation_policy_states BEGIN SELECT RAISE(ABORT,'Policy lifecycle identity is retained'); END;
CREATE TRIGGER moderation_action_parent BEFORE INSERT ON moderation_actions WHEN json_extract(NEW.before_snapshot,'$.id') IS NOT NEW.target_id OR json_extract(NEW.after_snapshot,'$.id') IS NOT NEW.target_id OR json_extract(NEW.before_snapshot,'$.version') IS NOT NEW.expected_version OR json_extract(NEW.after_snapshot,'$.version') IS NOT NEW.result_version OR (NEW.kind='post' AND NOT EXISTS(SELECT 1 FROM posts p WHERE p.id=NEW.target_id AND p.id=NEW.post_id AND p.user_id=NEW.target_user_id AND p.version=NEW.result_version)) OR (NEW.kind='comment' AND NOT EXISTS(SELECT 1 FROM comments cm JOIN moderation_comment_heads h ON h.comment_id=cm.id WHERE cm.id=NEW.target_id AND cm.post_id=NEW.post_id AND cm.user_id=NEW.target_user_id AND h.version=NEW.result_version)) OR (NEW.report_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM reports r WHERE r.id=NEW.report_id AND r.post_id=NEW.post_id)) OR (NEW.policy_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM moderation_policy_versions p WHERE p.id=NEW.policy_id AND json_extract(NEW.policy_snapshot,'$.id')=p.id AND json_extract(NEW.policy_snapshot,'$.contentHash')=p.content_hash)) BEGIN SELECT RAISE(ABORT,'Moderation action must match current parent/version'); END;
CREATE TRIGGER moderation_action_no_update BEFORE UPDATE ON moderation_actions BEGIN SELECT RAISE(ABORT,'Moderation action is immutable'); END;
CREATE TRIGGER moderation_action_no_delete BEFORE DELETE ON moderation_actions BEGIN SELECT RAISE(ABORT,'Moderation action is retained'); END;
CREATE TRIGGER moderation_appeal_owner BEFORE INSERT ON moderation_appeals WHEN NOT EXISTS(SELECT 1 FROM moderation_actions a WHERE a.id=NEW.action_id AND a.target_user_id=NEW.appellant_id AND ((a.action='hide' AND json_extract(a.before_snapshot,'$.status')='published' AND json_extract(a.after_snapshot,'$.status')='hidden') OR (a.action='lock' AND json_extract(a.before_snapshot,'$.locked')=0 AND json_extract(a.after_snapshot,'$.locked')=1) OR (a.action='comment_delete' AND json_extract(a.before_snapshot,'$.status')='published' AND json_extract(a.after_snapshot,'$.status')='deleted'))) BEGIN SELECT RAISE(ABORT,'Only the subject of an adverse action may appeal'); END;
CREATE TRIGGER moderation_appeal_transition BEFORE UPDATE ON moderation_appeals WHEN NEW.id!=OLD.id OR NEW.action_id!=OLD.action_id OR NEW.appellant_id!=OLD.appellant_id OR NEW.submission_reason!=OLD.submission_reason OR NEW.created_at!=OLD.created_at OR NEW.version!=OLD.version+1 OR OLD.status IN ('accepted','rejected','withdrawn') OR (OLD.status='submitted' AND NEW.status NOT IN ('reviewing','withdrawn')) OR (OLD.status='reviewing' AND NEW.status NOT IN ('reviewing','accepted','rejected','withdrawn')) BEGIN SELECT RAISE(ABORT,'Invalid or terminal appeal transition'); END;
CREATE TRIGGER moderation_appeal_no_delete BEFORE DELETE ON moderation_appeals BEGIN SELECT RAISE(ABORT,'Appeal identity is retained'); END;
CREATE TRIGGER moderation_decision_parent BEFORE INSERT ON moderation_decisions WHEN NOT EXISTS(SELECT 1 FROM moderation_appeals a WHERE a.id=NEW.appeal_id AND a.current_decision_id=NEW.id AND a.version=NEW.result_version AND a.status=NEW.result_status) BEGIN SELECT RAISE(ABORT,'Appeal decision must match its current version'); END;
CREATE TRIGGER moderation_decision_no_update BEFORE UPDATE ON moderation_decisions BEGIN SELECT RAISE(ABORT,'Appeal decision is immutable'); END;
CREATE TRIGGER moderation_decision_no_delete BEFORE DELETE ON moderation_decisions BEGIN SELECT RAISE(ABORT,'Appeal decision is retained'); END;
CREATE TRIGGER moderation_request_no_update BEFORE UPDATE ON moderation_requests BEGIN SELECT RAISE(ABORT,'Moderation request receipt is immutable'); END;
CREATE TRIGGER moderation_request_no_delete BEFORE DELETE ON moderation_requests BEGIN SELECT RAISE(ABORT,'Moderation request receipt is retained'); END;
INSERT INTO schema_migrations VALUES(20,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=20;
"""


SCHEMA_V21 = """
CREATE UNIQUE INDEX notification_owner_identity ON notifications(id,user_id);
CREATE TABLE notification_signing_keys(id TEXT PRIMARY KEY CHECK(id='v1'),secret BLOB NOT NULL CHECK(length(secret)=32),created_at TEXT NOT NULL);
CREATE TABLE notification_intents(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL UNIQUE,recipient_id TEXT NOT NULL REFERENCES users(id),event_key TEXT NOT NULL,
 category TEXT NOT NULL CHECK(category IN ('learning','conversation','transaction','operation','marketing','legacy')),channel TEXT NOT NULL CHECK(channel IN ('site','email')),
 origin TEXT NOT NULL CHECK(origin IN ('baseline','compat','event')),reference_kind TEXT NOT NULL,reference_id TEXT,group_key TEXT,
 payload_hash TEXT CHECK(payload_hash IS NULL OR length(payload_hash)=64),decision TEXT NOT NULL CHECK(decision IN ('site_created','preference_off','marketing_unconfirmed','external_unconfigured')),
 preference_version INTEGER CHECK(preference_version IS NULL OR preference_version>=0),notification_id TEXT UNIQUE,observed_at TEXT NOT NULL,
 UNIQUE(recipient_id,event_key,channel),FOREIGN KEY(notification_id,recipient_id) REFERENCES notifications(id,user_id) DEFERRABLE INITIALLY DEFERRED,
 CHECK((decision='site_created')=(notification_id IS NOT NULL)),CHECK(decision!='site_created' OR channel='site'),
 CHECK((origin='event' AND payload_hash IS NOT NULL AND preference_version IS NOT NULL) OR (origin!='event' AND preference_version IS NULL AND decision='site_created')),
 CHECK((reference_kind='unknown')=(reference_id IS NULL))
);
CREATE INDEX notification_intent_owner ON notification_intents(recipient_id,channel,seq);
CREATE INDEX notification_intent_group ON notification_intents(recipient_id,category,group_key,seq);
CREATE TABLE notification_operations(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,request_hash TEXT NOT NULL CHECK(length(request_hash)=64),kind TEXT NOT NULL CHECK(kind IN ('preference','read')),
 category TEXT,channel TEXT,expected_version INTEGER,result_version INTEGER,result_enabled INTEGER,before_state TEXT CHECK(before_state IS NULL OR json_valid(before_state)),after_state TEXT CHECK(after_state IS NULL OR json_valid(after_state)),
 scope TEXT CHECK(scope IS NULL OR json_valid(scope)),changed_count INTEGER NOT NULL DEFAULT 0 CHECK(changed_count>=0),affected_hash TEXT CHECK(affected_hash IS NULL OR length(affected_hash)=64),read_at TEXT,
 result TEXT NOT NULL CHECK(json_valid(result)),created_at TEXT NOT NULL,UNIQUE(user_id,request_key),UNIQUE(id,user_id,category,channel,result_version,result_enabled),
 CHECK((kind='preference' AND category IS NOT NULL AND channel IS NOT NULL AND expected_version IS NOT NULL AND result_version IS NOT NULL AND result_enabled IS NOT NULL AND category IN ('learning','conversation','transaction','operation','marketing','legacy') AND channel IN ('site','email') AND expected_version>=0 AND result_version=expected_version+1 AND result_enabled IN (0,1) AND before_state IS NOT NULL AND after_state IS NOT NULL AND scope IS NULL AND read_at IS NULL) OR (kind='read' AND category IS NULL AND channel IS NULL AND expected_version IS NULL AND result_version IS NULL AND result_enabled IS NULL AND before_state IS NULL AND after_state IS NULL AND scope IS NOT NULL AND read_at IS NOT NULL AND affected_hash IS NOT NULL))
);
CREATE TABLE notification_preferences(
 user_id TEXT NOT NULL REFERENCES users(id),category TEXT NOT NULL CHECK(category IN ('learning','conversation','transaction','operation','marketing','legacy')),channel TEXT NOT NULL CHECK(channel IN ('site','email')),
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),version INTEGER NOT NULL CHECK(version>=1),current_operation_id TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(user_id,category,channel),
 FOREIGN KEY(current_operation_id,user_id,category,channel,version,enabled) REFERENCES notification_operations(id,user_id,category,channel,result_version,result_enabled) DEFERRABLE INITIALLY DEFERRED
);
CREATE TRIGGER notification_compat_intent AFTER INSERT ON notifications WHEN NOT EXISTS(SELECT 1 FROM notification_intents i WHERE i.notification_id=NEW.id) BEGIN
 INSERT INTO notification_intents(id,recipient_id,event_key,category,channel,origin,reference_kind,decision,notification_id,observed_at) VALUES('noticecompat_'||NEW.id,NEW.user_id,'compat/'||NEW.id,'legacy','site','compat','unknown','site_created',NEW.id,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
END;
CREATE TRIGGER notification_delivery_frozen BEFORE UPDATE ON notifications WHEN NEW.id IS NOT OLD.id OR NEW.user_id IS NOT OLD.user_id OR NEW.title IS NOT OLD.title OR NEW.body IS NOT OLD.body OR NEW.href IS NOT OLD.href OR NEW.created_at IS NOT OLD.created_at OR (OLD.read_at IS NOT NULL AND NEW.read_at IS NOT OLD.read_at) BEGIN SELECT RAISE(ABORT,'Notification content and existing read time are retained'); END;
CREATE TRIGGER notification_intent_no_update BEFORE UPDATE ON notification_intents BEGIN SELECT RAISE(ABORT,'Notification intent is immutable'); END;
CREATE TRIGGER notification_intent_no_delete BEFORE DELETE ON notification_intents BEGIN SELECT RAISE(ABORT,'Notification intent is retained'); END;
CREATE TRIGGER notification_operation_no_update BEFORE UPDATE ON notification_operations BEGIN SELECT RAISE(ABORT,'Notification operation receipt is immutable'); END;
CREATE TRIGGER notification_operation_no_delete BEFORE DELETE ON notification_operations BEGIN SELECT RAISE(ABORT,'Notification operation receipt is retained'); END;
CREATE TRIGGER notification_preference_version BEFORE UPDATE ON notification_preferences WHEN NEW.user_id!=OLD.user_id OR NEW.category!=OLD.category OR NEW.channel!=OLD.channel OR NEW.version!=OLD.version+1 BEGIN SELECT RAISE(ABORT,'Notification preference requires its next version'); END;
CREATE TRIGGER notification_preference_no_delete BEFORE DELETE ON notification_preferences BEGIN SELECT RAISE(ABORT,'Notification preference identity is retained'); END;
CREATE TRIGGER notification_preference_operation_parent BEFORE INSERT ON notification_operations WHEN NEW.kind='preference' AND (NOT EXISTS(SELECT 1 FROM notification_preferences p WHERE p.current_operation_id=NEW.id AND p.user_id=NEW.user_id AND p.category=NEW.category AND p.channel=NEW.channel AND p.version=NEW.result_version AND p.enabled=NEW.result_enabled) OR json_extract(NEW.before_state,'$.version') IS NOT NEW.expected_version OR json_extract(NEW.after_state,'$.version') IS NOT NEW.result_version OR json_extract(NEW.after_state,'$.enabled') IS NOT NEW.result_enabled) BEGIN SELECT RAISE(ABORT,'Preference receipt must match its current head'); END;
CREATE TRIGGER notification_signing_key_no_update BEFORE UPDATE ON notification_signing_keys BEGIN SELECT RAISE(ABORT,'Read scope signing identity is retained'); END;
CREATE TRIGGER notification_signing_key_no_delete BEFORE DELETE ON notification_signing_keys BEGIN SELECT RAISE(ABORT,'Read scope signing identity is retained'); END;
INSERT INTO schema_migrations VALUES(21,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=21;
"""


SCHEMA_V22 = """
CREATE UNIQUE INDEX community_comment_post_identity ON comments(id,post_id);
CREATE INDEX community_comment_post_page ON comments(post_id,status,created_at,id);
CREATE TABLE community_comment_heads(
 comment_id TEXT PRIMARY KEY,post_id TEXT NOT NULL REFERENCES posts(id),parent_id TEXT,
 content_version INTEGER NOT NULL DEFAULT 1 CHECK(content_version>=1),current_revision_id TEXT,
 origin TEXT NOT NULL CHECK(origin IN ('baseline','compat','created')),observed_at TEXT NOT NULL,
 FOREIGN KEY(comment_id,post_id) REFERENCES comments(id,post_id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(parent_id,post_id) REFERENCES comments(id,post_id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(current_revision_id,comment_id,content_version) REFERENCES community_comment_revisions(id,comment_id,version) DEFERRABLE INITIALLY DEFERRED,
 CHECK(parent_id IS NULL OR parent_id!=comment_id),CHECK(current_revision_id IS NOT NULL OR (origin IN ('baseline','compat') AND content_version=1))
);
CREATE INDEX community_comment_parent ON community_comment_heads(post_id,parent_id,comment_id);
CREATE TABLE community_comment_revisions(
 id TEXT PRIMARY KEY,comment_id TEXT NOT NULL REFERENCES comments(id),version INTEGER NOT NULL CHECK(version>=1),
 origin TEXT NOT NULL CHECK(origin IN ('baseline','created','edited')),previous_revision_id TEXT REFERENCES community_comment_revisions(id),
 actor_id TEXT REFERENCES users(id),body TEXT NOT NULL,body_hash TEXT NOT NULL CHECK(length(body_hash)=64),reason TEXT,event_at TEXT,observed_at TEXT NOT NULL,
 UNIQUE(comment_id,version),UNIQUE(id,comment_id,version),
 CHECK((origin='baseline' AND version=1 AND previous_revision_id IS NULL AND actor_id IS NULL AND reason IS NULL AND event_at IS NULL) OR (origin='created' AND version=1 AND previous_revision_id IS NULL AND actor_id IS NOT NULL AND reason IS NULL AND event_at IS NOT NULL AND length(body) BETWEEN 2 AND 5000) OR (origin='edited' AND version>1 AND previous_revision_id IS NOT NULL AND actor_id IS NOT NULL AND reason IS NOT NULL AND length(reason) BETWEEN 3 AND 2000 AND event_at IS NOT NULL AND length(body) BETWEEN 2 AND 5000))
);
CREATE TABLE community_comment_operations(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 kind TEXT NOT NULL CHECK(kind IN ('create','edit')),comment_id TEXT NOT NULL,post_id TEXT NOT NULL,expected_version INTEGER,result_version INTEGER NOT NULL,revision_id TEXT NOT NULL,
 result TEXT NOT NULL CHECK(json_valid(result)),created_at TEXT NOT NULL,UNIQUE(user_id,request_key),UNIQUE(comment_id,result_version),
 FOREIGN KEY(comment_id,post_id) REFERENCES comments(id,post_id),FOREIGN KEY(revision_id,comment_id,result_version) REFERENCES community_comment_revisions(id,comment_id,version),
 CHECK((kind='create' AND expected_version IS NULL AND result_version=1) OR (kind='edit' AND expected_version IS NOT NULL AND expected_version>=1 AND result_version=expected_version+1))
);
CREATE TABLE community_block_operations(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),target_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 expected_version INTEGER NOT NULL CHECK(expected_version>=0),result_version INTEGER NOT NULL CHECK(result_version=expected_version+1),before_blocked INTEGER NOT NULL CHECK(before_blocked IN (0,1)),after_blocked INTEGER NOT NULL CHECK(after_blocked IN (0,1)),
 previous_operation_id TEXT REFERENCES community_block_operations(id),result TEXT NOT NULL CHECK(json_valid(result)),created_at TEXT NOT NULL,
 UNIQUE(user_id,request_key),UNIQUE(user_id,target_id,result_version),UNIQUE(id,user_id,target_id,result_version,after_blocked),CHECK(user_id!=target_id),CHECK((expected_version=0 AND previous_operation_id IS NULL AND before_blocked=0) OR (expected_version>0 AND previous_operation_id IS NOT NULL))
);
CREATE TABLE community_block_heads(
 user_id TEXT NOT NULL REFERENCES users(id),target_id TEXT NOT NULL REFERENCES users(id),blocked INTEGER NOT NULL CHECK(blocked IN (0,1)),version INTEGER NOT NULL CHECK(version>=1),current_operation_id TEXT NOT NULL,updated_at TEXT NOT NULL,
 PRIMARY KEY(user_id,target_id),CHECK(user_id!=target_id),FOREIGN KEY(current_operation_id,user_id,target_id,version,blocked) REFERENCES community_block_operations(id,user_id,target_id,result_version,after_blocked) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX community_block_reverse ON community_block_heads(target_id,user_id,blocked);
CREATE TRIGGER community_comment_parent_insert BEFORE INSERT ON community_comment_heads WHEN NEW.parent_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM community_comment_heads h JOIN comments cm ON cm.id=h.comment_id LEFT JOIN moderation_comment_heads mh ON mh.comment_id=cm.id WHERE h.comment_id=NEW.parent_id AND h.post_id=NEW.post_id AND h.parent_id IS NULL AND cm.status='published' AND COALESCE(mh.owner_deleted,0)=0) BEGIN SELECT RAISE(ABORT,'Reply requires a visible root in the same post'); END;
CREATE TRIGGER community_comment_head_update BEFORE UPDATE ON community_comment_heads WHEN NEW.comment_id IS NOT OLD.comment_id OR NEW.post_id IS NOT OLD.post_id OR NEW.parent_id IS NOT OLD.parent_id OR NEW.origin IS NOT OLD.origin OR NEW.observed_at IS NOT OLD.observed_at OR NEW.content_version!=OLD.content_version+1 OR NEW.current_revision_id IS NULL OR NEW.current_revision_id IS OLD.current_revision_id BEGIN SELECT RAISE(ABORT,'Comment relationship is immutable; content requires its next revision'); END;
CREATE TRIGGER community_comment_head_no_delete BEFORE DELETE ON community_comment_heads BEGIN SELECT RAISE(ABORT,'Comment identity is retained'); END;
CREATE TRIGGER community_comment_compat AFTER INSERT ON comments WHEN NOT EXISTS(SELECT 1 FROM community_comment_heads WHERE comment_id=NEW.id) BEGIN INSERT INTO community_comment_heads(comment_id,post_id,origin,observed_at) VALUES(NEW.id,NEW.post_id,'compat',strftime('%Y-%m-%dT%H:%M:%SZ','now')); END;
CREATE TRIGGER community_comment_revision_parent BEFORE INSERT ON community_comment_revisions WHEN NOT EXISTS(SELECT 1 FROM community_comment_heads h JOIN comments cm ON cm.id=h.comment_id LEFT JOIN moderation_comment_heads mh ON mh.comment_id=cm.id WHERE h.comment_id=NEW.comment_id AND cm.body=NEW.body AND cm.status='published' AND COALESCE(mh.owner_deleted,0)=0 AND ((NEW.origin='baseline' AND h.origin IN ('baseline','compat') AND h.content_version=1 AND h.current_revision_id IS NULL) OR (NEW.origin!='baseline' AND h.current_revision_id=NEW.id AND h.content_version=NEW.version AND cm.user_id=NEW.actor_id))) OR (NEW.origin='edited' AND NOT EXISTS(SELECT 1 FROM community_comment_revisions p WHERE p.id=NEW.previous_revision_id AND p.comment_id=NEW.comment_id AND p.version=NEW.version-1)) BEGIN SELECT RAISE(ABORT,'Revision must match current content and its previous revision'); END;
CREATE TRIGGER community_comment_revision_no_update BEFORE UPDATE ON community_comment_revisions BEGIN SELECT RAISE(ABORT,'Comment revision is immutable'); END;
CREATE TRIGGER community_comment_revision_no_delete BEFORE DELETE ON community_comment_revisions BEGIN SELECT RAISE(ABORT,'Comment revision is retained'); END;
CREATE TRIGGER community_comment_operation_parent BEFORE INSERT ON community_comment_operations WHEN NOT EXISTS(SELECT 1 FROM comments cm JOIN community_comment_heads h ON h.comment_id=cm.id WHERE cm.id=NEW.comment_id AND cm.post_id=NEW.post_id AND cm.user_id=NEW.user_id AND h.content_version=NEW.result_version AND h.current_revision_id=NEW.revision_id) OR json_extract(NEW.result,'$.id') IS NOT NEW.id OR json_extract(NEW.result,'$.commentId') IS NOT NEW.comment_id OR json_extract(NEW.result,'$.postId') IS NOT NEW.post_id OR json_extract(NEW.result,'$.contentVersion') IS NOT NEW.result_version OR json_extract(NEW.result,'$.revisionId') IS NOT NEW.revision_id BEGIN SELECT RAISE(ABORT,'Comment receipt must match its current owner and revision'); END;
CREATE TRIGGER community_comment_operation_no_update BEFORE UPDATE ON community_comment_operations BEGIN SELECT RAISE(ABORT,'Comment receipt is immutable'); END;
CREATE TRIGGER community_comment_operation_no_delete BEFORE DELETE ON community_comment_operations BEGIN SELECT RAISE(ABORT,'Comment receipt is retained'); END;
CREATE TRIGGER community_block_head_update BEFORE UPDATE ON community_block_heads WHEN NEW.user_id IS NOT OLD.user_id OR NEW.target_id IS NOT OLD.target_id OR NEW.version!=OLD.version+1 BEGIN SELECT RAISE(ABORT,'Community block requires its next version'); END;
CREATE TRIGGER community_block_head_no_delete BEFORE DELETE ON community_block_heads BEGIN SELECT RAISE(ABORT,'Community block intent is retained'); END;
CREATE TRIGGER community_block_operation_parent BEFORE INSERT ON community_block_operations WHEN NOT EXISTS(SELECT 1 FROM community_block_heads h WHERE h.current_operation_id=NEW.id AND h.user_id=NEW.user_id AND h.target_id=NEW.target_id AND h.version=NEW.result_version AND h.blocked=NEW.after_blocked) OR (NEW.expected_version>0 AND NOT EXISTS(SELECT 1 FROM community_block_operations p WHERE p.id=NEW.previous_operation_id AND p.user_id=NEW.user_id AND p.target_id=NEW.target_id AND p.result_version=NEW.expected_version AND p.after_blocked=NEW.before_blocked)) OR json_extract(NEW.result,'$.id') IS NOT NEW.id OR json_extract(NEW.result,'$.targetId') IS NOT NEW.target_id OR json_extract(NEW.result,'$.version') IS NOT NEW.result_version OR json_extract(NEW.result,'$.blocked') IS NOT NEW.after_blocked BEGIN SELECT RAISE(ABORT,'Community block receipt must match current head and previous intent'); END;
CREATE TRIGGER community_block_operation_no_update BEFORE UPDATE ON community_block_operations BEGIN SELECT RAISE(ABORT,'Community block receipt is immutable'); END;
CREATE TRIGGER community_block_operation_no_delete BEFORE DELETE ON community_block_operations BEGIN SELECT RAISE(ABORT,'Community block receipt is retained'); END;
INSERT INTO schema_migrations VALUES(22,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=22;
"""


# Durable checkout preparation receipts; no historical consent/order backfill.
SCHEMA_V23 = """
CREATE TABLE checkout_preparations(
 user_id TEXT NOT NULL REFERENCES users(id),
 request_key TEXT NOT NULL CHECK(length(request_key) BETWEEN 12 AND 120),
 request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 request_json TEXT NOT NULL CHECK(json_valid(request_json) AND json_extract(request_json,'$.schemaVersion')=1),
 order_id TEXT NOT NULL REFERENCES purchase_orders(id),
 created_at TEXT NOT NULL,
 PRIMARY KEY(user_id,request_key)
);
CREATE INDEX checkout_preparation_order ON checkout_preparations(order_id);
CREATE TRIGGER checkout_preparation_parent BEFORE INSERT ON checkout_preparations WHEN NOT EXISTS(SELECT 1 FROM purchase_orders o JOIN order_policy_sets p ON p.order_id=o.id WHERE o.id=NEW.order_id AND o.user_id=NEW.user_id AND o.course_id=json_extract(NEW.request_json,'$.courseId') AND p.fingerprint=json_extract(NEW.request_json,'$.policyDigest') AND json_extract(p.course_snapshot,'$.product.type')='course') OR json_type(NEW.request_json,'$.acceptedPolicyIds') IS NOT 'array' OR json_array_length(NEW.request_json,'$.acceptedPolicyIds')!=4 OR (SELECT count(DISTINCT value) FROM json_each(NEW.request_json,'$.acceptedPolicyIds'))!=4 OR EXISTS(SELECT 1 FROM json_each(NEW.request_json,'$.acceptedPolicyIds') a WHERE NOT EXISTS(SELECT 1 FROM consents co WHERE co.order_id=NEW.order_id AND co.user_id=NEW.user_id AND co.context='purchase' AND co.policy_version_id=a.value)) BEGIN SELECT RAISE(ABORT,'Preparation must match its owner, product and sealed consent'); END;
CREATE TRIGGER checkout_preparation_no_update BEFORE UPDATE ON checkout_preparations BEGIN SELECT RAISE(ABORT,'Checkout preparation is immutable'); END;
CREATE TRIGGER checkout_preparation_no_delete BEFORE DELETE ON checkout_preparations BEGIN SELECT RAISE(ABORT,'Checkout preparation is retained'); END;
INSERT INTO schema_migrations VALUES(23,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=23;
"""


def _execute_schema(connection, script):
    # executescript() commits an existing transaction before running its input.
    # Execute complete SQL statements ourselves to retain the version-read lock.
    statement = ""
    for character in script:
        statement += character
        if character == ";" and sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("A schema migration contains an incomplete SQL statement")


def migrate(connection):
    # Another server can finish upgrading and issue new grants while this server
    # waits. Re-read the version only after owning the write transaction; replaying
    # a cached v5 backfill could otherwise create new indefinite legacy grants.
    connection.execute("BEGIN IMMEDIATE")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > LATEST_VERSION:
            raise RuntimeError(
                "Database schema is newer than this application. Upgrade the application; do not reset the database."
            )
        for target, script in (
            (3, SCHEMA_V3),
            (4, SCHEMA_V4),
            (5, SCHEMA_V5),
            (6, SCHEMA_V6),
            (7, SCHEMA_V7),
            (8, SCHEMA_V8),
            (9, SCHEMA_V9),
            (10, SCHEMA_V10),
            (11, SCHEMA_V11),
            (12, SCHEMA_V12),
            (13, SCHEMA_V13),
            (14, SCHEMA_V14),
            (15, SCHEMA_V15),
            (16, SCHEMA_V16),
            (17, SCHEMA_V17),
            (18, SCHEMA_V18),
            (19, SCHEMA_V19),
            (20, SCHEMA_V20),
            (21, SCHEMA_V21),
            (22, SCHEMA_V22),
            (23, SCHEMA_V23),
            (24, SCHEMA_V24),
            (25, SCHEMA_V25),
            (26, SCHEMA_V26),
            (27, SCHEMA_V27),
            (28, SCHEMA_V28),
            (29, SCHEMA_V29),
            (30, SCHEMA_V30),
            (31, SCHEMA_V31),
            (32, SCHEMA_V32), (33, SCHEMA_V33),
        ):
            if version < target:
                if target == 26:
                    # Some SQLite builds default to legacy rename semantics.
                    # Cyclic FK targets must follow both temporary table names,
                    # even for a caller whose foreign_keys pragma is OFF.
                    legacy = connection.execute("PRAGMA legacy_alter_table").fetchone()[0]
                    from .reconciliation_runtime_records import REBUILT_QUEUE_OBJECTS

                    # Keep any operator-added queue index/trigger too. Rebuilding
                    # a table must not silently discard unrelated local intent.
                    extra_objects = [row for row in connection.execute("SELECT name,sql FROM sqlite_master WHERE tbl_name IN ('reconciliation_jobs','reconciliation_attempts') AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall() if row[0] not in REBUILT_QUEUE_OBJECTS]
                    try:
                        _execute_schema(connection, script)
                        for name, sql in extra_objects:
                            if connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone():
                                raise RuntimeError("A queue object conflicts with the new schema; preserve and review it before upgrade")
                            connection.execute(sql)
                    finally:
                        connection.execute("PRAGMA legacy_alter_table=" + str(int(legacy)))
                else:
                    _execute_schema(connection, script)
                if target == 18:
                    from .discussion_records import backfill

                    backfill(connection)
                if target == 20:
                    from .moderation_records import backfill as moderation_backfill

                    moderation_backfill(connection)
                if target == 21:
                    from .notification_records import backfill as notification_backfill

                    notification_backfill(connection)
                if target == 22:
                    from .community_records import backfill as community_backfill

                    community_backfill(connection)
                if target == 24:
                    from .refund_records import backfill as refund_backfill

                    refund_backfill(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
