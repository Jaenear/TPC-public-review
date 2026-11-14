"""v25: receipts and fenced query work, never inferred payment history.

No backfill. The original commerce and access tables are not changed.
"""

RECONCILIATION_TABLES = {
    "reconciliation_order_scopes", "provider_events", "provider_event_conflicts",
    "provider_event_links", "reconciliation_jobs", "reconciliation_attempts",
}

SCHEMA_V25 = r"""
CREATE TABLE reconciliation_order_scopes(
 order_id TEXT PRIMARY KEY REFERENCES purchase_orders(id),provider TEXT NOT NULL CHECK(provider='toss'),
 mode TEXT NOT NULL CHECK(mode IN ('test','live')),merchant_hash TEXT NOT NULL CHECK(length(merchant_hash)=64),
 order_reference_hash TEXT NOT NULL CHECK(length(order_reference_hash)=64),
 observed_order_version INTEGER NOT NULL CHECK(observed_order_version>=1),
 observed_by TEXT NOT NULL REFERENCES users(id),observed_at INTEGER NOT NULL CHECK(observed_at>=0),
 UNIQUE(order_id,provider,mode,merchant_hash),UNIQUE(order_id,provider,mode,merchant_hash,order_reference_hash)
);
CREATE TRIGGER reconciliation_scope_parent BEFORE INSERT ON reconciliation_order_scopes WHEN NOT EXISTS(
 SELECT 1 FROM purchase_orders o WHERE o.id=NEW.order_id AND o.mode=NEW.mode AND o.version=NEW.observed_order_version
) BEGIN SELECT RAISE(ABORT,'Scope must match the current order'); END;
CREATE TABLE provider_events(
 id TEXT PRIMARY KEY,provider TEXT NOT NULL CHECK(provider='toss'),mode TEXT NOT NULL CHECK(mode IN ('test','live')),
 merchant_hash TEXT NOT NULL CHECK(length(merchant_hash)=64),identity_kind TEXT NOT NULL CHECK(identity_kind IN ('provider_id','content')),
 identity_hash TEXT NOT NULL CHECK(length(identity_hash)=64),content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
 event_type TEXT NOT NULL CHECK(event_type IN ('PAYMENT_STATUS_CHANGED','DEPOSIT_CALLBACK','CANCEL_STATUS_CHANGED')),
 order_reference_hash TEXT CHECK(order_reference_hash IS NULL OR length(order_reference_hash)=64),
 received_at INTEGER NOT NULL CHECK(received_at>=0),
 UNIQUE(provider,mode,merchant_hash,identity_kind,identity_hash),
 UNIQUE(id,provider,mode,merchant_hash,order_reference_hash),
 CHECK(identity_kind!='content' OR identity_hash=content_hash)
);
CREATE TABLE provider_event_conflicts(
 id TEXT PRIMARY KEY,event_id TEXT NOT NULL REFERENCES provider_events(id),
 content_hash TEXT NOT NULL CHECK(length(content_hash)=64),observed_at INTEGER NOT NULL CHECK(observed_at>=0),
 UNIQUE(event_id,content_hash)
);
CREATE TRIGGER provider_conflict_distinct BEFORE INSERT ON provider_event_conflicts WHEN EXISTS(
 SELECT 1 FROM provider_events WHERE id=NEW.event_id AND (identity_kind!='provider_id' OR content_hash=NEW.content_hash)
) BEGIN SELECT RAISE(ABORT,'Only a conflicting provider identity is a conflict'); END;
CREATE TABLE provider_event_links(
 event_id TEXT PRIMARY KEY,order_id TEXT NOT NULL,provider TEXT NOT NULL,mode TEXT NOT NULL,merchant_hash TEXT NOT NULL,
 order_reference_hash TEXT NOT NULL,linked_by TEXT REFERENCES users(id),linked_at INTEGER NOT NULL CHECK(linked_at>=0),
 UNIQUE(event_id,order_id),
 FOREIGN KEY(order_id,provider,mode,merchant_hash,order_reference_hash) REFERENCES reconciliation_order_scopes(order_id,provider,mode,merchant_hash,order_reference_hash),
 FOREIGN KEY(event_id,provider,mode,merchant_hash,order_reference_hash) REFERENCES provider_events(id,provider,mode,merchant_hash,order_reference_hash)
);
CREATE TABLE reconciliation_jobs(
 id TEXT PRIMARY KEY,order_id TEXT NOT NULL,provider TEXT NOT NULL,mode TEXT NOT NULL,merchant_hash TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('event','operator')),event_id TEXT,
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
 FOREIGN KEY(current_attempt_id,id) REFERENCES reconciliation_attempts(id,job_id) DEFERRABLE INITIALLY DEFERRED,
 CHECK((source='event' AND event_id IS NOT NULL AND requested_by IS NULL AND request_key_hash IS NULL AND request_order_version IS NULL) OR
       (source='operator' AND event_id IS NULL AND requested_by IS NOT NULL AND request_key_hash IS NOT NULL AND request_order_version IS NOT NULL AND request_order_version>=1)),
 CHECK((state='running' AND lease_until IS NOT NULL AND current_attempt_id IS NOT NULL) OR (state!='running' AND lease_until IS NULL)),
 CHECK(attempt_count<=max_attempts),
 CHECK((state='queued' AND attempt_count=0 AND current_attempt_id IS NULL) OR (state!='queued' AND attempt_count>=1 AND current_attempt_id IS NOT NULL))
);
CREATE UNIQUE INDEX reconciliation_one_running_order ON reconciliation_jobs(order_id) WHERE state='running';
CREATE INDEX reconciliation_ready ON reconciliation_jobs(provider,mode,merchant_hash,state,available_at,created_at);
CREATE TABLE reconciliation_attempts(
 id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES reconciliation_jobs(id),ordinal INTEGER NOT NULL CHECK(ordinal>=1),
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
CREATE TRIGGER reconciliation_scope_no_update BEFORE UPDATE ON reconciliation_order_scopes BEGIN SELECT RAISE(ABORT,'Scope observation is immutable'); END;
CREATE TRIGGER reconciliation_scope_no_delete BEFORE DELETE ON reconciliation_order_scopes BEGIN SELECT RAISE(ABORT,'Scope observation is retained'); END;
CREATE TRIGGER provider_event_no_update BEFORE UPDATE ON provider_events BEGIN SELECT RAISE(ABORT,'Provider receipt is immutable'); END;
CREATE TRIGGER provider_event_no_delete BEFORE DELETE ON provider_events BEGIN SELECT RAISE(ABORT,'Provider receipt is retained'); END;
CREATE TRIGGER provider_conflict_no_update BEFORE UPDATE ON provider_event_conflicts BEGIN SELECT RAISE(ABORT,'Conflict receipt is immutable'); END;
CREATE TRIGGER provider_conflict_no_delete BEFORE DELETE ON provider_event_conflicts BEGIN SELECT RAISE(ABORT,'Conflict receipt is retained'); END;
CREATE TRIGGER provider_link_no_update BEFORE UPDATE ON provider_event_links BEGIN SELECT RAISE(ABORT,'Event association is immutable'); END;
CREATE TRIGGER provider_link_no_delete BEFORE DELETE ON provider_event_links BEGIN SELECT RAISE(ABORT,'Event association is retained'); END;
CREATE TRIGGER reconciliation_job_no_delete BEFORE DELETE ON reconciliation_jobs BEGIN SELECT RAISE(ABORT,'Reconciliation job is retained'); END;
CREATE TRIGGER reconciliation_attempt_no_delete BEFORE DELETE ON reconciliation_attempts BEGIN SELECT RAISE(ABORT,'Reconciliation attempt is retained'); END;
INSERT INTO schema_migrations VALUES(25,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=25;
"""
