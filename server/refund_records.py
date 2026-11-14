"""v24 refund intent receipts and immutable evidence, never historical approvals."""

import hashlib
from .core import now_iso

REFUND_TABLES = {"refund_reviews", "refund_operations", "refund_events", "refund_commands", "refund_legacy_attempts"}

SCHEMA_V24 = r"""
CREATE TABLE refund_reviews(
 id TEXT PRIMARY KEY,order_id TEXT NOT NULL REFERENCES purchase_orders(id),reviewer_id TEXT NOT NULL REFERENCES users(id),
 decision TEXT NOT NULL CHECK(decision IN ('full_cancel','reply')),context_hash TEXT NOT NULL CHECK(length(context_hash)=64),
 context_json TEXT NOT NULL CHECK(json_valid(context_json)),expected_case_version INTEGER NOT NULL CHECK(expected_case_version>=0),order_version INTEGER NOT NULL CHECK(order_version>=1),
 basis TEXT NOT NULL CHECK(length(basis) BETWEEN 10 AND 4000),operator_note TEXT NOT NULL DEFAULT '' CHECK(length(operator_note)<=4000),
 public_message TEXT NOT NULL CHECK(length(public_message) BETWEEN 5 AND 2000),cancel_reason TEXT NOT NULL,
 checks_json TEXT NOT NULL CHECK(json_valid(checks_json)),created_at TEXT NOT NULL,UNIQUE(id,order_id),
 CHECK((decision='full_cancel' AND length(cancel_reason) BETWEEN 5 AND 200) OR (decision='reply' AND cancel_reason=''))
);
CREATE INDEX refund_review_order ON refund_reviews(order_id,created_at,id);
CREATE TABLE refund_legacy_attempts(
 order_id TEXT PRIMARY KEY REFERENCES purchase_orders(id),payment_key_hash TEXT NOT NULL CHECK(length(payment_key_hash)=64),
 amount INTEGER NOT NULL CHECK(amount>0),currency TEXT NOT NULL CHECK(currency='KRW'),mode TEXT NOT NULL CHECK(mode IN ('test','live')),
 original_reason TEXT NOT NULL,prior_updated_at TEXT NOT NULL,observed_at TEXT NOT NULL
);
CREATE TABLE refund_operations(
 id TEXT PRIMARY KEY,order_id TEXT NOT NULL UNIQUE REFERENCES purchase_orders(id),review_id TEXT,
 origin TEXT NOT NULL CHECK(origin IN ('reviewed','legacy_recovery')),initiated_by TEXT NOT NULL REFERENCES users(id),
 payment_key_hash TEXT NOT NULL CHECK(length(payment_key_hash)=64),amount INTEGER NOT NULL CHECK(amount>0),currency TEXT NOT NULL CHECK(currency='KRW'),mode TEXT NOT NULL CHECK(mode IN ('test','live')),
 cancel_reason TEXT NOT NULL,public_message TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('checking','unverified','verified')),
 version INTEGER NOT NULL CHECK(version>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(id,order_id),
 FOREIGN KEY(review_id,order_id) REFERENCES refund_reviews(id,order_id),
 CHECK((origin='reviewed' AND review_id IS NOT NULL) OR (origin='legacy_recovery' AND review_id IS NULL))
);
CREATE TABLE refund_events(
 id TEXT PRIMARY KEY,order_id TEXT NOT NULL REFERENCES purchase_orders(id),version INTEGER NOT NULL CHECK(version>=1),
 kind TEXT NOT NULL CHECK(kind IN ('inquiry_submitted','review_saved','reply_sent','cancellation_started','cancellation_unverified','cancellation_verified','legacy_recovery_started')),
 visibility TEXT NOT NULL CHECK(visibility IN ('public','operator')),actor_id TEXT NOT NULL REFERENCES users(id),review_id TEXT,operation_id TEXT,
 body TEXT NOT NULL CHECK(length(body)<=4000),created_at TEXT NOT NULL,UNIQUE(order_id,version),
 FOREIGN KEY(review_id,order_id) REFERENCES refund_reviews(id,order_id),FOREIGN KEY(operation_id,order_id) REFERENCES refund_operations(id,order_id),
 CHECK((kind='review_saved' AND visibility='operator' AND review_id IS NOT NULL) OR (kind!='review_saved' AND visibility='public'))
);
CREATE INDEX refund_event_page ON refund_events(order_id,visibility,version DESC);
CREATE TABLE refund_commands(
 actor_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL CHECK(length(request_key) BETWEEN 12 AND 120),
 order_id TEXT NOT NULL REFERENCES purchase_orders(id),kind TEXT NOT NULL CHECK(kind IN ('inquiry','review','execute','recheck')),
 request_hash TEXT NOT NULL CHECK(length(request_hash)=64),receipt TEXT NOT NULL CHECK(json_valid(receipt)),created_at TEXT NOT NULL,
 PRIMARY KEY(actor_id,request_key)
);
CREATE TRIGGER refund_review_actor BEFORE INSERT ON refund_reviews WHEN NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.reviewer_id AND status='active' AND role='admin') OR json_extract(NEW.context_json,'$.order.id') IS NOT NEW.order_id OR json_extract(NEW.context_json,'$.order.version') IS NOT NEW.order_version OR json_extract(NEW.context_json,'$.viewerId') IS NOT NEW.reviewer_id OR json_extract(NEW.context_json,'$.contextHash') IS NOT NEW.context_hash BEGIN SELECT RAISE(ABORT,'Review requires a current operator and its original evidence'); END;
CREATE TRIGGER refund_review_no_update BEFORE UPDATE ON refund_reviews BEGIN SELECT RAISE(ABORT,'Refund review is immutable'); END;
CREATE TRIGGER refund_review_no_delete BEFORE DELETE ON refund_reviews BEGIN SELECT RAISE(ABORT,'Refund review is retained'); END;
CREATE TRIGGER refund_legacy_no_update BEFORE UPDATE ON refund_legacy_attempts BEGIN SELECT RAISE(ABORT,'Legacy cancellation marker is immutable'); END;
CREATE TRIGGER refund_legacy_no_delete BEFORE DELETE ON refund_legacy_attempts BEGIN SELECT RAISE(ABORT,'Legacy cancellation marker is retained'); END;
CREATE TRIGGER refund_operation_parent BEFORE INSERT ON refund_operations WHEN NOT EXISTS(SELECT 1 FROM purchase_orders WHERE id=NEW.order_id AND amount=NEW.amount AND currency=NEW.currency AND mode=NEW.mode AND status='refunding') OR (NEW.origin='reviewed' AND NOT EXISTS(SELECT 1 FROM refund_reviews WHERE id=NEW.review_id AND order_id=NEW.order_id AND decision='full_cancel' AND cancel_reason=NEW.cancel_reason AND public_message=NEW.public_message)) OR (NEW.origin='legacy_recovery' AND NOT EXISTS(SELECT 1 FROM refund_legacy_attempts WHERE order_id=NEW.order_id AND payment_key_hash=NEW.payment_key_hash AND amount=NEW.amount AND currency=NEW.currency AND mode=NEW.mode AND original_reason=NEW.cancel_reason)) BEGIN SELECT RAISE(ABORT,'Cancellation requires its matching review or real legacy attempt'); END;
CREATE TRIGGER refund_operation_update BEFORE UPDATE ON refund_operations WHEN NEW.id IS NOT OLD.id OR NEW.order_id IS NOT OLD.order_id OR NEW.review_id IS NOT OLD.review_id OR NEW.origin IS NOT OLD.origin OR NEW.initiated_by IS NOT OLD.initiated_by OR NEW.payment_key_hash IS NOT OLD.payment_key_hash OR NEW.amount IS NOT OLD.amount OR NEW.currency IS NOT OLD.currency OR NEW.mode IS NOT OLD.mode OR NEW.cancel_reason IS NOT OLD.cancel_reason OR NEW.public_message IS NOT OLD.public_message OR NEW.created_at IS NOT OLD.created_at OR NEW.version!=OLD.version+1 OR OLD.status='verified' OR (OLD.status='unverified' AND NEW.status!='checking') BEGIN SELECT RAISE(ABORT,'Only the next cancellation observation may change'); END;
CREATE TRIGGER refund_operation_no_delete BEFORE DELETE ON refund_operations BEGIN SELECT RAISE(ABORT,'Cancellation work is retained'); END;
CREATE TRIGGER refund_event_next BEFORE INSERT ON refund_events WHEN NEW.version!=COALESCE((SELECT MAX(version) FROM refund_events WHERE order_id=NEW.order_id),0)+1 BEGIN SELECT RAISE(ABORT,'Refund event requires the next order version'); END;
CREATE TRIGGER refund_event_no_update BEFORE UPDATE ON refund_events BEGIN SELECT RAISE(ABORT,'Refund event is immutable'); END;
CREATE TRIGGER refund_event_no_delete BEFORE DELETE ON refund_events BEGIN SELECT RAISE(ABORT,'Refund event is retained'); END;
CREATE TRIGGER refund_command_parent BEFORE INSERT ON refund_commands WHEN json_extract(NEW.receipt,'$.schemaVersion') IS NOT 1 OR json_extract(NEW.receipt,'$.requestKey') IS NOT NEW.request_key OR json_extract(NEW.receipt,'$.orderId') IS NOT NEW.order_id OR json_extract(NEW.receipt,'$.kind') IS NOT NEW.kind OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.actor_id AND status='active') OR (NEW.kind='inquiry' AND NOT EXISTS(SELECT 1 FROM purchase_orders WHERE id=NEW.order_id AND user_id=NEW.actor_id)) OR (NEW.kind!='inquiry' AND NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.actor_id AND role='admin')) BEGIN SELECT RAISE(ABORT,'Refund receipt must match its current actor and intent'); END;
CREATE TRIGGER refund_command_no_update BEFORE UPDATE ON refund_commands BEGIN SELECT RAISE(ABORT,'Refund command receipt is immutable'); END;
CREATE TRIGGER refund_command_no_delete BEFORE DELETE ON refund_commands BEGIN SELECT RAISE(ABORT,'Refund command receipt is retained'); END;
INSERT INTO schema_migrations VALUES(24,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
PRAGMA user_version=24;
"""


def key_hash(value):
    return hashlib.sha256((value or "").encode()).hexdigest()


def backfill(c):
    """Only observed in-flight cancellations; no invented reviews/consents."""
    for oid, key, amount, currency, mode, reason, prior_at in c.execute("SELECT id,payment_key,amount,currency,mode,refund_reason,updated_at FROM purchase_orders WHERE status='refunding' AND payment_key IS NOT NULL AND length(payment_key)>=6").fetchall():
        c.execute(
            "INSERT INTO refund_legacy_attempts VALUES(?,?,?,?,?,?,?,?)",
            (oid, key_hash(key), amount, currency, mode, reason, prior_at, now_iso()),
        )
