"""v27: actual operator ownership, immutable review events and command receipts.

No historical review is inferred. These records cannot approve a payment,
cancel money, change an access grant or complete a reconciliation job.
"""

RECONCILIATION_DESK_TABLES = {
    "reconciliation_cases", "reconciliation_case_events", "reconciliation_case_commands",
}

SCHEMA_V27 = r"""
CREATE TABLE reconciliation_cases(
 id TEXT PRIMARY KEY,subject_kind TEXT NOT NULL CHECK(subject_kind IN ('order','event')),
 order_id TEXT UNIQUE REFERENCES purchase_orders(id),event_id TEXT UNIQUE REFERENCES provider_events(id),
 owner_id TEXT REFERENCES users(id),state TEXT NOT NULL CHECK(state IN ('open','investigating','resolved')),
 version INTEGER NOT NULL CHECK(version>=1),context_json TEXT NOT NULL CHECK(json_valid(context_json) AND length(context_json)<=32768),
 context_hash TEXT NOT NULL CHECK(length(context_hash)=64),created_by TEXT NOT NULL REFERENCES users(id),
 created_at INTEGER NOT NULL CHECK(created_at>=0),updated_at INTEGER NOT NULL CHECK(updated_at>=created_at),
 CHECK((subject_kind='order' AND order_id IS NOT NULL AND event_id IS NULL) OR (subject_kind='event' AND event_id IS NOT NULL AND order_id IS NULL)),
 FOREIGN KEY(id,version) REFERENCES reconciliation_case_events(case_id,version) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE reconciliation_case_events(
 id TEXT PRIMARY KEY,case_id TEXT NOT NULL REFERENCES reconciliation_cases(id),version INTEGER NOT NULL CHECK(version>=1),
 action TEXT NOT NULL CHECK(action IN ('claim','takeover','release','note','investigate','resolve','reopen')),
 prior_state TEXT CHECK(prior_state IN ('open','investigating','resolved')),state TEXT NOT NULL CHECK(state IN ('open','investigating','resolved')),
 prior_owner_id TEXT REFERENCES users(id),owner_id TEXT REFERENCES users(id),actor_id TEXT NOT NULL REFERENCES users(id),
 note TEXT NOT NULL CHECK(length(note) BETWEEN 10 AND 1500),context_hash TEXT NOT NULL CHECK(length(context_hash)=64),
 created_at INTEGER NOT NULL CHECK(created_at>=0),UNIQUE(case_id,version),UNIQUE(id,case_id)
);
CREATE TABLE reconciliation_case_commands(
 actor_id TEXT NOT NULL REFERENCES users(id),key_hash TEXT NOT NULL CHECK(length(key_hash)=64),
 request_hash TEXT NOT NULL CHECK(length(request_hash)=64),case_id TEXT NOT NULL REFERENCES reconciliation_cases(id),
 event_id TEXT NOT NULL UNIQUE,subject_kind TEXT NOT NULL CHECK(subject_kind IN ('order','event')),subject_id TEXT NOT NULL,
 expected_version INTEGER NOT NULL CHECK(expected_version>=0),expected_context_hash TEXT NOT NULL CHECK(length(expected_context_hash)=64),
 created_at INTEGER NOT NULL CHECK(created_at>=0),PRIMARY KEY(actor_id,key_hash),
 FOREIGN KEY(event_id,case_id) REFERENCES reconciliation_case_events(id,case_id)
);
CREATE INDEX reconciliation_case_owner ON reconciliation_cases(owner_id,state,updated_at DESC);
CREATE INDEX reconciliation_case_history ON reconciliation_case_events(case_id,version DESC);
CREATE TRIGGER reconciliation_case_origin BEFORE INSERT ON reconciliation_cases WHEN
 NEW.version!=1 OR NEW.state!='open' OR NEW.owner_id IS NOT NEW.created_by OR
 NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND role='admin' AND status='active')
 BEGIN SELECT RAISE(ABORT,'Review starts with an actual current operator claim'); END;
CREATE TRIGGER reconciliation_case_transition BEFORE UPDATE ON reconciliation_cases WHEN
 NEW.id IS NOT OLD.id OR NEW.subject_kind IS NOT OLD.subject_kind OR NEW.order_id IS NOT OLD.order_id OR NEW.event_id IS NOT OLD.event_id OR
 NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at OR NEW.version!=OLD.version+1 OR NEW.updated_at<OLD.updated_at
 BEGIN SELECT RAISE(ABORT,'Only the next review observation may change'); END;
CREATE TRIGGER reconciliation_case_no_delete BEFORE DELETE ON reconciliation_cases BEGIN SELECT RAISE(ABORT,'Operator review is retained'); END;
CREATE TRIGGER reconciliation_case_event_authority BEFORE INSERT ON reconciliation_case_events WHEN
 NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.actor_id AND role='admin' AND status='active') OR
 NOT EXISTS(SELECT 1 FROM reconciliation_cases WHERE id=NEW.case_id AND version=NEW.version AND state=NEW.state AND owner_id IS NEW.owner_id AND context_hash=NEW.context_hash AND updated_at=NEW.created_at) OR
 (NEW.version=1 AND (NEW.action!='claim' OR NEW.prior_state IS NOT NULL OR NEW.prior_owner_id IS NOT NULL OR NEW.owner_id IS NOT NEW.actor_id)) OR
 (NEW.version>1 AND NOT EXISTS(SELECT 1 FROM reconciliation_case_events WHERE case_id=NEW.case_id AND version=NEW.version-1 AND state=NEW.prior_state AND owner_id IS NEW.prior_owner_id)) OR
 NOT COALESCE((
   (NEW.action='claim' AND NEW.prior_owner_id IS NULL AND NEW.owner_id=NEW.actor_id AND (NEW.version=1 OR (NEW.prior_state!='resolved' AND NEW.state=NEW.prior_state))) OR
   (NEW.action='takeover' AND NEW.prior_owner_id IS NOT NULL AND NEW.prior_owner_id!=NEW.actor_id AND NEW.owner_id=NEW.actor_id AND NEW.state=NEW.prior_state) OR
   (NEW.action='release' AND NEW.prior_owner_id=NEW.actor_id AND NEW.owner_id IS NULL AND NEW.state=NEW.prior_state AND NEW.prior_state!='resolved') OR
   (NEW.action='note' AND NEW.prior_owner_id=NEW.actor_id AND NEW.owner_id=NEW.actor_id AND NEW.state=NEW.prior_state AND NEW.prior_state!='resolved') OR
   (NEW.action='investigate' AND NEW.prior_owner_id=NEW.actor_id AND NEW.owner_id=NEW.actor_id AND NEW.prior_state!='resolved' AND NEW.state='investigating') OR
   (NEW.action='resolve' AND NEW.prior_owner_id=NEW.actor_id AND NEW.owner_id=NEW.actor_id AND NEW.prior_state!='resolved' AND NEW.state='resolved') OR
   (NEW.action='reopen' AND NEW.prior_owner_id=NEW.actor_id AND NEW.owner_id=NEW.actor_id AND NEW.prior_state='resolved' AND NEW.state='open')
 ),0) BEGIN SELECT RAISE(ABORT,'Review event must match its current actor, parent and real transition'); END;
CREATE TRIGGER reconciliation_case_event_no_update BEFORE UPDATE ON reconciliation_case_events BEGIN SELECT RAISE(ABORT,'Review event is immutable'); END;
CREATE TRIGGER reconciliation_case_event_no_delete BEFORE DELETE ON reconciliation_case_events BEGIN SELECT RAISE(ABORT,'Review event is retained'); END;
CREATE TRIGGER reconciliation_case_command_authority BEFORE INSERT ON reconciliation_case_commands WHEN
 NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.actor_id AND role='admin' AND status='active') OR
 NOT EXISTS(SELECT 1 FROM reconciliation_case_events e JOIN reconciliation_cases c ON c.id=e.case_id WHERE e.id=NEW.event_id AND e.case_id=NEW.case_id AND e.actor_id=NEW.actor_id AND e.version=NEW.expected_version+1 AND c.subject_kind=NEW.subject_kind AND COALESCE(c.order_id,c.event_id)=NEW.subject_id AND e.context_hash=NEW.expected_context_hash AND e.created_at=NEW.created_at)
 BEGIN SELECT RAISE(ABORT,'Review command requires its actual immutable event'); END;
CREATE TRIGGER reconciliation_case_command_no_update BEFORE UPDATE ON reconciliation_case_commands BEGIN SELECT RAISE(ABORT,'Review receipt is immutable'); END;
CREATE TRIGGER reconciliation_case_command_no_delete BEFORE DELETE ON reconciliation_case_commands BEGIN SELECT RAISE(ABORT,'Review receipt is retained'); END;
INSERT INTO schema_migrations VALUES(27,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=27;
"""
