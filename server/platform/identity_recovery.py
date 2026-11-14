"""Durable, non-enumerating admission and bounded local recovery processing."""
import hashlib
import hmac
import json
import logging
import threading
import time
import uuid

from ..core import ApiError, new_id
from ..identity_contract import IdentityError, require
from ..mail_contract import address

ACKNOWLEDGEMENT = {
    'accepted': True,
    'message': '입력한 주소로 계정을 확인할 수 있으면 복구 안내를 준비합니다. 메일함을 확인해주세요.',
}
INTAKE_TTL = 900
PENDING_CAPACITY = 1000
RETENTION_SECONDS = 7 * 86400


class IdentityRecovery:
    def __init__(self, app, identity):
        self.app, self.identity = app, identity
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._closed = False

    def _digest(self, domain, value):
        encoded = json.dumps([domain, value], ensure_ascii=False, separators=(',', ':')).encode()
        return hmac.new(self.app.limiter.key_secret, b'TPC-RECOVERY-INTAKE-v1\0'+encoded, hashlib.sha256).hexdigest()

    @staticmethod
    def _request_key(value):
        require(isinstance(value, str) and len(value) in (32, 36))
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError):
            raise IdentityError(400, 'invalid_input') from None
        require(parsed.version == 4 and parsed.variant == uuid.RFC_4122 and value in (parsed.hex, str(parsed)))
        return parsed.hex

    def submit(self, data):
        """The only membership-dependent public work is one indexed lookup.

        Both outcomes insert the same admission shape. Eligibility, issuance,
        per-member quotas and mail creation run after admission in another worker.
        """
        self.identity._keys()
        require(type(data) is dict and set(data) == {'requestKey', 'email'})
        original_key = self._request_key(data['requestKey'])
        target = address(data['email'])
        self.app.limiter.check(('recovery-input', target), 10, 3600, busy_timeout_ms=2000)
        request_hash = self._digest('request-key', original_key)
        input_hash = self._digest('address', target)
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current = self.identity._now()
            existing = c.execute('SELECT input_hash FROM identity_recovery_intakes WHERE request_key_hash=?',
                                 (request_hash,)).fetchone()
            if existing:
                require(hmac.compare_digest(existing['input_hash'], input_hash), 'conflict', 409)
            else:
                pending = c.execute("SELECT count(*) FROM identity_recovery_intakes WHERE state='pending'").fetchone()[0]
                require(pending < PENDING_CAPACITY, 'not_ready', 503)
                # Do not filter by status or branch into token creation here.
                user = c.execute('SELECT id FROM users WHERE email=?', (target,)).fetchone()
                c.execute('INSERT INTO identity_recovery_intakes(id,request_key_hash,input_hash,user_id,version,state,created_at,expires_at) '
                          "VALUES(?,?,?,?,1,'pending',?,?)",
                          (new_id('recoveryintake'), request_hash, input_hash, user['id'] if user else None,
                           current, current+INTAKE_TTL))
        self.wake()
        return dict(ACKNOWLEDGEMENT)

    def _terminal(self, c, row, state, code, command_id=None):
        changed = c.execute('UPDATE identity_recovery_intakes SET version=version+1,state=?,code=?,command_id=?,finished_at=? '
                            "WHERE id=? AND version=? AND state='pending'",
                            (state, code, command_id, self.identity._now(), row['id'], row['version']))
        require(changed.rowcount == 1, 'conflict', 409)
        return {'id': row['id'], 'state': state, 'code': code}

    def process_one(self):
        """One short transaction joins issue, its mail/receipt and intake outcome."""
        self.identity._keys()
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            found = c.execute("SELECT * FROM identity_recovery_intakes WHERE state='pending' ORDER BY created_at,id LIMIT 1").fetchone()
            if found is None:
                return None
            row = dict(found)
            if row['expires_at'] <= self.identity._now():
                return self._terminal(c, row, 'expired', 'expired')
            user = c.execute('SELECT * FROM users WHERE id=?', (row['user_id'],)).fetchone()
            eligible = user is not None and user['status'] == 'active'
            if eligible:
                eligible = hmac.compare_digest(self._digest('address', address(user['email'])), row['input_hash'])
            if not eligible:
                return self._terminal(c, row, 'ignored', 'not_eligible')
            # A quota/context denial must not retain a newly created head or
            # partially revoke an older challenge. The terminal intake remains.
            c.execute('SAVEPOINT recovery_issue')
            try:
                result = self.identity.request_recovery_in(c, user['id'], {
                    'requestKey': 'recovery_'+row['id'],
                    'identityVersion': self.identity._head(c, user['id'])['version'],
                })
            except ApiError as error:
                c.execute('ROLLBACK TO recovery_issue')
                c.execute('RELEASE recovery_issue')
                if error.status == 429:
                    return self._terminal(c, row, 'ignored', 'quota')
                if isinstance(error, IdentityError) and error.status == 409:
                    return self._terminal(c, row, 'ignored', 'not_eligible')
                raise
            c.execute('RELEASE recovery_issue')
            return self._terminal(c, row, 'processed', 'issued', result['receipt']['id'])

    def prune(self):
        """Trusted maintenance only; no public or GET path invokes retention."""
        with self.app.db.connect() as c:
            changed = c.execute("DELETE FROM identity_recovery_intakes WHERE state!='pending' AND finished_at<?",
                                (self.identity._now()-RETENTION_SECONDS,))
            return changed.rowcount

    def start(self):
        if self.identity.keyring is None:
            return False
        with self._lock:
            if self._closed:
                return False
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop.clear()
            self._wake.clear()
            self._thread = threading.Thread(target=self._run, name='tpc-recovery-intake', daemon=True)
            self._thread.start()
        return True

    def wake(self):
        if self.start():
            self._wake.set()

    def stop(self):
        with self._lock:
            thread = self._thread
            self._stop.set()
            self._wake.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=12)
            if thread.is_alive():
                raise RuntimeError('Recovery admission worker did not stop')
        with self._lock:
            if self._thread is thread:
                self._thread = None

    def close(self):
        # A late handler may durably admit work, but cannot revive a worker
        # after its HTTP server has shut down. A fresh server resumes pending.
        with self._lock:
            self._closed = True
        self.stop()

    def _run(self):
        next_cleanup, next_warning = 0.0, 0.0
        while not self._stop.is_set():
            self._wake.wait(0.5)
            self._wake.clear()
            if self._stop.is_set():
                break
            if self.identity.keyring is None:
                continue
            try:
                if time.monotonic() >= next_cleanup:
                    self.prune()
                    next_cleanup = time.monotonic()+60
                for _ in range(5):
                    if self._stop.is_set() or self.process_one() is None:
                        break
            except Exception:
                # No exception repr, request data, addresses, tokens or URL.
                if time.monotonic() >= next_warning:
                    logging.getLogger('tpc.recovery').warning('Recovery admission processing deferred')
                    next_warning = time.monotonic()+5
