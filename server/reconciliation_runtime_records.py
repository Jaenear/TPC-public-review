"""v26: authenticated ingress, automatic query provenance and safe outcomes.

The two cyclic queue tables are rebuilt only to add a genuine `scheduled`
source. Existing rowids, IDs, columns, facts and fences are copied verbatim.
No machine/member account or fictional provider event is created for a timer.
"""

RECONCILIATION_RUNTIME_TABLES = {
    "reconciliation_scope_observations", "provider_event_auth",
    "reconciliation_commands", "reconciliation_outcomes",
    "reconciliation_schedules",
}

REBUILT_QUEUE_OBJECTS = {
    "reconciliation_one_running_order", "reconciliation_ready",
    "reconciliation_job_transition", "reconciliation_attempt_transition",
    "reconciliation_attempt_parent", "reconciliation_job_owner",
    "reconciliation_job_no_delete", "reconciliation_attempt_no_delete",
}

SCHEMA_V26 = r"""
PRAGMA defer_foreign_keys=ON;
PRAGMA legacy_alter_table=OFF;
CREATE TABLE reconciliation_jobs_v26(
 id TEXT PRIMARY KEY,order_id TEXT NOT NULL,provider TEXT NOT NULL,mode TEXT NOT NULL,merchant_hash TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('event','operator','scheduled')),event_id TEXT,
 requested_by TEXT REFERENCES users(id),request_key_hash TEXT CHECK(request_key_hash IS NULL OR length(request_key_hash)=64),request_order_version INTEGER,
 state TEXT NOT NULL CHECK(state IN ('queued','running','matched','retry','review')),
 version INTEGER NOT NULL CHECK(version>=1),attempt_count INTEGER NOT NULL CHECK(attempt_count>=0),
 policy_version INTEGER NOT NULL CHECK(policy_version=1),max_attempts INTEGER NOT NULL CHECK(max_attempts BETWEEN 1 AND 10),
 base_delay INTEGER NOT NULL CHECK(base_delay BETWEEN 1 AND 3600),max_delay INTEGER NOT NULL CHECK(max_delay BETWEEN base_delay AND 86400),
 lease_seconds INTEGER NOT NULL CHECK(lease_seconds BETWEEN 5 AND 300),
 available_at INTEGER NOT NULL CHECK(available_at>=0),lease_until INTEGER,current_attempt_id TEXT,
 created_at INTEGER NOT NULL CHECK(created_at>=0),updated_at INTEGER NOT NULL CHECK(updated_at>=created_at),
 UNIQUE(id,order_id),UNIQUE(event_id),UNIQUE(requested_by,request_key_hash),
 FOREIGN KEY(order_id,provider,mode,merchant_hash) REFERENCES reconciliation_order_scopes(order_id,provider,mode,merchant_hash),
 FOREIGN KEY(event_id,order_id) REFERENCES provider_event_links(event_id,order_id),
 FOREIGN KEY(current_attempt_id,id) REFERENCES reconciliation_attempts_v26(id,job_id) DEFERRABLE INITIALLY DEFERRED,
 CHECK((source='event' AND event_id IS NOT NULL AND requested_by IS NULL AND request_key_hash IS NULL AND request_order_version IS NULL) OR
       (source='operator' AND event_id IS NULL AND requested_by IS NOT NULL AND request_key_hash IS NOT NULL AND request_order_version IS NOT NULL AND request_order_version>=1) OR
       (source='scheduled' AND event_id IS NULL AND requested_by IS NULL AND request_key_hash IS NULL AND request_order_version IS NULL)),
 CHECK((state='running' AND lease_until IS NOT NULL AND current_attempt_id IS NOT NULL) OR (state!='running' AND lease_until IS NULL)),
 CHECK(attempt_count<=max_attempts),
 CHECK((state='queued' AND attempt_count=0 AND current_attempt_id IS NULL) OR (state!='queued' AND attempt_count>=1 AND current_attempt_id IS NOT NULL))
);
CREATE TABLE reconciliation_attempts_v26(
 id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES reconciliation_jobs_v26(id),ordinal INTEGER NOT NULL CHECK(ordinal>=1),
 worker_hash TEXT NOT NULL CHECK(length(worker_hash)=64),token_hash TEXT NOT NULL UNIQUE CHECK(length(token_hash)=64),
 order_facts TEXT NOT NULL CHECK(json_valid(order_facts)),started_at INTEGER NOT NULL CHECK(started_at>=0),
 lease_until INTEGER NOT NULL CHECK(lease_until>started_at),
 state TEXT NOT NULL CHECK(state IN ('running','expired','matched','retry','review')),
 result_code TEXT CHECK(result_code IN ('pg_match','lookup_timeout','lookup_error','scope_mismatch','reference_mismatch',
 'amount_mismatch','currency_mismatch','payment_key_mismatch','order_changed','unsupported_status','lease_expired')),
 observed_status TEXT CHECK(observed_status IN ('READY','IN_PROGRESS','WAITING_FOR_DEPOSIT','DONE','CANCELED','PARTIAL_CANCELED','ABORTED','EXPIRED')),
 observed_amount INTEGER CHECK(observed_amount IS NULL OR observed_amount>=0),
 observed_currency TEXT CHECK(observed_currency IS NULL OR observed_currency='KRW'),
 observed_key_hash TEXT CHECK(observed_key_hash IS NULL OR length(observed_key_hash)=64),finished_at INTEGER,
 UNIQUE(job_id,ordinal),UNIQUE(id,job_id),
 CHECK((state='running' AND finished_at IS NULL AND result_code IS NULL) OR
       (state!='running' AND finished_at IS NOT NULL AND finished_at>=started_at AND result_code IS NOT NULL)),
 CHECK((state='matched' AND result_code='pg_match') OR (state!='matched' AND result_code IS NOT 'pg_match')),
 CHECK((state='expired' AND result_code='lease_expired') OR (state!='expired' AND result_code IS NOT 'lease_expired'))
);
INSERT INTO reconciliation_jobs_v26(rowid,id,order_id,provider,mode,merchant_hash,source,event_id,requested_by,request_key_hash,request_order_version,state,version,attempt_count,policy_version,max_attempts,base_delay,max_delay,lease_seconds,available_at,lease_until,current_attempt_id,created_at,updated_at) SELECT rowid,* FROM reconciliation_jobs;
INSERT INTO reconciliation_attempts_v26(rowid,id,job_id,ordinal,worker_hash,token_hash,order_facts,started_at,lease_until,state,result_code,observed_status,observed_amount,observed_currency,observed_key_hash,finished_at) SELECT rowid,* FROM reconciliation_attempts;
DROP TABLE reconciliation_attempts;
DROP TABLE reconciliation_jobs;
ALTER TABLE reconciliation_jobs_v26 RENAME TO reconciliation_jobs;
ALTER TABLE reconciliation_attempts_v26 RENAME TO reconciliation_attempts;
CREATE UNIQUE INDEX reconciliation_one_running_order ON reconciliation_jobs(order_id) WHERE state='running';
CREATE INDEX reconciliation_ready ON reconciliation_jobs(provider,mode,merchant_hash,state,available_at,created_at);
CREATE INDEX reconciliation_scope_reference ON reconciliation_order_scopes(provider,mode,merchant_hash,order_reference_hash);
CREATE INDEX provider_event_unlinked ON provider_events(provider,mode,merchant_hash,order_reference_hash);
CREATE TRIGGER reconciliation_job_transition BEFORE UPDATE ON reconciliation_jobs WHEN
 NEW.id IS NOT OLD.id OR NEW.order_id IS NOT OLD.order_id OR NEW.provider IS NOT OLD.provider OR NEW.mode IS NOT OLD.mode OR
 NEW.merchant_hash IS NOT OLD.merchant_hash OR NEW.source IS NOT OLD.source OR NEW.event_id IS NOT OLD.event_id OR
 NEW.requested_by IS NOT OLD.requested_by OR NEW.request_key_hash IS NOT OLD.request_key_hash OR NEW.request_order_version IS NOT OLD.request_order_version OR NEW.created_at IS NOT OLD.created_at OR
 NEW.policy_version IS NOT OLD.policy_version OR NEW.max_attempts IS NOT OLD.max_attempts OR NEW.base_delay IS NOT OLD.base_delay OR
 NEW.max_delay IS NOT OLD.max_delay OR NEW.lease_seconds IS NOT OLD.lease_seconds OR NEW.version!=OLD.version+1 OR
 NOT((OLD.state IN ('queued','retry') AND NEW.state='running' AND NEW.attempt_count=OLD.attempt_count+1) OR
     (OLD.state='running' AND NEW.state IN ('matched','retry','review') AND NEW.attempt_count=OLD.attempt_count AND NEW.current_attempt_id=OLD.current_attempt_id))
 BEGIN SELECT RAISE(ABORT,'Invalid reconciliation job transition'); END;
CREATE TRIGGER reconciliation_attempt_transition BEFORE UPDATE ON reconciliation_attempts WHEN
 NEW.id IS NOT OLD.id OR NEW.job_id IS NOT OLD.job_id OR NEW.ordinal IS NOT OLD.ordinal OR NEW.worker_hash IS NOT OLD.worker_hash OR
 NEW.token_hash IS NOT OLD.token_hash OR NEW.order_facts IS NOT OLD.order_facts OR NEW.started_at IS NOT OLD.started_at OR NEW.lease_until IS NOT OLD.lease_until OR
 OLD.state!='running' OR NEW.state NOT IN ('expired','matched','retry','review')
 BEGIN SELECT RAISE(ABORT,'An attempt can finish only once'); END;
CREATE TRIGGER reconciliation_attempt_parent BEFORE INSERT ON reconciliation_attempts WHEN NOT EXISTS(
 SELECT 1 FROM reconciliation_jobs WHERE id=NEW.job_id AND state IN ('queued','retry') AND attempt_count+1=NEW.ordinal AND NEW.ordinal<=max_attempts
) BEGIN SELECT RAISE(ABORT,'Attempt must claim an eligible job'); END;
CREATE TRIGGER reconciliation_job_owner BEFORE UPDATE ON reconciliation_jobs WHEN NEW.state='running' AND NOT EXISTS(
 SELECT 1 FROM reconciliation_attempts a WHERE a.id=NEW.current_attempt_id AND a.job_id=NEW.id AND a.ordinal=NEW.attempt_count AND a.state='running' AND a.lease_until=NEW.lease_until
) BEGIN SELECT RAISE(ABORT,'Job must have its current running owner'); END;
CREATE TRIGGER reconciliation_job_no_delete BEFORE DELETE ON reconciliation_jobs BEGIN SELECT RAISE(ABORT,'Reconciliation job is retained'); END;
CREATE TRIGGER reconciliation_attempt_no_delete BEFORE DELETE ON reconciliation_attempts BEGIN SELECT RAISE(ABORT,'Reconciliation attempt is retained'); END;
CREATE TABLE reconciliation_scope_observations(
 id TEXT PRIMARY KEY,order_id TEXT NOT NULL,provider TEXT NOT NULL,mode TEXT NOT NULL,merchant_hash TEXT NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('checkout','approval','operator')),actor_id TEXT NOT NULL REFERENCES users(id),
 order_version INTEGER NOT NULL CHECK(order_version>=1),observed_at INTEGER NOT NULL CHECK(observed_at>=0),
 FOREIGN KEY(order_id,provider,mode,merchant_hash) REFERENCES reconciliation_order_scopes(order_id,provider,mode,merchant_hash)
);
CREATE TABLE provider_event_auth(
 event_id TEXT PRIMARY KEY REFERENCES provider_events(id),algorithm TEXT NOT NULL CHECK(algorithm='hmac_sha256_v1'),
 raw_body_hash TEXT NOT NULL CHECK(length(raw_body_hash)=64),transmission_time_hash TEXT NOT NULL CHECK(length(transmission_time_hash)=64),
 payment_key_hash TEXT CHECK(payment_key_hash IS NULL OR length(payment_key_hash)=64),
 secret_hash TEXT CHECK(secret_hash IS NULL OR length(secret_hash)=64),transaction_hash TEXT CHECK(transaction_hash IS NULL OR length(transaction_hash)=64),
 hint_status TEXT CHECK(hint_status IN ('READY','IN_PROGRESS','WAITING_FOR_DEPOSIT','DONE','CANCELED','PARTIAL_CANCELED','ABORTED','EXPIRED','UNKNOWN')),
 disposition TEXT NOT NULL CHECK(disposition IN ('eligible','scope_mismatch','unresolved_reference')),
 verified_at INTEGER NOT NULL CHECK(verified_at>=0)
);
CREATE TABLE reconciliation_commands(
 actor_id TEXT NOT NULL REFERENCES users(id),request_key_hash TEXT NOT NULL CHECK(length(request_key_hash)=64),
 request_hash TEXT NOT NULL CHECK(length(request_hash)=64),order_id TEXT NOT NULL REFERENCES purchase_orders(id),
 prior_job_id TEXT,expected_job_version INTEGER,expected_order_version INTEGER NOT NULL CHECK(expected_order_version>=1),
 job_id TEXT NOT NULL UNIQUE,reason TEXT NOT NULL CHECK(length(reason) BETWEEN 10 AND 1000),
 scope_acknowledged INTEGER NOT NULL CHECK(scope_acknowledged IN (0,1)),created_at INTEGER NOT NULL CHECK(created_at>=0),
 PRIMARY KEY(actor_id,request_key_hash),
 FOREIGN KEY(job_id,order_id) REFERENCES reconciliation_jobs(id,order_id),
 FOREIGN KEY(prior_job_id,order_id) REFERENCES reconciliation_jobs(id,order_id),
 CHECK((prior_job_id IS NULL AND expected_job_version IS NULL) OR (prior_job_id IS NOT NULL AND expected_job_version IS NOT NULL AND expected_job_version>=1))
);
CREATE TABLE reconciliation_outcomes(
 attempt_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,
 detail_code TEXT NOT NULL CHECK(detail_code IN (
 'payment_verified','payment_current','access_reconciled','cancellation_verified','cancellation_current','external_cancellation_verified',
 'unpaid_terminal','unpaid_closed','provider_pending','refund_pending','partial_cancellation','provider_state_conflict','missing_purchase_evidence',
 'lookup_timeout','lookup_failed','lookup_unavailable','configuration_changed','identity_mismatch','notice_secret_mismatch',
 'notice_key_mismatch','notice_auth_missing','order_changed','apply_failed')),
 observed_at INTEGER NOT NULL CHECK(observed_at>=0),
 FOREIGN KEY(attempt_id,job_id) REFERENCES reconciliation_attempts(id,job_id)
);
CREATE TABLE reconciliation_schedules(
 job_id TEXT PRIMARY KEY,order_id TEXT NOT NULL REFERENCES purchase_orders(id),
 phase TEXT NOT NULL CHECK(phase IN ('authorization','refund')),reference_id TEXT NOT NULL CHECK(length(reference_id) BETWEEN 1 AND 120),
 operation_id TEXT,initiated_by TEXT NOT NULL REFERENCES users(id),order_version INTEGER NOT NULL CHECK(order_version>=1),
 created_at INTEGER NOT NULL CHECK(created_at>=0),UNIQUE(order_id,phase,reference_id),
 FOREIGN KEY(job_id,order_id) REFERENCES reconciliation_jobs(id,order_id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(operation_id,order_id) REFERENCES refund_operations(id,order_id),
 CHECK((phase='authorization' AND operation_id IS NULL) OR (phase='refund' AND operation_id IS NOT NULL))
);
CREATE TRIGGER reconciliation_schedule_authority BEFORE INSERT ON reconciliation_schedules WHEN
 NOT EXISTS(SELECT 1 FROM purchase_orders WHERE id=NEW.order_id AND version=NEW.order_version) OR
 (NEW.phase='authorization' AND NOT EXISTS(SELECT 1 FROM audit_logs WHERE id=NEW.reference_id AND actor_id=NEW.initiated_by AND target_type='purchase' AND target_id=NEW.order_id AND action IN ('checkout.authorization_started','payment.approval_started'))) OR
 (NEW.phase='refund' AND NOT EXISTS(SELECT 1 FROM refund_commands WHERE actor_id=NEW.initiated_by AND order_id=NEW.order_id AND json_extract(receipt,'$.id')=NEW.reference_id AND json_extract(receipt,'$.operationId')=NEW.operation_id))
 BEGIN SELECT RAISE(ABORT,'Scheduled work must have an actual authorization or refund command'); END;
CREATE TRIGGER reconciliation_scheduled_job_parent BEFORE INSERT ON reconciliation_jobs WHEN NEW.source='scheduled' AND NOT EXISTS(
 SELECT 1 FROM reconciliation_schedules WHERE job_id=NEW.id AND order_id=NEW.order_id
) BEGIN SELECT RAISE(ABORT,'Scheduled work requires real trigger provenance'); END;
CREATE TRIGGER reconciliation_schedule_no_update BEFORE UPDATE ON reconciliation_schedules BEGIN SELECT RAISE(ABORT,'Query schedule is immutable'); END;
CREATE TRIGGER reconciliation_schedule_no_delete BEFORE DELETE ON reconciliation_schedules BEGIN SELECT RAISE(ABORT,'Query schedule is retained'); END;
CREATE TRIGGER reconciliation_scope_observation_no_update BEFORE UPDATE ON reconciliation_scope_observations BEGIN SELECT RAISE(ABORT,'Scope observation is immutable'); END;
CREATE TRIGGER reconciliation_scope_observation_no_delete BEFORE DELETE ON reconciliation_scope_observations BEGIN SELECT RAISE(ABORT,'Scope observation is retained'); END;
CREATE TRIGGER provider_event_auth_no_update BEFORE UPDATE ON provider_event_auth BEGIN SELECT RAISE(ABORT,'Ingress authentication is immutable'); END;
CREATE TRIGGER provider_event_auth_no_delete BEFORE DELETE ON provider_event_auth BEGIN SELECT RAISE(ABORT,'Ingress authentication is retained'); END;
CREATE TRIGGER reconciliation_command_no_update BEFORE UPDATE ON reconciliation_commands BEGIN SELECT RAISE(ABORT,'Query command is immutable'); END;
CREATE TRIGGER reconciliation_command_no_delete BEFORE DELETE ON reconciliation_commands BEGIN SELECT RAISE(ABORT,'Query command is retained'); END;
CREATE TRIGGER reconciliation_outcome_no_update BEFORE UPDATE ON reconciliation_outcomes BEGIN SELECT RAISE(ABORT,'Query outcome is immutable'); END;
CREATE TRIGGER reconciliation_outcome_no_delete BEFORE DELETE ON reconciliation_outcomes BEGIN SELECT RAISE(ABORT,'Query outcome is retained'); END;
INSERT INTO schema_migrations VALUES(26,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=26;
"""
