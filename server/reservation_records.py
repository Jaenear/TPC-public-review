"""Additive v29. Preserve legacy inquiries; no policies or reservations are seeded."""

RESERVATION_TABLES = frozenset({
    'reservation_policies', 'slot_holds', 'booking_occupancies',
    'coaching_reservations', 'booking_sessions', 'booking_events',
    'booking_commands', 'booking_reschedules', 'booking_payment_facts',
})

SCHEMA_V29 = """
CREATE TABLE reservation_policies(
 id TEXT PRIMARY KEY,product_id TEXT NOT NULL REFERENCES coaching_products(id),
 product_revision_id TEXT NOT NULL,product_version INTEGER NOT NULL CHECK(product_version>0),
 scope TEXT NOT NULL CHECK(scope IN ('technical','commercial')),
 payload TEXT NOT NULL,payload_hash TEXT NOT NULL,approval_reference TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(product_revision_id,product_id) REFERENCES coaching_product_revisions(id,product_id),
 UNIQUE(product_id,product_revision_id,payload_hash)
);
CREATE TABLE slot_holds(
 id TEXT PRIMARY KEY,member_id TEXT NOT NULL REFERENCES users(id),
 coach_id TEXT NOT NULL REFERENCES coach_profiles(id),owner_id TEXT NOT NULL REFERENCES users(id),
 product_id TEXT NOT NULL REFERENCES coaching_products(id),slot_id TEXT NOT NULL REFERENCES coaching_slots(id),
 policy_id TEXT NOT NULL REFERENCES reservation_policies(id),quote TEXT NOT NULL,quote_hash TEXT NOT NULL,
 reservation_id TEXT REFERENCES coaching_reservations(id),session_ordinal INTEGER CHECK(session_ordinal BETWEEN 1 AND 24),
 state TEXT NOT NULL CHECK(state IN ('active','released','consumed','expired')),
 expires_at TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 CHECK(expires_at>created_at),CHECK((reservation_id IS NULL AND session_ordinal IS NULL) OR (reservation_id IS NOT NULL AND session_ordinal IS NOT NULL))
);
CREATE TABLE coaching_reservations(
 id TEXT PRIMARY KEY,member_id TEXT NOT NULL REFERENCES users(id),
 coach_id TEXT NOT NULL REFERENCES coach_profiles(id),owner_id TEXT NOT NULL REFERENCES users(id),
 product_id TEXT NOT NULL REFERENCES coaching_products(id),policy_id TEXT NOT NULL REFERENCES reservation_policies(id),
 legacy_inquiry_id TEXT REFERENCES bookings(id),quote TEXT NOT NULL,quote_hash TEXT NOT NULL,
 scope TEXT NOT NULL CHECK(scope IN ('technical','commercial')),
 price INTEGER NOT NULL CHECK(price>=0),currency TEXT NOT NULL CHECK(currency='KRW'),payment_order_ref TEXT NOT NULL UNIQUE,
 sequence TEXT NOT NULL CHECK(sequence IN ('coach_then_payment','payment_then_coach')),
 state TEXT NOT NULL CHECK(state IN ('awaiting_coach','awaiting_payment','confirmed','completed','cancelled','expired','exception')),
 payment_state TEXT NOT NULL CHECK(payment_state IN ('not_required','unpaid','paid','refund_pending','refunded','exception')),
 payment_fact_id TEXT REFERENCES booking_payment_facts(id),
 session_count INTEGER NOT NULL CHECK(session_count BETWEEN 1 AND 24),
 no_show_consumes INTEGER NOT NULL CHECK(no_show_consumes IN (0,1)),
 deadline_at TEXT,coach_accepted_at TEXT,confirmed_at TEXT,valid_until TEXT,
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 CHECK((price=0 AND payment_state='not_required') OR (price>0 AND payment_state!='not_required')),
 CHECK(state NOT IN ('confirmed','completed') OR (payment_state IN ('not_required','paid') AND coach_accepted_at IS NOT NULL AND confirmed_at IS NOT NULL)),
 CHECK(payment_state NOT IN ('paid','refund_pending','refunded') OR payment_fact_id IS NOT NULL),
 CHECK((state IN ('awaiting_coach','awaiting_payment') AND deadline_at IS NOT NULL) OR (state NOT IN ('awaiting_coach','awaiting_payment') AND deadline_at IS NULL))
);
CREATE INDEX coaching_reservation_member ON coaching_reservations(member_id,created_at,id);
CREATE INDEX coaching_reservation_coach ON coaching_reservations(coach_id,state,created_at,id);
CREATE UNIQUE INDEX coaching_reservation_one_inquiry ON coaching_reservations(legacy_inquiry_id) WHERE legacy_inquiry_id IS NOT NULL;
CREATE TABLE booking_sessions(
 id TEXT PRIMARY KEY,reservation_id TEXT NOT NULL REFERENCES coaching_reservations(id),
 ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 24),
 state TEXT NOT NULL CHECK(state IN ('unscheduled','requested','confirmed','completed','no_show','cancelled')),
 slot_id TEXT REFERENCES coaching_slots(id),occupancy_id TEXT UNIQUE REFERENCES booking_occupancies(id),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),performed_at TEXT,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(reservation_id,ordinal),
 CHECK((state='unscheduled' AND slot_id IS NULL AND occupancy_id IS NULL) OR state!='unscheduled'),
 CHECK(state NOT IN ('requested','confirmed','completed','no_show') OR (slot_id IS NOT NULL AND occupancy_id IS NOT NULL))
);
CREATE TABLE booking_occupancies(
 id TEXT PRIMARY KEY,hold_id TEXT NOT NULL UNIQUE REFERENCES slot_holds(id),
 reservation_id TEXT REFERENCES coaching_reservations(id),session_id TEXT REFERENCES booking_sessions(id),
 coach_id TEXT NOT NULL REFERENCES coach_profiles(id),slot_id TEXT NOT NULL REFERENCES coaching_slots(id),
 occupied_from TEXT NOT NULL,occupied_until TEXT NOT NULL CHECK(occupied_until>occupied_from),
 kind TEXT NOT NULL CHECK(kind IN ('hold','request','confirmed')),
 active INTEGER NOT NULL CHECK(active IN (0,1)),expires_at TEXT,checked_at TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),created_at TEXT NOT NULL,
 CHECK((kind='hold' AND reservation_id IS NULL AND session_id IS NULL) OR (kind!='hold' AND reservation_id IS NOT NULL AND session_id IS NOT NULL)),
 CHECK((kind='confirmed' AND expires_at IS NULL) OR (kind!='confirmed' AND expires_at IS NOT NULL)),
 CHECK(active=0 OR kind='confirmed' OR expires_at>checked_at)
);
CREATE INDEX booking_occupancy_range ON booking_occupancies(coach_id,active,occupied_from,occupied_until,expires_at);
CREATE INDEX booking_occupancy_expiry ON booking_occupancies(active,expires_at,id);
CREATE UNIQUE INDEX booking_one_active_appointment ON booking_occupancies(session_id) WHERE active=1 AND session_id IS NOT NULL;
CREATE TABLE booking_events(
 id TEXT PRIMARY KEY,reservation_id TEXT REFERENCES coaching_reservations(id),hold_id TEXT REFERENCES slot_holds(id),
 session_id TEXT REFERENCES booking_sessions(id),actor_id TEXT REFERENCES users(id),
 action TEXT NOT NULL,version INTEGER NOT NULL,reason TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL,
 CHECK(reservation_id IS NOT NULL OR hold_id IS NOT NULL)
);
CREATE INDEX booking_event_reservation ON booking_events(reservation_id,created_at,id);
CREATE INDEX booking_event_hold ON booking_events(hold_id,created_at,id);
CREATE TABLE booking_commands(
 id TEXT PRIMARY KEY,actor_scope TEXT NOT NULL,actor_id TEXT REFERENCES users(id),
 request_key TEXT NOT NULL,command TEXT NOT NULL,request_hash TEXT NOT NULL,
 entity_type TEXT NOT NULL CHECK(entity_type IN ('policy','hold','reservation')),
 entity_id TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(actor_scope,request_key)
);
CREATE TABLE booking_reschedules(
 id TEXT PRIMARY KEY,reservation_id TEXT NOT NULL REFERENCES coaching_reservations(id),
 session_id TEXT NOT NULL REFERENCES booking_sessions(id),
 from_slot_id TEXT NOT NULL REFERENCES coaching_slots(id),to_slot_id TEXT NOT NULL REFERENCES coaching_slots(id),
 from_occupancy_id TEXT NOT NULL REFERENCES booking_occupancies(id),to_occupancy_id TEXT NOT NULL UNIQUE REFERENCES booking_occupancies(id),
 target_hold_id TEXT NOT NULL UNIQUE REFERENCES slot_holds(id),
 actor_id TEXT NOT NULL REFERENCES users(id),reason TEXT NOT NULL,reservation_version INTEGER NOT NULL,created_at TEXT NOT NULL,
 CHECK(from_slot_id!=to_slot_id)
);
CREATE TABLE booking_payment_facts(
 id TEXT PRIMARY KEY,reservation_id TEXT NOT NULL REFERENCES coaching_reservations(id),
 kind TEXT NOT NULL CHECK(kind IN ('paid','refunded')),
 amount INTEGER NOT NULL CHECK(amount>0),currency TEXT NOT NULL CHECK(currency='KRW'),
 mode TEXT NOT NULL CHECK(mode IN ('synthetic','live')),authority TEXT NOT NULL,source_event_id TEXT NOT NULL,
 source_order_id TEXT NOT NULL,source_hash TEXT NOT NULL,paid_fact_id TEXT REFERENCES booking_payment_facts(id),
 observed_at TEXT NOT NULL,UNIQUE(authority,source_event_id),UNIQUE(reservation_id,kind),
 CHECK((kind='paid' AND paid_fact_id IS NULL) OR (kind='refunded' AND paid_fact_id IS NOT NULL))
);
CREATE TRIGGER booking_overlap_insert BEFORE INSERT ON booking_occupancies
 WHEN NEW.active=1 AND EXISTS(SELECT 1 FROM booking_occupancies x WHERE x.coach_id=NEW.coach_id AND x.active=1 AND (x.expires_at IS NULL OR x.expires_at>NEW.checked_at) AND x.occupied_from<NEW.occupied_until AND x.occupied_until>NEW.occupied_from)
 BEGIN SELECT RAISE(ABORT,'Coach time range is already occupied'); END;
CREATE TRIGGER booking_overlap_update BEFORE UPDATE ON booking_occupancies
 WHEN NEW.active=1 AND EXISTS(SELECT 1 FROM booking_occupancies x WHERE x.id!=OLD.id AND x.coach_id=NEW.coach_id AND x.active=1 AND (x.expires_at IS NULL OR x.expires_at>NEW.checked_at) AND x.occupied_from<NEW.occupied_until AND x.occupied_until>NEW.occupied_from)
 BEGIN SELECT RAISE(ABORT,'Coach time range is already occupied'); END;
CREATE TRIGGER booking_occupancy_facts BEFORE UPDATE ON booking_occupancies
 WHEN NEW.id!=OLD.id OR NEW.hold_id!=OLD.hold_id OR NEW.coach_id!=OLD.coach_id OR NEW.slot_id!=OLD.slot_id OR NEW.occupied_from!=OLD.occupied_from OR NEW.occupied_until!=OLD.occupied_until OR NEW.created_at!=OLD.created_at OR NEW.version!=OLD.version+1 OR NEW.checked_at<OLD.checked_at OR (OLD.active=0 AND NEW.active!=0) OR (OLD.reservation_id IS NOT NULL AND (NEW.reservation_id IS NOT OLD.reservation_id OR NEW.session_id IS NOT OLD.session_id))
 BEGIN SELECT RAISE(ABORT,'Occupancy identity/range/history is immutable'); END;
CREATE TRIGGER booking_occupancy_slot BEFORE INSERT ON booking_occupancies
 WHEN NOT EXISTS(SELECT 1 FROM coaching_slots s JOIN slot_holds h ON h.id=NEW.hold_id WHERE s.id=NEW.slot_id AND s.id=h.slot_id AND s.coach_id=NEW.coach_id AND s.occupied_from=NEW.occupied_from AND s.occupied_until=NEW.occupied_until)
 BEGIN SELECT RAISE(ABORT,'Occupancy must match its original candidate'); END;
CREATE TRIGGER booking_hold_candidate BEFORE INSERT ON slot_holds
 WHEN NOT EXISTS(SELECT 1 FROM coaching_slots s JOIN reservation_policies p ON p.id=NEW.policy_id WHERE s.id=NEW.slot_id AND s.product_id=NEW.product_id AND s.coach_id=NEW.coach_id AND p.product_id=NEW.product_id)
 BEGIN SELECT RAISE(ABORT,'Hold must match its product/coach/policy candidate'); END;
CREATE TRIGGER booking_occupancy_attachment BEFORE UPDATE ON booking_occupancies
 WHEN NEW.reservation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM booking_sessions s JOIN coaching_reservations r ON r.id=s.reservation_id JOIN slot_holds h ON h.id=NEW.hold_id WHERE s.id=NEW.session_id AND r.id=NEW.reservation_id AND r.coach_id=NEW.coach_id AND r.member_id=h.member_id AND r.product_id=h.product_id AND s.slot_id=NEW.slot_id AND s.occupancy_id=NEW.id)
 BEGIN SELECT RAISE(ABORT,'Occupancy must attach to its own reservation/session'); END;
CREATE TRIGGER booking_hold_facts BEFORE UPDATE ON slot_holds
 WHEN NEW.id!=OLD.id OR NEW.member_id!=OLD.member_id OR NEW.coach_id!=OLD.coach_id OR NEW.owner_id!=OLD.owner_id OR NEW.product_id!=OLD.product_id OR NEW.slot_id!=OLD.slot_id OR NEW.policy_id!=OLD.policy_id OR NEW.quote!=OLD.quote OR NEW.quote_hash!=OLD.quote_hash OR NEW.reservation_id IS NOT OLD.reservation_id OR NEW.session_ordinal IS NOT OLD.session_ordinal OR NEW.expires_at!=OLD.expires_at OR NEW.created_at!=OLD.created_at OR NEW.version!=OLD.version+1 OR OLD.state!='active' OR NEW.state NOT IN ('released','consumed','expired')
 BEGIN SELECT RAISE(ABORT,'Hold identity/quote/final state is immutable'); END;
CREATE TRIGGER booking_reservation_facts BEFORE UPDATE ON coaching_reservations
 WHEN NEW.id!=OLD.id OR NEW.member_id!=OLD.member_id OR NEW.coach_id!=OLD.coach_id OR NEW.owner_id!=OLD.owner_id OR NEW.product_id!=OLD.product_id OR NEW.policy_id!=OLD.policy_id OR NEW.legacy_inquiry_id IS NOT OLD.legacy_inquiry_id OR NEW.quote!=OLD.quote OR NEW.quote_hash!=OLD.quote_hash OR NEW.scope!=OLD.scope OR NEW.price!=OLD.price OR NEW.currency!=OLD.currency OR NEW.payment_order_ref!=OLD.payment_order_ref OR NEW.sequence!=OLD.sequence OR NEW.session_count!=OLD.session_count OR NEW.no_show_consumes!=OLD.no_show_consumes OR NEW.created_at!=OLD.created_at OR NEW.version!=OLD.version+1 OR NEW.updated_at<OLD.updated_at OR (OLD.state IN ('completed','cancelled') AND NEW.state!=OLD.state AND NEW.state!='exception')
 BEGIN SELECT RAISE(ABORT,'Reservation identity/quote/history is immutable'); END;
CREATE TRIGGER booking_inquiry_link BEFORE INSERT ON coaching_reservations
 WHEN NEW.legacy_inquiry_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM bookings b WHERE b.id=NEW.legacy_inquiry_id AND b.user_id=NEW.member_id AND b.coach_id=NEW.coach_id)
 BEGIN SELECT RAISE(ABORT,'Legacy inquiry does not belong to this member/coach'); END;
CREATE TRIGGER booking_pending_origin BEFORE INSERT ON coaching_reservations
 WHEN NEW.state NOT IN ('awaiting_coach','awaiting_payment') OR NEW.payment_state NOT IN ('unpaid','not_required') OR NEW.payment_fact_id IS NOT NULL OR NEW.coach_accepted_at IS NOT NULL OR NEW.confirmed_at IS NOT NULL
 BEGIN SELECT RAISE(ABORT,'New reservations start pending without assumed financial/coach facts'); END;
CREATE TRIGGER booking_payment_link BEFORE UPDATE ON coaching_reservations
 WHEN NEW.payment_state IN ('paid','refund_pending','refunded') AND NOT EXISTS(SELECT 1 FROM booking_payment_facts f WHERE f.id=NEW.payment_fact_id AND f.reservation_id=NEW.id AND f.kind=CASE WHEN NEW.payment_state='refunded' THEN 'refunded' ELSE 'paid' END AND f.amount=NEW.price AND f.currency=NEW.currency)
 BEGIN SELECT RAISE(ABORT,'Payment state requires its own verified fact'); END;
CREATE TRIGGER booking_terminal_no_occupancy BEFORE UPDATE ON coaching_reservations
 WHEN NEW.state IN ('completed','cancelled','expired','exception') AND EXISTS(SELECT 1 FROM booking_occupancies o WHERE o.reservation_id=NEW.id AND o.active=1)
 BEGIN SELECT RAISE(ABORT,'Closed reservations cannot keep active occupancy'); END;
CREATE TRIGGER booking_package_completion BEFORE UPDATE ON coaching_reservations
 WHEN NEW.state='completed' AND ((SELECT COUNT(*) FROM booking_sessions s WHERE s.reservation_id=NEW.id)!=NEW.session_count OR EXISTS(SELECT 1 FROM booking_sessions s WHERE s.reservation_id=NEW.id AND s.state!='completed' AND NOT(s.state='no_show' AND NEW.no_show_consumes=1)))
 BEGIN SELECT RAISE(ABORT,'Every purchased session must be consumed before completion'); END;
CREATE TRIGGER booking_session_facts BEFORE UPDATE ON booking_sessions
 WHEN NEW.id!=OLD.id OR NEW.reservation_id!=OLD.reservation_id OR NEW.ordinal!=OLD.ordinal OR NEW.created_at!=OLD.created_at OR NEW.version!=OLD.version+1 OR NEW.updated_at<OLD.updated_at OR (OLD.state='completed' AND (NEW.state!=OLD.state OR NEW.slot_id IS NOT OLD.slot_id OR NEW.occupancy_id IS NOT OLD.occupancy_id))
 BEGIN SELECT RAISE(ABORT,'Session identity/completed history is immutable'); END;
CREATE TRIGGER booking_session_ordinal BEFORE INSERT ON booking_sessions
 WHEN NEW.ordinal>(SELECT session_count FROM coaching_reservations WHERE id=NEW.reservation_id)
 BEGIN SELECT RAISE(ABORT,'Session ordinal exceeds the purchased package'); END;
CREATE TRIGGER booking_payment_fact_match BEFORE INSERT ON booking_payment_facts
 WHEN NOT EXISTS(SELECT 1 FROM coaching_reservations r WHERE r.id=NEW.reservation_id AND r.price=NEW.amount AND r.currency=NEW.currency AND r.payment_order_ref=NEW.source_order_id AND ((r.scope='technical' AND NEW.mode='synthetic') OR (r.scope='commercial' AND NEW.mode='live'))) OR (NEW.kind='refunded' AND NOT EXISTS(SELECT 1 FROM booking_payment_facts f WHERE f.id=NEW.paid_fact_id AND f.reservation_id=NEW.reservation_id AND f.kind='paid' AND f.amount=NEW.amount AND f.source_order_id=NEW.source_order_id))
 BEGIN SELECT RAISE(ABORT,'Financial fact does not match its reservation/mode'); END;
""" + ''.join(
    "CREATE TRIGGER "+table+"_no_delete BEFORE DELETE ON "+table+" BEGIN SELECT RAISE(ABORT,'Reservation records are retained'); END;\n"
    for table in sorted(RESERVATION_TABLES)
) + ''.join(
    "CREATE TRIGGER "+table+"_no_update BEFORE UPDATE ON "+table+" BEGIN SELECT RAISE(ABORT,'Reservation original facts are immutable'); END;\n"
    for table in ['reservation_policies','booking_events','booking_commands','booking_reschedules','booking_payment_facts']
) + """
INSERT INTO schema_migrations VALUES(29,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=29;
"""
