"""Additive v31 mail foundation. No backfill, transport settings or secret URLs."""

MAIL_TABLES = {
    'mail_jobs', 'mail_job_states', 'delivery_attempts',
    'delivery_attempt_results', 'mail_delivery_facts', 'mail_events',
    'suppression_list', 'suppression_events',
}

SCHEMA_V31 = """
CREATE TABLE mail_jobs(
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL REFERENCES users(id),
 request_key TEXT NOT NULL CHECK(length(request_key) BETWEEN 8 AND 128),
 request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 template_id TEXT NOT NULL CHECK(template_id IN ('account_notice','verify_email','change_email','reset_password')),
 template_version INTEGER NOT NULL CHECK(template_version>=1),
 template_hash TEXT NOT NULL CHECK(length(template_hash)=64),
 recipient_address TEXT NOT NULL CHECK(length(recipient_address) BETWEEN 3 AND 254),
 recipient_hash TEXT NOT NULL CHECK(length(recipient_hash)=64),
 display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 2 AND 50),
 source_ref TEXT NOT NULL CHECK(json_valid(source_ref) AND json_type(source_ref)='object' AND json_type(source_ref,'$.kind')='text' AND json_type(source_ref,'$.id')='text' AND json_type(source_ref,'$.version')='integer' AND json_extract(source_ref,'$.version')>=1),
 source_hash TEXT NOT NULL CHECK(length(source_hash)=64),
 expires_at INTEGER NOT NULL,
 created_at INTEGER NOT NULL CHECK(expires_at>created_at),
 UNIQUE(user_id,request_key)
);
CREATE INDEX mail_job_recipient ON mail_jobs(recipient_hash,created_at);
CREATE TABLE mail_job_states(
 job_id TEXT PRIMARY KEY REFERENCES mail_jobs(id),
 version INTEGER NOT NULL CHECK(version>=1),
 state TEXT NOT NULL CHECK(state IN ('queued','leased','sending','submitted','delivered','retry_wait','failed','rejected','unknown','suppressed','cancelled','expired')),
 current_attempt_id TEXT REFERENCES delivery_attempts(id) DEFERRABLE INITIALLY DEFERRED,
 last_event_id TEXT NOT NULL REFERENCES mail_events(id) DEFERRABLE INITIALLY DEFERRED,
 ready_at INTEGER NOT NULL,
 lease_hash TEXT CHECK(lease_hash IS NULL OR length(lease_hash)=64),
 lease_until INTEGER,
 sealed_profile_hash TEXT CHECK(sealed_profile_hash IS NULL OR length(sealed_profile_hash)=64),
 sealed_content_hash TEXT CHECK(sealed_content_hash IS NULL OR length(sealed_content_hash)=64),
 last_code TEXT NOT NULL,
 updated_at INTEGER NOT NULL,
 CHECK((state IN ('leased','sending') AND lease_hash IS NOT NULL AND lease_until IS NOT NULL AND current_attempt_id IS NOT NULL) OR (state NOT IN ('leased','sending') AND lease_hash IS NULL AND lease_until IS NULL)),
 CHECK((sealed_profile_hash IS NULL)=(sealed_content_hash IS NULL)),
 CHECK(state NOT IN ('sending','submitted','delivered','unknown') OR sealed_profile_hash IS NOT NULL)
);
CREATE INDEX mail_ready ON mail_job_states(state,ready_at);
CREATE TABLE delivery_attempts(
 id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL REFERENCES mail_jobs(id),
 ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 10),
 worker_id TEXT NOT NULL CHECK(length(worker_id) BETWEEN 1 AND 100),
 lease_hash TEXT NOT NULL CHECK(length(lease_hash)=64),
 lease_until INTEGER NOT NULL,
 delivery_key TEXT NOT NULL,
 profile_hash TEXT NOT NULL CHECK(length(profile_hash)=64),
 transport_mode TEXT NOT NULL CHECK(transport_mode IN ('synthetic','external')),
 content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
 created_at INTEGER NOT NULL CHECK(lease_until>created_at),
 UNIQUE(job_id,ordinal), UNIQUE(id,job_id)
);
CREATE TABLE delivery_attempt_results(
 attempt_id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('accepted','not_submitted','rejected','unknown','not_sent')),
 code TEXT NOT NULL,
 provider_reference TEXT CHECK(provider_reference IS NULL OR length(provider_reference) BETWEEN 1 AND 128),
 result_hash TEXT NOT NULL CHECK(length(result_hash)=64),
 result_version INTEGER NOT NULL CHECK(result_version>=2),
 created_at INTEGER NOT NULL,
 FOREIGN KEY(attempt_id,job_id) REFERENCES delivery_attempts(id,job_id),
 CHECK(outcome!='accepted' OR provider_reference IS NOT NULL)
);
CREATE TABLE mail_delivery_facts(
 id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL REFERENCES mail_jobs(id),
 attempt_id TEXT NOT NULL,
 profile_hash TEXT NOT NULL CHECK(length(profile_hash)=64),
 event_key TEXT NOT NULL CHECK(length(event_key) BETWEEN 8 AND 128),
 delivery_key TEXT NOT NULL,
 provider_reference TEXT NOT NULL CHECK(length(provider_reference) BETWEEN 1 AND 128),
 outcome TEXT NOT NULL CHECK(outcome IN ('delivered','bounced')),
 evidence_hash TEXT NOT NULL CHECK(length(evidence_hash)=64),
 result_version INTEGER NOT NULL,
 created_at INTEGER NOT NULL,
 UNIQUE(profile_hash,event_key),
 FOREIGN KEY(attempt_id,job_id) REFERENCES delivery_attempts(id,job_id)
);
CREATE TABLE mail_events(
 id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL REFERENCES mail_jobs(id),
 version INTEGER NOT NULL CHECK(version>=1),
 previous_event_id TEXT REFERENCES mail_events(id),
 before_state TEXT,
 after_state TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('queued','claimed','submission_started','accepted','not_submitted','rejected','unknown','lease_recovered','suppressed','cancelled','expired','delivery_confirmed','bounce_recorded')),
 code TEXT NOT NULL,
 attempt_id TEXT,
 created_at INTEGER NOT NULL,
 UNIQUE(job_id,version),
 FOREIGN KEY(attempt_id,job_id) REFERENCES delivery_attempts(id,job_id)
);
CREATE TABLE suppression_list(
 recipient_hash TEXT PRIMARY KEY CHECK(length(recipient_hash)=64),
 version INTEGER NOT NULL CHECK(version>=1),
 blocked INTEGER NOT NULL CHECK(blocked IN (0,1)),
 current_event_id TEXT NOT NULL REFERENCES suppression_events(id) DEFERRABLE INITIALLY DEFERRED,
 updated_at INTEGER NOT NULL
);
CREATE TABLE suppression_events(
 id TEXT PRIMARY KEY,
 recipient_hash TEXT NOT NULL REFERENCES suppression_list(recipient_hash),
 version INTEGER NOT NULL CHECK(version>=1),
 previous_event_id TEXT REFERENCES suppression_events(id),
 actor_id TEXT NOT NULL REFERENCES users(id),
 target_user_id TEXT NOT NULL REFERENCES users(id),
 request_key TEXT NOT NULL CHECK(length(request_key) BETWEEN 8 AND 128),
 request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 blocked INTEGER NOT NULL CHECK(blocked IN (0,1)),
 code TEXT NOT NULL CHECK(code IN ('hard_bounce','complaint','operator_hold','operator_release')),
 created_at INTEGER NOT NULL,
 UNIQUE(recipient_hash,version),UNIQUE(actor_id,request_key)
);
CREATE TRIGGER mail_job_parent BEFORE INSERT ON mail_jobs WHEN NOT EXISTS(SELECT 1 FROM users u WHERE u.id=NEW.user_id AND u.status='active' AND lower(u.email)=NEW.recipient_address AND u.name=NEW.display_name) OR (SELECT count(*) FROM json_each(NEW.source_ref))!=3 BEGIN SELECT RAISE(ABORT,'Mail job must bind its current member and source'); END;
CREATE TRIGGER mail_state_initial BEFORE INSERT ON mail_job_states WHEN NEW.version!=1 OR NEW.state!='queued' OR NEW.current_attempt_id IS NOT NULL OR NEW.lease_hash IS NOT NULL OR NEW.sealed_profile_hash IS NOT NULL BEGIN SELECT RAISE(ABORT,'Mail starts as a queued intent'); END;
CREATE TRIGGER mail_state_next BEFORE UPDATE ON mail_job_states WHEN NEW.job_id IS NOT OLD.job_id OR NEW.version!=OLD.version+1 OR NEW.last_event_id IS OLD.last_event_id OR NEW.updated_at<OLD.updated_at OR (OLD.sealed_profile_hash IS NOT NULL AND (NEW.sealed_profile_hash IS NOT OLD.sealed_profile_hash OR NEW.sealed_content_hash IS NOT OLD.sealed_content_hash)) OR (OLD.sealed_profile_hash IS NULL AND NEW.sealed_profile_hash IS NOT NULL AND NEW.state!='sending') OR NOT(
 (OLD.state IN ('queued','retry_wait') AND NEW.state IN ('leased','cancelled','expired','suppressed')) OR
 (OLD.state='leased' AND NEW.state IN ('sending','queued','cancelled','expired','suppressed')) OR
 (OLD.state='sending' AND NEW.state IN ('submitted','retry_wait','failed','rejected','unknown','expired')) OR
 (OLD.state IN ('submitted','unknown') AND NEW.state IN ('delivered','failed'))
) BEGIN SELECT RAISE(ABORT,'Mail state requires its next explicit transition'); END;
CREATE TRIGGER mail_state_no_delete BEFORE DELETE ON mail_job_states BEGIN SELECT RAISE(ABORT,'Mail state is retained'); END;
CREATE TRIGGER mail_attempt_parent BEFORE INSERT ON delivery_attempts WHEN NOT EXISTS(SELECT 1 FROM mail_job_states s WHERE s.job_id=NEW.job_id AND s.state IN ('queued','retry_wait')) OR NEW.ordinal!=(SELECT count(*)+1 FROM delivery_attempts WHERE job_id=NEW.job_id) BEGIN SELECT RAISE(ABORT,'Mail claim must bind its queued job and next attempt'); END;
CREATE TRIGGER mail_result_parent BEFORE INSERT ON delivery_attempt_results WHEN NOT EXISTS(SELECT 1 FROM mail_job_states s JOIN delivery_attempts a ON a.id=s.current_attempt_id WHERE s.job_id=NEW.job_id AND a.id=NEW.attempt_id AND s.version=NEW.result_version AND ((NEW.outcome='accepted' AND s.state='submitted') OR (NEW.outcome='not_submitted' AND s.state IN ('retry_wait','failed','expired')) OR (NEW.outcome='rejected' AND s.state='rejected') OR (NEW.outcome='unknown' AND s.state='unknown') OR (NEW.outcome='not_sent' AND s.state IN ('queued','cancelled','expired','suppressed')))) BEGIN SELECT RAISE(ABORT,'Mail result must bind its current attempt and outcome'); END;
CREATE TRIGGER mail_fact_parent BEFORE INSERT ON mail_delivery_facts WHEN NOT EXISTS(SELECT 1 FROM delivery_attempts a JOIN mail_job_states s ON s.current_attempt_id=a.id WHERE a.id=NEW.attempt_id AND a.job_id=NEW.job_id AND a.profile_hash=NEW.profile_hash AND a.delivery_key=NEW.delivery_key AND s.version=NEW.result_version AND ((NEW.outcome='delivered' AND s.state='delivered') OR (NEW.outcome='bounced' AND s.state='failed'))) BEGIN SELECT RAISE(ABORT,'Delivery evidence must bind its attempt and current result'); END;
CREATE TRIGGER mail_event_parent BEFORE INSERT ON mail_events WHEN NOT EXISTS(SELECT 1 FROM mail_job_states s WHERE s.job_id=NEW.job_id AND s.last_event_id=NEW.id AND s.version=NEW.version AND s.state=NEW.after_state AND s.last_code=NEW.code AND s.updated_at=NEW.created_at) OR (NEW.version=1 AND (NEW.previous_event_id IS NOT NULL OR NEW.before_state IS NOT NULL OR NEW.after_state!='queued' OR NEW.kind!='queued')) OR (NEW.version>1 AND NOT EXISTS(SELECT 1 FROM mail_events p WHERE p.id=NEW.previous_event_id AND p.job_id=NEW.job_id AND p.version=NEW.version-1 AND p.after_state=NEW.before_state)) BEGIN SELECT RAISE(ABORT,'Mail event must bind the current and previous state'); END;
CREATE TRIGGER suppression_state_initial BEFORE INSERT ON suppression_list WHEN NEW.version!=1 OR NEW.blocked!=1 BEGIN SELECT RAISE(ABORT,'A new suppression must be an explicit block'); END;
CREATE TRIGGER suppression_state_next BEFORE UPDATE ON suppression_list WHEN NEW.recipient_hash IS NOT OLD.recipient_hash OR NEW.version!=OLD.version+1 OR NEW.current_event_id IS OLD.current_event_id OR NEW.updated_at<OLD.updated_at BEGIN SELECT RAISE(ABORT,'Suppression requires its next version'); END;
CREATE TRIGGER suppression_state_no_delete BEFORE DELETE ON suppression_list BEGIN SELECT RAISE(ABORT,'Suppression intent is retained'); END;
CREATE TRIGGER suppression_event_parent BEFORE INSERT ON suppression_events WHEN NOT EXISTS(SELECT 1 FROM suppression_list s WHERE s.recipient_hash=NEW.recipient_hash AND s.version=NEW.version AND s.current_event_id=NEW.id AND s.blocked=NEW.blocked AND s.updated_at=NEW.created_at) OR NOT EXISTS(SELECT 1 FROM users u WHERE u.id=NEW.actor_id AND u.role='admin' AND u.status='active') OR (NEW.version=1 AND NEW.previous_event_id IS NOT NULL) OR (NEW.version>1 AND NOT EXISTS(SELECT 1 FROM suppression_events p WHERE p.id=NEW.previous_event_id AND p.recipient_hash=NEW.recipient_hash AND p.version=NEW.version-1)) OR (NEW.blocked=0 AND NEW.code!='operator_release') OR (NEW.blocked=1 AND NEW.code='operator_release') BEGIN SELECT RAISE(ABORT,'Suppression event must bind its administrator and previous intent'); END;
"""

for _table in sorted(MAIL_TABLES - {'mail_job_states', 'suppression_list'}):
    for _verb in ('UPDATE', 'DELETE'):
        SCHEMA_V31 += 'CREATE TRIGGER '+_table+'_no_'+_verb.lower()+' BEFORE '+_verb+' ON '+_table+" BEGIN SELECT RAISE(ABORT,'Mail facts are immutable'); END;\n"

SCHEMA_V31 += "INSERT INTO schema_migrations VALUES(31,strftime('%Y-%m-%dT%H:%M:%SZ','now'));\nPRAGMA user_version=31;\n"
