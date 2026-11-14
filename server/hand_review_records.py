"""Additive v30: private review facts; no legacy review is converted at startup."""

HAND_REVIEW_TABLES = frozenset({
    'hand_review_heads', 'hand_review_revisions', 'hand_review_actions',
    'hand_review_assignments', 'hand_review_responses', 'hand_review_events',
    'hand_review_commands', 'hand_review_attachments',
    'hand_review_attachment_payloads', 'hand_review_attachment_states',
    'hand_review_revision_attachments',
})

SCHEMA_V30 = """
CREATE TABLE hand_review_heads(
 review_id TEXT PRIMARY KEY REFERENCES reviews(id),
 baseline TEXT NOT NULL CHECK(json_valid(baseline)),baseline_hash TEXT NOT NULL CHECK(length(baseline_hash)=64),
 revision_id TEXT NOT NULL,assignment_id TEXT,response_id TEXT,
 state TEXT NOT NULL CHECK(state IN ('submitted','needs_information','in_review','answered','cancelled')),
 version INTEGER NOT NULL CHECK(version>0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 FOREIGN KEY(review_id,revision_id) REFERENCES hand_review_revisions(review_id,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(review_id,assignment_id) REFERENCES hand_review_assignments(review_id,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(review_id,response_id) REFERENCES hand_review_responses(review_id,id) DEFERRABLE INITIALLY DEFERRED,
 CHECK(state!='answered' OR response_id IS NOT NULL)
);
CREATE TABLE hand_review_revisions(
 id TEXT PRIMARY KEY,review_id TEXT NOT NULL REFERENCES hand_review_heads(review_id),
 revision_no INTEGER NOT NULL CHECK(revision_no>0),kind TEXT NOT NULL CHECK(kind IN ('legacy','structured')),
 payload TEXT NOT NULL CHECK(json_valid(payload)),payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
 author_id TEXT NOT NULL REFERENCES users(id),reason TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(review_id,id),UNIQUE(review_id,revision_no)
);
CREATE TABLE hand_review_actions(
 revision_id TEXT NOT NULL REFERENCES hand_review_revisions(id),ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 200),
 street TEXT NOT NULL CHECK(street IN ('preflop','flop','turn','river')),position TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('post_blind','post_ante','check','bet','call','raise','fold','all_in')),
 amount_milli_bb INTEGER NOT NULL CHECK(typeof(amount_milli_bb)='integer' AND amount_milli_bb BETWEEN 0 AND 20000000),
 note TEXT NOT NULL,PRIMARY KEY(revision_id,ordinal),
 CHECK((action IN ('check','fold') AND amount_milli_bb=0) OR (action NOT IN ('check','fold') AND amount_milli_bb>0))
);
CREATE TABLE hand_review_assignments(
 id TEXT PRIMARY KEY,review_id TEXT NOT NULL REFERENCES hand_review_heads(review_id),
 coach_id TEXT NOT NULL REFERENCES coach_profiles(id),owner_id TEXT NOT NULL REFERENCES users(id),
 profile_revision_id TEXT NOT NULL REFERENCES coach_profile_revisions(id),profile_version INTEGER NOT NULL CHECK(profile_version>0),
 actor_id TEXT NOT NULL REFERENCES users(id),reason TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(review_id,id)
);
CREATE TABLE hand_review_responses(
 id TEXT PRIMARY KEY,review_id TEXT NOT NULL REFERENCES hand_review_heads(review_id),revision_id TEXT NOT NULL,
 assignment_id TEXT,kind TEXT NOT NULL CHECK(kind IN ('legacy','text')),body TEXT NOT NULL,
 author_id TEXT REFERENCES users(id),created_at TEXT NOT NULL,UNIQUE(review_id,id),
 CHECK(kind='legacy' OR author_id IS NOT NULL),
 FOREIGN KEY(review_id,revision_id) REFERENCES hand_review_revisions(review_id,id),
 FOREIGN KEY(review_id,assignment_id) REFERENCES hand_review_assignments(review_id,id)
);
CREATE TABLE hand_review_events(
 id TEXT PRIMARY KEY,review_id TEXT NOT NULL REFERENCES hand_review_heads(review_id),
 version INTEGER NOT NULL CHECK(version>0),action TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES users(id),
 reason TEXT NOT NULL,payload TEXT NOT NULL CHECK(json_valid(payload)),created_at TEXT NOT NULL,UNIQUE(review_id,version)
);
CREATE TABLE hand_review_commands(
 id TEXT PRIMARY KEY,actor_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,
 command TEXT NOT NULL,request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 review_id TEXT NOT NULL REFERENCES hand_review_heads(review_id),result TEXT NOT NULL CHECK(json_valid(result)),created_at TEXT NOT NULL,
 UNIQUE(actor_id,request_key)
);
CREATE TABLE hand_review_attachments(
 id TEXT PRIMARY KEY,review_id TEXT NOT NULL REFERENCES hand_review_heads(review_id),uploader_id TEXT NOT NULL REFERENCES users(id),
 filename TEXT NOT NULL,media_type TEXT NOT NULL,size INTEGER NOT NULL CHECK(typeof(size)='integer' AND size BETWEEN 1 AND 8388608),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),policy_hash TEXT NOT NULL CHECK(length(policy_hash)=64),created_at TEXT NOT NULL,
 UNIQUE(review_id,id),UNIQUE(id,review_id,size,sha256)
);
CREATE TABLE hand_review_attachment_payloads(
 attachment_id TEXT PRIMARY KEY,review_id TEXT NOT NULL,body BLOB NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,
 expires_at INTEGER NOT NULL CHECK(expires_at>0),
 CHECK(typeof(body)='blob' AND length(body)=size),
 FOREIGN KEY(attachment_id,review_id,size,sha256) REFERENCES hand_review_attachments(id,review_id,size,sha256)
);
CREATE TABLE hand_review_attachment_states(
 attachment_id TEXT PRIMARY KEY REFERENCES hand_review_attachments(id),
 state TEXT NOT NULL CHECK(state IN ('pending','approved','rejected','blocked','deleted','expired')),
 version INTEGER NOT NULL CHECK(version>0),inspection_reference TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL
);
CREATE TABLE hand_review_revision_attachments(
 review_id TEXT NOT NULL,revision_id TEXT NOT NULL,attachment_id TEXT NOT NULL,
 PRIMARY KEY(revision_id,attachment_id),
 FOREIGN KEY(review_id,revision_id) REFERENCES hand_review_revisions(review_id,id),
 FOREIGN KEY(review_id,attachment_id) REFERENCES hand_review_attachments(review_id,id)
);
CREATE INDEX hand_review_queue ON hand_review_heads(state,updated_at,review_id);
CREATE INDEX hand_review_assigned ON hand_review_heads(assignment_id,state);
CREATE INDEX hand_review_attachment_owner ON hand_review_attachments(uploader_id,created_at,id);
CREATE TRIGGER hand_review_head_identity BEFORE UPDATE ON hand_review_heads
 WHEN NEW.review_id!=OLD.review_id OR NEW.baseline!=OLD.baseline OR NEW.baseline_hash!=OLD.baseline_hash OR NEW.created_at!=OLD.created_at OR NEW.version!=OLD.version+1
 BEGIN SELECT RAISE(ABORT,'Review identity and baseline are immutable; CAS advances once'); END;
CREATE TRIGGER hand_review_head_terminal BEFORE UPDATE ON hand_review_heads
 WHEN OLD.state IN ('answered','cancelled') AND (NEW.state!=OLD.state OR NEW.revision_id!=OLD.revision_id OR (NEW.assignment_id IS NOT OLD.assignment_id AND NEW.assignment_id IS NOT NULL) OR NEW.response_id IS NOT OLD.response_id)
 BEGIN SELECT RAISE(ABORT,'Terminal review cannot reopen'); END;
CREATE TRIGGER hand_review_asset_state BEFORE UPDATE ON hand_review_attachment_states
 WHEN NEW.attachment_id!=OLD.attachment_id OR NEW.version!=OLD.version+1
 BEGIN SELECT RAISE(ABORT,'Attachment state requires current version'); END;
CREATE TRIGGER hand_review_payload_delete BEFORE DELETE ON hand_review_attachment_payloads
 WHEN NOT EXISTS(SELECT 1 FROM hand_review_attachment_states s WHERE s.attachment_id=OLD.attachment_id AND s.state IN ('rejected','deleted','expired'))
 BEGIN SELECT RAISE(ABORT,'Payload removal needs a recorded terminal state'); END;
CREATE TRIGGER hand_review_legacy_frozen BEFORE UPDATE ON reviews
 WHEN EXISTS(SELECT 1 FROM hand_review_heads WHERE review_id=OLD.id)
 BEGIN SELECT RAISE(ABORT,'Managed legacy review remains an immutable baseline'); END;
CREATE TRIGGER hand_review_legacy_retained BEFORE DELETE ON reviews
 WHEN EXISTS(SELECT 1 FROM hand_review_heads WHERE review_id=OLD.id)
 BEGIN SELECT RAISE(ABORT,'Managed legacy review identity is retained'); END;
"""

for _table in sorted(HAND_REVIEW_TABLES - {'hand_review_heads', 'hand_review_attachment_states', 'hand_review_attachment_payloads'}):
    for _operation in ('UPDATE', 'DELETE'):
        SCHEMA_V30 += "CREATE TRIGGER " + _table + '_no_' + _operation.lower() + ' BEFORE ' + _operation + ' ON ' + _table + " BEGIN SELECT RAISE(ABORT,'Review facts are immutable'); END;\n"
for _table in ('hand_review_heads', 'hand_review_attachment_states'):
    SCHEMA_V30 += 'CREATE TRIGGER ' + _table + '_no_delete BEFORE DELETE ON ' + _table + " BEGIN SELECT RAISE(ABORT,'Review identities are retained'); END;\n"
SCHEMA_V30 += "CREATE TRIGGER hand_review_payload_no_update BEFORE UPDATE ON hand_review_attachment_payloads BEGIN SELECT RAISE(ABORT,'Review bytes are immutable'); END;\n"
SCHEMA_V30 += "INSERT INTO schema_migrations VALUES(30,strftime('%Y-%m-%dT%H:%M:%SZ','now'));\nPRAGMA user_version=30;\n"
