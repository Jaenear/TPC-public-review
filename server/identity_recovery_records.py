"""Additive v33 transient recovery admission. Applied SQL3–32 is unchanged."""

RECOVERY_INTAKE_TABLES = {'identity_recovery_intakes'}

SCHEMA_V33 = """
CREATE TABLE identity_recovery_intakes(
 id TEXT PRIMARY KEY CHECK(length(id)=47 AND substr(id,1,15)='recoveryintake_' AND substr(id,16) NOT GLOB '*[^0-9a-f]*'),
 request_key_hash TEXT NOT NULL UNIQUE CHECK(length(request_key_hash)=64 AND request_key_hash NOT GLOB '*[^0-9a-f]*'),
 input_hash TEXT NOT NULL CHECK(length(input_hash)=64 AND input_hash NOT GLOB '*[^0-9a-f]*'),
 user_id TEXT REFERENCES users(id),
 version INTEGER NOT NULL CHECK(version IN (1,2)),
 state TEXT NOT NULL CHECK(state IN ('pending','processed','ignored','expired')),
 code TEXT CHECK(code IN ('issued','not_eligible','quota','expired')),
 command_id TEXT UNIQUE REFERENCES identity_commands(id) DEFERRABLE INITIALLY DEFERRED,
 created_at INTEGER NOT NULL,
 expires_at INTEGER NOT NULL CHECK(expires_at=created_at+900),
 finished_at INTEGER,
 CHECK((state='pending')=(version=1)),
 CHECK((state='pending')=(finished_at IS NULL)),
 CHECK((state='pending')=(code IS NULL)),
 CHECK(finished_at IS NULL OR finished_at>=created_at),
 CHECK((state='processed')=(command_id IS NOT NULL)),
 CHECK(state!='processed' OR (user_id IS NOT NULL AND code='issued' AND finished_at<expires_at)),
 CHECK(state!='ignored' OR code IN ('not_eligible','quota')),
 CHECK(state!='expired' OR (code='expired' AND finished_at>=expires_at))
);
CREATE INDEX identity_recovery_pending ON identity_recovery_intakes(state,created_at,id);
CREATE INDEX identity_recovery_retention ON identity_recovery_intakes(state,finished_at);
CREATE TRIGGER identity_recovery_initial BEFORE INSERT ON identity_recovery_intakes
 WHEN NEW.version!=1 OR NEW.state!='pending' OR NEW.code IS NOT NULL OR NEW.command_id IS NOT NULL OR NEW.finished_at IS NOT NULL
 BEGIN SELECT RAISE(ABORT,'Recovery admission starts pending'); END;
CREATE TRIGGER identity_recovery_transition BEFORE UPDATE ON identity_recovery_intakes
 WHEN OLD.state!='pending' OR NEW.state='pending' OR NEW.version!=OLD.version+1
 OR NEW.id!=OLD.id OR NEW.request_key_hash!=OLD.request_key_hash OR NEW.input_hash!=OLD.input_hash
 OR NEW.user_id IS NOT OLD.user_id OR NEW.created_at!=OLD.created_at OR NEW.expires_at!=OLD.expires_at
 OR (NEW.state='processed' AND NOT EXISTS(
  SELECT 1 FROM identity_commands c WHERE c.id=NEW.command_id AND c.user_id=OLD.user_id
  AND c.operation='reset_password.issue' AND c.request_key='recovery_'||OLD.id))
 BEGIN SELECT RAISE(ABORT,'Recovery admission binds one terminal issue'); END;
CREATE TRIGGER identity_recovery_pending_no_delete BEFORE DELETE ON identity_recovery_intakes
 WHEN OLD.state='pending'
 BEGIN SELECT RAISE(ABORT,'Pending recovery admission must be resolved before retention cleanup'); END;
INSERT INTO schema_migrations VALUES(33,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=33;
"""
