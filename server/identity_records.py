"""Additive v32 identity state. Applied SQL3–31 is retained verbatim."""

IDENTITY_TABLES = {
    'identity_states', 'identity_challenges', 'identity_challenge_states',
    'identity_events', 'identity_commands', 'identity_notices', 'identity_mail_links',
}

SCHEMA_V32 = """
CREATE TABLE identity_states(
 user_id TEXT PRIMARY KEY REFERENCES users(id),
 version INTEGER NOT NULL CHECK(version>=0),
 credential_epoch INTEGER NOT NULL DEFAULT 0 CHECK(credential_epoch>=0),
 verified_email_hash TEXT CHECK(verified_email_hash IS NULL OR length(verified_email_hash)=64),
 verified_at INTEGER,
 last_event_id TEXT REFERENCES identity_events(id) DEFERRABLE INITIALLY DEFERRED,
 updated_at INTEGER NOT NULL,
 CHECK((verified_email_hash IS NULL)=(verified_at IS NULL)),
 CHECK((version=0)=(last_event_id IS NULL))
);
CREATE TABLE identity_challenges(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),
 purpose TEXT NOT NULL CHECK(purpose IN ('verify_email','change_email','reset_password')),
 source_email TEXT NOT NULL,candidate_email TEXT,
 credential_stamp TEXT NOT NULL CHECK(length(credential_stamp)=64),
 credential_epoch INTEGER NOT NULL CHECK(credential_epoch>=0),
 key_ref TEXT NOT NULL CHECK(length(key_ref) BETWEEN 1 AND 16),
 nonce TEXT NOT NULL CHECK(length(nonce)=32),
 token_hash TEXT NOT NULL UNIQUE CHECK(length(token_hash)=64),
 created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL CHECK(expires_at>created_at AND expires_at<=created_at+3600),
 CHECK((purpose='change_email')=(candidate_email IS NOT NULL)),
 CHECK(candidate_email IS NULL OR candidate_email!=source_email)
);
CREATE INDEX identity_challenge_owner ON identity_challenges(user_id,purpose,created_at,id);
CREATE TABLE identity_challenge_states(
 challenge_id TEXT PRIMARY KEY REFERENCES identity_challenges(id),
 version INTEGER NOT NULL CHECK(version>=1),
 state TEXT NOT NULL CHECK(state IN ('pending','consumed','revoked','expired')),
 last_event_id TEXT NOT NULL REFERENCES identity_events(id) DEFERRABLE INITIALLY DEFERRED,
 used_at INTEGER,revoked_at INTEGER,updated_at INTEGER NOT NULL,
 CHECK((state='consumed')=(used_at IS NOT NULL)),
 CHECK((state IN ('revoked','expired'))=(revoked_at IS NOT NULL))
);
CREATE TABLE identity_events(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),
 version INTEGER NOT NULL CHECK(version>=1),
 previous_event_id TEXT REFERENCES identity_events(id),
 command_id TEXT NOT NULL REFERENCES identity_commands(id) DEFERRABLE INITIALLY DEFERRED,
 kind TEXT NOT NULL CHECK(kind IN ('profile_saved','challenge_issued','challenge_revoked','challenge_expired','email_verified','email_changed','password_reset')),
 challenge_id TEXT REFERENCES identity_challenges(id),
 challenge_version INTEGER,previous_challenge_event_id TEXT REFERENCES identity_events(id),
 before_state TEXT,after_state TEXT,
 credential_epoch INTEGER NOT NULL CHECK(credential_epoch>=0),
 verified_email_hash TEXT,verified_at INTEGER,
 before_email_hash TEXT NOT NULL CHECK(length(before_email_hash)=64),
 after_email_hash TEXT NOT NULL CHECK(length(after_email_hash)=64),
 code TEXT NOT NULL CHECK(code IN ('profile_saved','issued','superseded','expired','security_change','verified','changed','reset')),
 created_at INTEGER NOT NULL,
 UNIQUE(user_id,version),UNIQUE(challenge_id,challenge_version),
 CHECK((challenge_id IS NULL)=(challenge_version IS NULL)),
 CHECK((verified_email_hash IS NULL)=(verified_at IS NULL)),
 CHECK((kind='profile_saved')=(challenge_id IS NULL)),
 CHECK((kind='profile_saved' AND before_state IS NULL AND after_state IS NULL) OR
       (kind='challenge_issued' AND before_state IS NULL AND after_state='pending') OR
       (kind='challenge_revoked' AND before_state='pending' AND after_state='revoked') OR
       (kind='challenge_expired' AND before_state='pending' AND after_state='expired') OR
       (kind IN('email_verified','email_changed','password_reset') AND before_state='pending' AND after_state='consumed'))
);
CREATE TABLE identity_commands(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),
 request_key TEXT NOT NULL CHECK(length(request_key) BETWEEN 8 AND 128),
 operation TEXT NOT NULL CHECK(operation IN ('profile','verify_email.issue','change_email.issue','reset_password.issue','verify_email.consume','change_email.consume','reset_password.consume')),
 key_ref TEXT,request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 event_id TEXT NOT NULL REFERENCES identity_events(id),
 result TEXT NOT NULL CHECK(json_valid(result) AND json_type(result)='object'),
 created_at INTEGER NOT NULL,UNIQUE(user_id,request_key),
 CHECK((operation='profile')=(key_ref IS NULL)),
 CHECK(json_extract(result,'$.id') IS id AND json_extract(result,'$.eventId') IS event_id AND json_extract(result,'$.kind') IS operation)
);
CREATE TABLE identity_notices(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),
 event_id TEXT NOT NULL REFERENCES identity_events(id),
 target_role TEXT NOT NULL CHECK(target_role IN ('current','previous')),
 recipient_address TEXT NOT NULL,recipient_hash TEXT NOT NULL CHECK(length(recipient_hash)=64),
 created_at INTEGER NOT NULL,UNIQUE(event_id,target_role)
);
CREATE TABLE identity_mail_links(
 job_id TEXT PRIMARY KEY REFERENCES mail_jobs(id),
 user_id TEXT NOT NULL REFERENCES users(id),
 challenge_id TEXT REFERENCES identity_challenges(id),
 notice_id TEXT REFERENCES identity_notices(id),
 created_at INTEGER NOT NULL,
 CHECK((challenge_id IS NULL)!=(notice_id IS NULL))
);
CREATE TRIGGER identity_challenge_parent BEFORE INSERT ON identity_challenges
 WHEN NOT EXISTS(SELECT 1 FROM users u JOIN identity_states s ON s.user_id=u.id WHERE u.id=NEW.user_id AND u.status='active' AND lower(u.email)=NEW.source_email AND s.credential_epoch=NEW.credential_epoch)
 BEGIN SELECT RAISE(ABORT,'Challenge binds its active member and credential epoch'); END;
CREATE TRIGGER identity_head_initial BEFORE INSERT ON identity_states
 WHEN NEW.version!=0 OR NEW.credential_epoch!=0 OR NEW.verified_email_hash IS NOT NULL OR NEW.last_event_id IS NOT NULL
 BEGIN SELECT RAISE(ABORT,'Identity starts unverified without backfill'); END;
CREATE TRIGGER identity_event_parent BEFORE INSERT ON identity_events
 WHEN NOT EXISTS(SELECT 1 FROM identity_states s WHERE s.user_id=NEW.user_id AND NEW.version=s.version+1 AND NEW.previous_event_id IS s.last_event_id
 AND ((NEW.kind IN('password_reset','email_changed') AND NEW.credential_epoch=s.credential_epoch+1) OR (NEW.kind NOT IN('password_reset','email_changed') AND NEW.credential_epoch=s.credential_epoch))
 AND ((NEW.kind IN('email_verified','email_changed') AND NEW.verified_email_hash=NEW.after_email_hash AND NEW.verified_at=NEW.created_at) OR (NEW.kind NOT IN('email_verified','email_changed') AND NEW.verified_email_hash IS s.verified_email_hash AND NEW.verified_at IS s.verified_at)))
 OR (NEW.challenge_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM identity_challenges q WHERE q.id=NEW.challenge_id AND q.user_id=NEW.user_id))
 OR (NEW.challenge_version=1 AND (NEW.previous_challenge_event_id IS NOT NULL OR NEW.before_state IS NOT NULL OR NEW.after_state!='pending' OR NEW.kind!='challenge_issued'))
 OR (NEW.challenge_version>1 AND NOT EXISTS(SELECT 1 FROM identity_challenge_states s WHERE s.challenge_id=NEW.challenge_id AND NEW.challenge_version=s.version+1 AND NEW.previous_challenge_event_id=s.last_event_id AND NEW.before_state=s.state AND s.state='pending' AND NEW.after_state IN('consumed','revoked','expired')))
 BEGIN SELECT RAISE(ABORT,'Identity event binds its member and previous heads'); END;
CREATE TRIGGER identity_head_next BEFORE UPDATE ON identity_states
 WHEN NEW.user_id IS NOT OLD.user_id OR NEW.version!=OLD.version+1 OR NEW.updated_at<OLD.updated_at
 OR NOT EXISTS(SELECT 1 FROM identity_events e WHERE e.id=NEW.last_event_id AND e.user_id=NEW.user_id AND e.version=NEW.version AND e.previous_event_id IS OLD.last_event_id AND e.credential_epoch=NEW.credential_epoch AND e.verified_email_hash IS NEW.verified_email_hash AND e.verified_at IS NEW.verified_at AND e.created_at=NEW.updated_at)
 OR (NEW.credential_epoch!=OLD.credential_epoch AND (NEW.credential_epoch!=OLD.credential_epoch+1 OR (SELECT kind FROM identity_events WHERE id=NEW.last_event_id) NOT IN('password_reset','email_changed')))
 OR (NEW.verified_email_hash IS NOT OLD.verified_email_hash AND (SELECT kind FROM identity_events WHERE id=NEW.last_event_id) NOT IN('email_verified','email_changed'))
 BEGIN SELECT RAISE(ABORT,'Identity head requires its exact next event'); END;
CREATE TRIGGER identity_challenge_initial BEFORE INSERT ON identity_challenge_states
 WHEN NEW.version!=1 OR NEW.state!='pending'
 OR NOT EXISTS(SELECT 1 FROM identity_events e WHERE e.id=NEW.last_event_id AND e.challenge_id=NEW.challenge_id AND e.challenge_version=1 AND e.kind='challenge_issued' AND e.after_state='pending' AND e.created_at=NEW.updated_at)
 BEGIN SELECT RAISE(ABORT,'Challenge starts as a pending issue'); END;
CREATE TRIGGER identity_challenge_next BEFORE UPDATE ON identity_challenge_states
 WHEN NEW.challenge_id IS NOT OLD.challenge_id OR OLD.state!='pending' OR NEW.state NOT IN('consumed','revoked','expired') OR NEW.version!=OLD.version+1 OR NEW.updated_at<OLD.updated_at
 OR NOT EXISTS(SELECT 1 FROM identity_events e WHERE e.id=NEW.last_event_id AND e.challenge_id=NEW.challenge_id AND e.challenge_version=NEW.version AND e.previous_challenge_event_id=OLD.last_event_id AND e.before_state=OLD.state AND e.after_state=NEW.state AND e.created_at=NEW.updated_at)
 BEGIN SELECT RAISE(ABORT,'Challenge can leave pending only once'); END;
CREATE TRIGGER identity_command_parent BEFORE INSERT ON identity_commands
 WHEN NOT EXISTS(SELECT 1 FROM identity_events e WHERE e.id=NEW.event_id AND e.user_id=NEW.user_id AND e.command_id=NEW.id)
 BEGIN SELECT RAISE(ABORT,'Identity receipt binds its original event'); END;
CREATE TRIGGER identity_notice_parent BEFORE INSERT ON identity_notices
 WHEN NOT EXISTS(SELECT 1 FROM identity_events e WHERE e.id=NEW.event_id AND e.user_id=NEW.user_id AND e.kind IN('email_verified','email_changed','password_reset') AND ((NEW.target_role='previous' AND e.kind='email_changed' AND NEW.recipient_hash=e.before_email_hash) OR (NEW.target_role='current' AND NEW.recipient_hash=e.after_email_hash)))
 BEGIN SELECT RAISE(ABORT,'Identity notice binds its security event and channel'); END;
CREATE TRIGGER identity_mail_link_parent BEFORE INSERT ON identity_mail_links
 WHEN NOT EXISTS(SELECT 1 FROM mail_jobs j WHERE j.id=NEW.job_id AND j.user_id=NEW.user_id AND
 ((NEW.challenge_id IS NOT NULL AND json_extract(j.source_ref,'$.kind')='identity_challenge' AND json_extract(j.source_ref,'$.id')=NEW.challenge_id AND EXISTS(SELECT 1 FROM identity_challenges q WHERE q.id=NEW.challenge_id AND q.user_id=NEW.user_id AND q.purpose=j.template_id))
 OR (NEW.notice_id IS NOT NULL AND json_extract(j.source_ref,'$.kind')='identity_notice' AND json_extract(j.source_ref,'$.id')=NEW.notice_id AND j.template_id='account_notice' AND EXISTS(SELECT 1 FROM identity_notices n WHERE n.id=NEW.notice_id AND n.user_id=NEW.user_id))))
 BEGIN SELECT RAISE(ABORT,'Identity mail link binds its original source'); END;
DROP TRIGGER mail_job_parent;
CREATE TRIGGER mail_job_parent BEFORE INSERT ON mail_jobs
 WHEN NOT EXISTS(SELECT 1 FROM users u WHERE u.id=NEW.user_id AND u.status='active' AND u.name=NEW.display_name AND (
 lower(u.email)=NEW.recipient_address
 OR EXISTS(SELECT 1 FROM identity_challenges q JOIN identity_challenge_states s ON s.challenge_id=q.id WHERE json_extract(NEW.source_ref,'$.kind')='identity_challenge' AND json_extract(NEW.source_ref,'$.id')=q.id AND json_extract(NEW.source_ref,'$.version')=s.version AND q.user_id=u.id AND q.purpose='change_email' AND NEW.template_id=q.purpose AND s.state='pending' AND q.source_email=lower(u.email) AND q.candidate_email=NEW.recipient_address AND q.expires_at>NEW.created_at)
 OR EXISTS(SELECT 1 FROM identity_notices n JOIN identity_events e ON e.id=n.event_id WHERE json_extract(NEW.source_ref,'$.kind')='identity_notice' AND json_extract(NEW.source_ref,'$.id')=n.id AND json_extract(NEW.source_ref,'$.version')=1 AND n.user_id=u.id AND e.user_id=u.id AND n.recipient_address=NEW.recipient_address AND NEW.template_id='account_notice')
 )) OR (SELECT count(*) FROM json_each(NEW.source_ref))!=3
 BEGIN SELECT RAISE(ABORT,'Mail job binds its current member or explicit trusted identity target'); END;
"""

for _table in sorted(IDENTITY_TABLES):
    SCHEMA_V32 += "\nCREATE TRIGGER "+_table+"_no_delete BEFORE DELETE ON "+_table+" BEGIN SELECT RAISE(ABORT,'Identity facts are retained'); END;"
    if _table not in ('identity_states', 'identity_challenge_states'):
        SCHEMA_V32 += "\nCREATE TRIGGER "+_table+"_no_update BEFORE UPDATE ON "+_table+" BEGIN SELECT RAISE(ABORT,'Identity facts are immutable'); END;"

SCHEMA_V32 += "\nINSERT INTO schema_migrations VALUES(32,strftime('%Y-%m-%dT%H:%M:%SZ','now'));\nPRAGMA user_version=32;\n"
