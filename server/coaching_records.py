"""Additive v28. Candidate slots are not reservations or payment authority."""

COACHING_TABLES = frozenset({
    "coaching_products", "coaching_product_revisions", "coaching_calendars",
    "coaching_availability_revisions", "coaching_slots", "coaching_events",
    "coaching_commands",
})

SCHEMA_V28 = """
CREATE TABLE coaching_products(
 id TEXT PRIMARY KEY,coach_id TEXT NOT NULL REFERENCES coach_profiles(id),
 state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','paused')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 public_version INTEGER NOT NULL DEFAULT 0 CHECK(public_version>=0),
 public_revision_id TEXT,created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(id,coach_id),
 FOREIGN KEY(public_revision_id,id) REFERENCES coaching_product_revisions(id,product_id)
);
CREATE TABLE coaching_product_revisions(
 id TEXT PRIMARY KEY,product_id TEXT NOT NULL REFERENCES coaching_products(id),
 status TEXT NOT NULL CHECK(status IN ('draft','submitted','approved','rejected')),
 payload TEXT NOT NULL,payload_hash TEXT NOT NULL,
 base_public_version INTEGER NOT NULL,profile_revision_id TEXT NOT NULL REFERENCES coach_profile_revisions(id),
 profile_version INTEGER NOT NULL,owner_id TEXT NOT NULL REFERENCES users(id),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),created_by TEXT NOT NULL REFERENCES users(id),
 reason TEXT NOT NULL,review_note TEXT NOT NULL DEFAULT '',approval_reference TEXT NOT NULL DEFAULT '',
 reviewed_by TEXT REFERENCES users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 submitted_at TEXT,reviewed_at TEXT,UNIQUE(id,product_id)
);
CREATE UNIQUE INDEX coaching_product_one_open ON coaching_product_revisions(product_id) WHERE status IN ('draft','submitted');
CREATE INDEX coaching_product_history ON coaching_product_revisions(product_id,created_at,id);
CREATE INDEX coaching_product_coach ON coaching_products(coach_id,state,id);
CREATE TABLE coaching_calendars(
 coach_id TEXT PRIMARY KEY REFERENCES coach_profiles(id),
 state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','paused')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 public_version INTEGER NOT NULL DEFAULT 0 CHECK(public_version>=0),
 public_revision_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 FOREIGN KEY(public_revision_id,coach_id) REFERENCES coaching_availability_revisions(id,coach_id)
);
CREATE TABLE coaching_availability_revisions(
 id TEXT PRIMARY KEY,coach_id TEXT NOT NULL REFERENCES coaching_calendars(coach_id),
 status TEXT NOT NULL CHECK(status IN ('draft','submitted','approved','rejected')),
 payload TEXT NOT NULL,payload_hash TEXT NOT NULL,
 base_public_version INTEGER NOT NULL,profile_revision_id TEXT NOT NULL REFERENCES coach_profile_revisions(id),
 profile_version INTEGER NOT NULL,owner_id TEXT NOT NULL REFERENCES users(id),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),created_by TEXT NOT NULL REFERENCES users(id),
 reason TEXT NOT NULL,review_note TEXT NOT NULL DEFAULT '',approval_reference TEXT NOT NULL DEFAULT '',
 reviewed_by TEXT REFERENCES users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 submitted_at TEXT,reviewed_at TEXT,UNIQUE(id,coach_id)
);
CREATE UNIQUE INDEX coaching_calendar_one_open ON coaching_availability_revisions(coach_id) WHERE status IN ('draft','submitted');
CREATE TABLE coaching_slots(
 id TEXT PRIMARY KEY,product_id TEXT NOT NULL,coach_id TEXT NOT NULL,
 product_revision_id TEXT NOT NULL,calendar_revision_id TEXT NOT NULL,
 starts_at TEXT NOT NULL,ends_at TEXT NOT NULL CHECK(ends_at>starts_at),
 occupied_from TEXT NOT NULL,occupied_until TEXT NOT NULL,
 original_starts_at TEXT NOT NULL,original_ends_at TEXT NOT NULL,time_zone TEXT NOT NULL,
 created_at TEXT NOT NULL,
 CHECK(occupied_from<=starts_at AND occupied_until>=ends_at),
 UNIQUE(product_revision_id,calendar_revision_id,starts_at),
 FOREIGN KEY(product_id,coach_id) REFERENCES coaching_products(id,coach_id),
 FOREIGN KEY(product_revision_id,product_id) REFERENCES coaching_product_revisions(id,product_id),
 FOREIGN KEY(calendar_revision_id,coach_id) REFERENCES coaching_availability_revisions(id,coach_id)
);
CREATE INDEX coaching_slot_window ON coaching_slots(product_id,starts_at,id);
CREATE INDEX coaching_slot_coach_window ON coaching_slots(coach_id,occupied_from,occupied_until);
CREATE TABLE coaching_events(
 id TEXT PRIMARY KEY,product_id TEXT REFERENCES coaching_products(id),
 calendar_coach_id TEXT REFERENCES coaching_calendars(coach_id),
 actor_id TEXT NOT NULL REFERENCES users(id),action TEXT NOT NULL,
 revision_id TEXT,entity_version INTEGER NOT NULL,note TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL,
 CHECK((product_id IS NOT NULL AND calendar_coach_id IS NULL) OR (product_id IS NULL AND calendar_coach_id IS NOT NULL))
);
CREATE INDEX coaching_event_product ON coaching_events(product_id,created_at,id);
CREATE INDEX coaching_event_calendar ON coaching_events(calendar_coach_id,created_at,id);
CREATE TABLE coaching_commands(
 id TEXT PRIMARY KEY,actor_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,
 command TEXT NOT NULL,entity_type TEXT NOT NULL CHECK(entity_type IN ('product','calendar')),
 entity_id TEXT NOT NULL,request_hash TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(actor_id,request_key)
);
CREATE TRIGGER coaching_product_identity BEFORE UPDATE ON coaching_products
 WHEN NEW.id!=OLD.id OR NEW.coach_id!=OLD.coach_id OR NEW.created_by!=OLD.created_by OR NEW.created_at!=OLD.created_at
 BEGIN SELECT RAISE(ABORT,'Coaching product identity is immutable'); END;
CREATE TRIGGER coaching_calendar_identity BEFORE UPDATE ON coaching_calendars
 WHEN NEW.coach_id!=OLD.coach_id OR NEW.created_at!=OLD.created_at
 BEGIN SELECT RAISE(ABORT,'Coaching calendar identity is immutable'); END;
CREATE TRIGGER coaching_product_no_delete BEFORE DELETE ON coaching_products BEGIN SELECT RAISE(ABORT,'Coaching products are retained'); END;
CREATE TRIGGER coaching_calendar_no_delete BEFORE DELETE ON coaching_calendars BEGIN SELECT RAISE(ABORT,'Coaching calendars are retained'); END;
CREATE TRIGGER coaching_product_revision_final BEFORE UPDATE ON coaching_product_revisions
 WHEN OLD.status IN ('approved','rejected') BEGIN SELECT RAISE(ABORT,'Reviewed coaching content is immutable'); END;
CREATE TRIGGER coaching_calendar_revision_final BEFORE UPDATE ON coaching_availability_revisions
 WHEN OLD.status IN ('approved','rejected') BEGIN SELECT RAISE(ABORT,'Reviewed coaching schedule is immutable'); END;
CREATE TRIGGER coaching_product_revision_submitted BEFORE UPDATE ON coaching_product_revisions
 WHEN OLD.status='submitted' AND (NEW.payload!=OLD.payload OR NEW.payload_hash!=OLD.payload_hash OR NEW.base_public_version!=OLD.base_public_version OR NEW.profile_revision_id!=OLD.profile_revision_id OR NEW.profile_version!=OLD.profile_version OR NEW.owner_id!=OLD.owner_id OR NEW.created_by!=OLD.created_by OR NEW.product_id!=OLD.product_id OR NEW.id!=OLD.id OR NEW.created_at!=OLD.created_at)
 BEGIN SELECT RAISE(ABORT,'Submitted coaching content is locked'); END;
CREATE TRIGGER coaching_calendar_revision_submitted BEFORE UPDATE ON coaching_availability_revisions
 WHEN OLD.status='submitted' AND (NEW.payload!=OLD.payload OR NEW.payload_hash!=OLD.payload_hash OR NEW.base_public_version!=OLD.base_public_version OR NEW.profile_revision_id!=OLD.profile_revision_id OR NEW.profile_version!=OLD.profile_version OR NEW.owner_id!=OLD.owner_id OR NEW.created_by!=OLD.created_by OR NEW.coach_id!=OLD.coach_id OR NEW.id!=OLD.id OR NEW.created_at!=OLD.created_at)
 BEGIN SELECT RAISE(ABORT,'Submitted coaching schedule is locked'); END;
CREATE TRIGGER coaching_product_revision_no_delete BEFORE DELETE ON coaching_product_revisions BEGIN SELECT RAISE(ABORT,'Coaching content history is retained'); END;
CREATE TRIGGER coaching_calendar_revision_no_delete BEFORE DELETE ON coaching_availability_revisions BEGIN SELECT RAISE(ABORT,'Coaching schedule history is retained'); END;
CREATE TRIGGER coaching_slot_no_update BEFORE UPDATE ON coaching_slots BEGIN SELECT RAISE(ABORT,'Candidate slot facts are immutable'); END;
CREATE TRIGGER coaching_slot_no_delete BEFORE DELETE ON coaching_slots BEGIN SELECT RAISE(ABORT,'Candidate slot facts are retained'); END;
CREATE TRIGGER coaching_event_no_update BEFORE UPDATE ON coaching_events BEGIN SELECT RAISE(ABORT,'Coaching events are immutable'); END;
CREATE TRIGGER coaching_event_no_delete BEFORE DELETE ON coaching_events BEGIN SELECT RAISE(ABORT,'Coaching events are retained'); END;
CREATE TRIGGER coaching_command_no_update BEFORE UPDATE ON coaching_commands BEGIN SELECT RAISE(ABORT,'Coaching receipts are immutable'); END;
CREATE TRIGGER coaching_command_no_delete BEFORE DELETE ON coaching_commands BEGIN SELECT RAISE(ABORT,'Coaching receipts are retained'); END;
INSERT INTO schema_migrations VALUES(28,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=28;
"""
