"""Durable trusted mail primitives. No automatic sender or member mail HTTP API.

An external adapter runs outside SQLite transactions. Its accepted response is
not delivery proof. Unknown submission outcomes never enter automatic retries.
"""
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timezone

from ..core import new_id
from ..mail_contract import (
    AccountMailSource, CODES, DeliveryEvidence, DefinitelyNotSubmitted, ID, KEY,
    MailClaim, MailError, MailProfile, SubmissionResult, TEMPLATES, TrustedMailRecipient,
    UnavailableMailAdapter, address, address_hash, canonical, checked_source,
    digest, render, require, template_for,
)

STATES = ('queued', 'leased', 'sending', 'submitted', 'delivered', 'retry_wait',
          'failed', 'rejected', 'unknown', 'suppressed', 'cancelled', 'expired')


def insert(c, table, values):
    names = list(values)
    c.execute('INSERT INTO '+table+'('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')', tuple(values[n] for n in names))


def token_hash(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def timestamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


class MailService:
    def __init__(self, app, adapter=None, profile=None, source=None, clock=time.time, max_attempts=3):
        require(profile is None or isinstance(profile, MailProfile))
        require(type(max_attempts) is int and 1 <= max_attempts <= 5)
        self.app, self.adapter, self.profile = app, adapter or UnavailableMailAdapter(), profile
        self.source, self.clock, self.max_attempts = source or AccountMailSource(), clock, max_attempts

    def _now(self):
        return int(self.clock())

    def _admin(self, c, actor):
        if not actor:
            raise MailError(401, 'member_changed')
        user = c.execute('SELECT id,role,status FROM users WHERE id=?', (actor.get('id'),)).fetchone()
        if not user or user['status'] != 'active':
            raise MailError(401, 'member_changed')
        if user['role'] != 'admin':
            raise MailError(403, 'member_changed')
        return user

    def _job(self, c, identifier):
        require(isinstance(identifier, str) and ID.fullmatch(identifier))
        row = c.execute('SELECT j.*,s.version,s.state,s.current_attempt_id,s.last_event_id,s.ready_at,s.lease_hash,s.lease_until,s.sealed_profile_hash,s.sealed_content_hash,s.last_code,s.updated_at FROM mail_jobs j JOIN mail_job_states s ON s.job_id=j.id WHERE j.id=?', (identifier,)).fetchone()
        if row is None:
            raise MailError(404, 'not_found')
        return dict(row)

    def _view(self, c, job):
        return {'id': job['id'], 'state': job['state'], 'version': job['version'],
                'templateId': job['template_id'], 'templateVersion': job['template_version'],
                'createdAt': timestamp(job['created_at']), 'expiresAt': timestamp(job['expires_at']),
                'updatedAt': timestamp(job['updated_at']), 'lastCode': job['last_code'],
                'attemptCount': c.execute('SELECT count(*) FROM delivery_attempts WHERE job_id=?', (job['id'],)).fetchone()[0],
                'leaseExpired': job['state'] in ('leased', 'sending') and job['lease_until'] <= self._now()}

    def contract(self, actor):
        with self.app.db.connect() as c:
            self._admin(c, actor)
            counts = {state: 0 for state in STATES}
            counts.update({r[0]: r[1] for r in c.execute('SELECT state,count(*) FROM mail_job_states GROUP BY state')})
            return {'scope': {'kind': 'mail_foundation', 'readModelOnly': True,
                    'publicDispatchAPIEnabled': False, 'feedbackHTTPEnabled': False,
                    'identityAPILinked': bool(getattr(getattr(self.app, 'platform', None), 'identity_commands_api', None)), 'realDeliveryAccepted': False,
                    'sitePreferenceIsEmailConsent': False},
                'schemaVersion': 31, 'storageSchemaVersion': c.execute('PRAGMA user_version').fetchone()[0],
                'configured': self.profile is not None and self.adapter.ready is True and callable(getattr(self.adapter, 'submit', None)) and getattr(self.adapter, 'mode', None) == self.profile.mode,
                'transportMode': self.profile.mode if self.profile else 'unconfigured',
                'adapter': {'supportsIdempotency': getattr(self.adapter, 'supports_idempotency', False) is True,
                            'supportsQuery': getattr(self.adapter, 'supports_query', False) is True},
                'identitySourceReady': getattr(self.source, 'identity_ready', False) is True,
                'templates': [{'id': t.id, 'version': t.version, 'title': t.title,
                               'hash': t.fingerprint, 'identitySourceRequired': t.identity}
                              for t in TEMPLATES.values()],
                'counts': counts, 'attemptModes': {mode: c.execute('SELECT count(*) FROM delivery_attempts WHERE transport_mode=?', (mode,)).fetchone()[0] for mode in ('synthetic', 'external')},
                'blockedAddressCount': c.execute('SELECT count(*) FROM suppression_list WHERE blocked=1').fetchone()[0],
                'limitations': ['제공자 접수는 실제 전달 완료가 아닙니다.', '기본 메일 전달과 인증/복구 업무 연결은 미설정입니다.', '실제 발신 도메인·수신/반송 인수는 DEC-13에서 별도로 확인합니다.']}

    def view_admin(self, actor, identifier):
        with self.app.db.connect() as c:
            self._admin(c, actor)
            job = self._job(c, identifier)
            attempts = [dict(r) for r in c.execute('SELECT a.id,a.ordinal,r.outcome,r.code,a.created_at FROM delivery_attempts a LEFT JOIN delivery_attempt_results r ON r.attempt_id=a.id WHERE a.job_id=? ORDER BY a.ordinal', (identifier,))]
            events = [dict(r) for r in c.execute('SELECT version,kind,code,before_state,after_state,created_at FROM mail_events WHERE job_id=? ORDER BY version', (identifier,))]
            return {'job': self._view(c, job), 'attempts': attempts, 'events': events}

    def enqueue(self, user_id, data):
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            return self.enqueue_in(c, user_id, data)

    def enqueue_in(self, c, user_id, data):
        """Trusted business hook; caller owns its encompassing write transaction."""
        require(c.in_transaction and isinstance(user_id, str) and ID.fullmatch(user_id))
        require(type(data) is dict and set(data) == {'requestKey', 'templateId', 'templateVersion', 'source', 'expiresAt'})
        require(isinstance(data['requestKey'], str) and KEY.fullmatch(data['requestKey']))
        template, source = template_for(data['templateId'], data['templateVersion']), checked_source(data['source'])
        require(type(data['expiresAt']) is int and data['expiresAt'] >= 1)
        request_hash = digest({k: v for k, v in data.items() if k != 'requestKey'})
        previous = c.execute('SELECT id,request_hash FROM mail_jobs WHERE user_id=? AND request_key=?', (user_id, data['requestKey'])).fetchone()
        if previous:
            require(previous['request_hash'] == request_hash, 'conflict', 409)
            return self._enqueue_receipt(c, self._job(c, previous['id']), True)
        now = self._now()
        require(now < data['expiresAt'] <= now + (3600 if template.identity else 86400))
        user = c.execute('SELECT id,name,email,status,version FROM users WHERE id=?', (user_id,)).fetchone()
        require(user is not None and user['status'] == 'active', 'member_changed', 409)
        # Rendering validation is independent of whether transport is configured.
        require(2 <= len(user['name']) <= 50 and not any(ord(x) < 32 or ord(x) == 127 for x in user['name']))
        source_hash = self._source_hash(c, user, source, template)
        require(isinstance(source_hash, str) and len(source_hash) == 64, 'context_changed', 409)
        recipient = self._recipient(c, user, source, template, source_hash)
        identifier, event_id = new_id('mail'), new_id('mailevent')
        insert(c, 'mail_jobs', {'id': identifier, 'user_id': user_id, 'request_key': data['requestKey'],
            'request_hash': request_hash, 'template_id': template.id, 'template_version': template.version,
            'template_hash': template.fingerprint, 'recipient_address': recipient, 'recipient_hash': address_hash(recipient),
            'display_name': user['name'], 'source_ref': canonical(source), 'source_hash': source_hash,
            'expires_at': data['expiresAt'], 'created_at': now})
        insert(c, 'mail_job_states', {'job_id': identifier, 'version': 1, 'state': 'queued',
            'last_event_id': event_id, 'ready_at': now, 'last_code': 'queued', 'updated_at': now})
        insert(c, 'mail_events', {'id': event_id, 'job_id': identifier, 'version': 1,
            'previous_event_id': None, 'before_state': None, 'after_state': 'queued',
            'kind': 'queued', 'code': 'queued', 'attempt_id': None, 'created_at': now})
        return self._enqueue_receipt(c, self._job(c, identifier), False)

    def _enqueue_receipt(self, c, job, replayed):
        return {'job': self._view(c, job), 'replayed': replayed,
                'receipt': {'jobId': job['id'], 'createdVersion': 1, 'stateAtCreation': 'queued', 'createdAt': timestamp(job['created_at'])}}

    def _source_hash(self, c, user, source, template):
        try:
            value = self.source.fingerprint_in(c, user, source, template)
            require(value is None or isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value), 'source_unavailable', 503)
            return value
        except MailError:
            raise
        except Exception:
            raise MailError(503, 'source_unavailable') from None

    def _recipient(self, c, user, source, template, source_hash):
        resolver = getattr(self.source, 'recipient_in', None)
        if not callable(resolver):
            return address(user['email'])
        try:
            value = resolver(c, user, source, template)
            require(isinstance(value, TrustedMailRecipient) and
                    secrets.compare_digest(value.source_hash, source_hash), 'source_unavailable', 503)
            return value.address
        except MailError:
            raise
        except Exception:
            raise MailError(503, 'source_unavailable') from None

    def _preflight(self, c, job):
        now = self._now()
        if job['expires_at'] <= now:
            return 'expired', None
        user = c.execute('SELECT id,name,email,status,version FROM users WHERE id=?', (job['user_id'],)).fetchone()
        if not user or user['status'] != 'active':
            return 'member_changed', None
        source = json.loads(job['source_ref'])
        if (source['kind'] == 'account' or not callable(getattr(self.source, 'recipient_in', None))) and address(user['email']) != job['recipient_address']:
            return 'recipient_changed', None
        if c.execute('SELECT 1 FROM suppression_list WHERE recipient_hash=? AND blocked=1', (job['recipient_hash'],)).fetchone():
            return 'suppressed', None
        try:
            template = template_for(job['template_id'], job['template_version'])
        except MailError:
            return 'template_changed', None
        if template.fingerprint != job['template_hash']:
            return 'template_changed', None
        source_hash = self._source_hash(c, user, source, template)
        if source_hash != job['source_hash']:
            return 'context_changed', None
        if self._recipient(c, user, source, template, source_hash) != job['recipient_address']:
            return 'recipient_changed', None
        require(self.profile is not None and self.adapter.ready is True and callable(getattr(self.adapter, 'submit', None)) and getattr(self.adapter, 'mode', None) == self.profile.mode, 'not_ready', 503)
        try:
            url = self.source.url_in(c, job, source, template, self.profile)
            envelope = render(template, self.profile, job['recipient_address'], job['display_name'], url)
        except Exception:
            raise MailError(503, 'source_unavailable') from None
        if job['sealed_profile_hash'] is not None and (job['sealed_profile_hash'] != self.profile.fingerprint or job['sealed_content_hash'] != envelope.fingerprint):
            return 'configuration_changed', None
        return None, envelope

    def _advance(self, c, job, state, kind, code, attempt_id=None, lease_hash=None, lease_until=None, ready_at=None, seal=None):
        require(state in STATES and code in CODES)
        event_id, now = new_id('mailevent'), self._now()
        current_attempt = attempt_id or job['current_attempt_id']
        profile_hash, content_hash = seal or (job['sealed_profile_hash'], job['sealed_content_hash'])
        changed = c.execute('UPDATE mail_job_states SET version=version+1,state=?,current_attempt_id=?,last_event_id=?,ready_at=?,lease_hash=?,lease_until=?,sealed_profile_hash=?,sealed_content_hash=?,last_code=?,updated_at=? WHERE job_id=? AND version=?',
            (state, current_attempt, event_id, ready_at if ready_at is not None else job['ready_at'], lease_hash, lease_until,
             profile_hash, content_hash, code, now, job['id'], job['version']))
        require(changed.rowcount == 1, 'conflict', 409)
        insert(c, 'mail_events', {'id': event_id, 'job_id': job['id'], 'version': job['version'] + 1,
            'previous_event_id': job['last_event_id'], 'before_state': job['state'], 'after_state': state,
            'kind': kind, 'code': code, 'attempt_id': current_attempt, 'created_at': now})
        return self._job(c, job['id'])

    def _save_result(self, c, job, attempt_id, outcome, code, provider_reference=None, result_hash=None):
        insert(c, 'delivery_attempt_results', {'attempt_id': attempt_id, 'job_id': job['id'],
            'outcome': outcome, 'code': code, 'provider_reference': provider_reference,
            'result_hash': result_hash or digest({'outcome': outcome, 'code': code, 'provider_reference': provider_reference}),
            'result_version': job['version'], 'created_at': self._now()})

    def _stop(self, c, job, code, attempt_id=None):
        state = code if code in ('expired', 'suppressed') else 'cancelled'
        result = self._advance(c, job, state, state, code, attempt_id=attempt_id)
        if attempt_id:
            self._save_result(c, result, attempt_id, 'not_sent', code)
        return result

    def claim(self, identifier, worker_id, lease_seconds=30):
        require(isinstance(worker_id, str) and ID.fullmatch(worker_id))
        require(type(lease_seconds) is int and 5 <= lease_seconds <= 300)
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            job = self._job(c, identifier)
            if job['state'] not in ('queued', 'retry_wait') or job['ready_at'] > self._now():
                return None
            code, envelope = self._preflight(c, job)
            if code:
                self._stop(c, job, code)
                return None
            ordinal = c.execute('SELECT count(*)+1 FROM delivery_attempts WHERE job_id=?', (identifier,)).fetchone()[0]
            if ordinal > self.max_attempts:
                self._stop(c, job, 'attempt_limit')
                return None
            attempt_id, token = new_id('mailattempt'), secrets.token_urlsafe(32)
            now, lease_until = self._now(), self._now() + lease_seconds
            insert(c, 'delivery_attempts', {'id': attempt_id, 'job_id': identifier, 'ordinal': ordinal,
                'worker_id': worker_id, 'lease_hash': token_hash(token), 'lease_until': lease_until,
                'delivery_key': 'delivery_'+identifier, 'profile_hash': self.profile.fingerprint, 'transport_mode': self.profile.mode,
                'content_hash': envelope.fingerprint, 'created_at': now})
            self._advance(c, job, 'leased', 'claimed', 'claimed', attempt_id, token_hash(token), lease_until)
            return MailClaim(identifier, attempt_id, token)

    def _holding(self, c, claim, state):
        require(isinstance(claim, MailClaim), 'conflict', 409)
        job = self._job(c, claim.job_id)
        attempt = c.execute('SELECT * FROM delivery_attempts WHERE id=? AND job_id=?', (claim.attempt_id, claim.job_id)).fetchone()
        require(attempt is not None and secrets.compare_digest(attempt['lease_hash'], token_hash(claim.lease_token)), 'conflict', 409)
        require(job['state'] == state and job['current_attempt_id'] == claim.attempt_id and job['lease_until'] > self._now(), 'conflict', 409)
        return job, attempt

    def begin_submission(self, claim):
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            job, attempt = self._holding(c, claim, 'leased')
            code, envelope = self._preflight(c, job)
            if code is None and (attempt['profile_hash'] != self.profile.fingerprint or attempt['content_hash'] != envelope.fingerprint):
                code = 'configuration_changed'
            if code:
                self._stop(c, job, code, claim.attempt_id)
                return None
            self._advance(c, job, 'sending', 'submission_started', 'claimed', claim.attempt_id,
                          attempt['lease_hash'], attempt['lease_until'], seal=(attempt['profile_hash'], attempt['content_hash']))
            return envelope

    def finish_submission(self, claim, result):
        require(isinstance(claim, MailClaim) and isinstance(result, SubmissionResult))
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            attempt = c.execute('SELECT * FROM delivery_attempts WHERE id=? AND job_id=?', (claim.attempt_id, claim.job_id)).fetchone()
            require(attempt is not None and secrets.compare_digest(attempt['lease_hash'], token_hash(claim.lease_token)), 'conflict', 409)
            old = c.execute('SELECT * FROM delivery_attempt_results WHERE attempt_id=?', (claim.attempt_id,)).fetchone()
            if old:
                require(old['result_hash'] == result.fingerprint, 'conflict', 409)
                return self._view(c, self._job(c, claim.job_id))
            job, attempt = self._holding(c, claim, 'sending')
            code, next_at = result.code, job['ready_at']
            state = {'accepted': 'submitted', 'unknown': 'unknown', 'rejected': 'rejected'}.get(result.outcome)
            if result.outcome == 'not_submitted':
                next_at = self._now() + min(2 ** (attempt['ordinal'] - 1), 60)
                if next_at >= job['expires_at']:
                    state, code = 'expired', 'expired'
                elif attempt['ordinal'] >= self.max_attempts:
                    state, code = 'failed', 'attempt_limit'
                else:
                    state = 'retry_wait'
            current = self._advance(c, job, state, result.outcome, code, claim.attempt_id, ready_at=next_at)
            self._save_result(c, current, claim.attempt_id, result.outcome, result.code, result.provider_reference, result.fingerprint)
            return self._view(c, current)

    def deliver_one(self, identifier, worker_id, lease_seconds=30):
        """Invoked explicitly by a trusted caller; never starts a background loop."""
        claim = self.claim(identifier, worker_id, lease_seconds)
        if claim is None:
            return None
        envelope = self.begin_submission(claim)
        if envelope is None:
            return None
        try:
            result = self.adapter.submit(envelope, 'delivery_'+identifier)
            if not isinstance(result, SubmissionResult):
                result = SubmissionResult('unknown', 'provider_unknown')
        except DefinitelyNotSubmitted:
            result = SubmissionResult('not_submitted', 'definitely_not_submitted')
        except Exception:
            # A timeout/exception is not proof that no external submission happened.
            result = SubmissionResult('unknown', 'provider_unknown')
        return self.finish_submission(claim, result)

    def recover_expired(self, identifier, expected_version):
        require(type(expected_version) is int and expected_version >= 1)
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            job = self._job(c, identifier)
            require(job['version'] == expected_version and job['state'] in ('leased', 'sending') and job['lease_until'] <= self._now(), 'conflict', 409)
            if job['state'] == 'sending':
                current = self._advance(c, job, 'unknown', 'unknown', 'lease_expired')
                self._save_result(c, current, job['current_attempt_id'], 'unknown', 'lease_expired')
            else:
                state = 'expired' if job['expires_at'] <= self._now() else 'queued'
                current = self._advance(c, job, state, 'lease_recovered', 'lease_expired', ready_at=self._now())
                self._save_result(c, current, job['current_attempt_id'], 'not_sent', 'lease_expired')
            return self._view(c, current)

    def cancel_admin(self, actor, identifier, expected_version):
        require(type(expected_version) is int and expected_version >= 1)
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self._admin(c, actor)
            job = self._job(c, identifier)
            require(job['version'] == expected_version and job['state'] in ('queued', 'retry_wait', 'leased'), 'conflict', 409)
            current = self._stop(c, job, 'cancelled', job['current_attempt_id'] if job['state'] == 'leased' else None)
            return self._view(c, current)

    def set_suppression(self, actor, target_user_id, blocked, expected_version, request_key, code):
        require(isinstance(target_user_id, str) and ID.fullmatch(target_user_id))
        require(type(blocked) is bool and type(expected_version) is int and expected_version >= 0)
        require(isinstance(request_key, str) and KEY.fullmatch(request_key))
        require(code in (('hard_bounce', 'complaint', 'operator_hold') if blocked else ('operator_release',)))
        request_hash = digest({'targetUserId': target_user_id, 'blocked': blocked, 'expectedVersion': expected_version, 'code': code})
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            user = self._admin(c, actor)
            previous = c.execute('SELECT * FROM suppression_events WHERE actor_id=? AND request_key=?', (user['id'], request_key)).fetchone()
            if previous:
                require(previous['request_hash'] == request_hash, 'conflict', 409)
                head = c.execute('SELECT version,blocked FROM suppression_list WHERE recipient_hash=?', (previous['recipient_hash'],)).fetchone()
                return {'replayed': True, 'receipt': {'id': previous['id'], 'version': previous['version'], 'blocked': bool(previous['blocked'])}, 'current': {'version': head['version'], 'blocked': bool(head['blocked'])}}
            target = c.execute('SELECT email FROM users WHERE id=?', (target_user_id,)).fetchone()
            require(target is not None, 'not_found', 404)
            recipient = address_hash(target['email'])
            head = c.execute('SELECT * FROM suppression_list WHERE recipient_hash=?', (recipient,)).fetchone()
            require(expected_version == (head['version'] if head else 0) and (head is not None or blocked), 'conflict', 409)
            version, event_id, now = expected_version + 1, new_id('mailsuppression'), self._now()
            if head:
                c.execute('UPDATE suppression_list SET version=?,blocked=?,current_event_id=?,updated_at=? WHERE recipient_hash=? AND version=?', (version, int(blocked), event_id, now, recipient, expected_version))
            else:
                insert(c, 'suppression_list', {'recipient_hash': recipient, 'version': version, 'blocked': 1, 'current_event_id': event_id, 'updated_at': now})
            insert(c, 'suppression_events', {'id': event_id, 'recipient_hash': recipient, 'version': version,
                'previous_event_id': head['current_event_id'] if head else None, 'actor_id': user['id'], 'target_user_id': target_user_id,
                'request_key': request_key, 'request_hash': request_hash, 'blocked': int(blocked), 'code': code, 'created_at': now})
            return {'replayed': False, 'receipt': {'id': event_id, 'version': version, 'blocked': blocked}, 'current': {'version': version, 'blocked': blocked}}

    def record_verified_delivery(self, identifier, evidence, expected_version):
        """Trusted preverified adapter evidence; callback verification is API02."""
        require(isinstance(evidence, DeliveryEvidence) and type(expected_version) is int and expected_version >= 1)
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            job = self._job(c, identifier)
            previous = c.execute('SELECT * FROM mail_delivery_facts WHERE profile_hash=? AND event_key=?', (evidence.profile_hash, evidence.event_key)).fetchone()
            if previous:
                require(previous['job_id'] == identifier and previous['evidence_hash'] == evidence.fingerprint, 'conflict', 409)
                return self._view(c, job)
            attempt = c.execute('SELECT * FROM delivery_attempts WHERE id=? AND job_id=?', (evidence.attempt_id, identifier)).fetchone()
            require(job['version'] == expected_version and job['state'] in ('submitted', 'unknown') and attempt is not None and job['current_attempt_id'] == evidence.attempt_id, 'conflict', 409)
            require(attempt['profile_hash'] == evidence.profile_hash and attempt['delivery_key'] == evidence.delivery_key, 'conflict', 409)
            result = c.execute('SELECT * FROM delivery_attempt_results WHERE attempt_id=?', (attempt['id'],)).fetchone()
            require(result is not None and (result['provider_reference'] is None or result['provider_reference'] == evidence.provider_reference), 'conflict', 409)
            state, kind = ('delivered', 'delivery_confirmed') if evidence.outcome == 'delivered' else ('failed', 'bounce_recorded')
            current = self._advance(c, job, state, kind, evidence.outcome)
            insert(c, 'mail_delivery_facts', {'id': new_id('maildelivery'), 'job_id': identifier,
                'attempt_id': evidence.attempt_id, 'profile_hash': evidence.profile_hash,
                'event_key': evidence.event_key, 'delivery_key': evidence.delivery_key,
                'provider_reference': evidence.provider_reference, 'outcome': evidence.outcome,
                'evidence_hash': evidence.fingerprint, 'result_version': current['version'], 'created_at': self._now()})
            return self._view(c, current)
