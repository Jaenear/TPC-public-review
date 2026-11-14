"""Trusted identity model; public issuance/consumption HTTP is API02 work."""
import hmac
import json
import time

from ..core import new_id
from ..identity_contract import IdentityError, IdentityKeyring, ID, KEY, PURPOSES, digest, require
from ..mail_contract import address, address_hash, canonical
from .identity_flow import IdentityFlow


class IdentityService(IdentityFlow):
    def __init__(self, app, keyring=None, clock=time.time):
        require(keyring is None or isinstance(keyring, IdentityKeyring))
        self.app, self.keyring, self.clock, self.mail = app, keyring, clock, None

    def _now(self):
        return int(self.clock())

    def _keys(self):
        require(self.keyring is not None, 'not_ready', 503)
        return self.keyring

    @staticmethod
    def _insert(c, table, values):
        names = list(values)
        c.execute('INSERT INTO '+table+'('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',
                  tuple(values[n] for n in names))

    def _user(self, c, identifier):
        require(isinstance(identifier, str) and ID.fullmatch(identifier))
        row = c.execute('SELECT * FROM users WHERE id=?', (identifier,)).fetchone()
        require(row is not None and row['status'] == 'active', 'context_changed', 409)
        return dict(row)

    def _actor(self, c, actor, admin=False):
        require(isinstance(actor, dict) and isinstance(actor.get('id'), str), 'unauthorized', 401)
        row = c.execute('SELECT * FROM users WHERE id=?', (actor['id'],)).fetchone()
        require(row is not None and row['status'] == 'active', 'unauthorized', 401)
        require(not admin or row['role'] == 'admin', 'forbidden', 403)
        return dict(row)

    def _head(self, c, user_id, create=False):
        row = c.execute('SELECT * FROM identity_states WHERE user_id=?', (user_id,)).fetchone()
        if row:
            return dict(row)
        value = {'user_id': user_id, 'version': 0, 'credential_epoch': 0, 'verified_email_hash': None,
                 'verified_at': None, 'last_event_id': None, 'updated_at': self._now()}
        if create:
            self._insert(c, 'identity_states', value)
        return value

    def _challenge(self, c, identifier):
        row = c.execute('SELECT q.*,s.version,s.state,s.last_event_id,s.used_at,s.revoked_at,s.updated_at '
                        'FROM identity_challenges q JOIN identity_challenge_states s ON s.challenge_id=q.id WHERE q.id=?',
                        (identifier,)).fetchone()
        require(row is not None, 'invalid_token')
        return dict(row)

    def _summary_in(self, c, user):
        head = self._head(c, user['id'])
        pending = c.execute("SELECT count(*) FROM identity_challenges q JOIN identity_challenge_states s ON s.challenge_id=q.id "
                            "WHERE q.user_id=? AND q.purpose='change_email' AND q.source_email=? AND q.credential_epoch=? "
                            "AND s.state='pending' AND q.expires_at>?",
                            (user['id'], address(user['email']), head['credential_epoch'], self._now())).fetchone()[0]
        return {'identityVersion': head['version'], 'profileVersion': user['version'],
                'emailVerified': head['verified_email_hash'] == address_hash(user['email']),
                'hasPendingEmailChange': bool(pending)}

    def _challenge_view(self, row):
        return {'id': row['id'], 'purpose': row['purpose'], 'version': row['version'], 'state': row['state'],
                'expiresAt': row['expires_at'], 'expired': row['state'] == 'pending' and row['expires_at'] <= self._now()}

    def contract(self, actor, admin=False):
        with self.app.db.connect() as c:
            user = self._actor(c, actor, admin)
            result = {'scope': {'kind': 'identity_foundation', 'readModelOnly': False,
                       'publicIdentityWriteAPIEnabled': True, 'realMailDeliveryAccepted': False},
                      'schemaVersion': 32, 'storageSchemaVersion': c.execute('PRAGMA user_version').fetchone()[0],
                      'configured': self.keyring is not None,
                      'keyMode': self.keyring.mode if self.keyring else 'unconfigured'}
            if admin:
                counts = {state: 0 for state in ('pending', 'consumed', 'revoked', 'expired')}
                counts.update(dict(c.execute('SELECT state,count(*) FROM identity_challenge_states GROUP BY state')))
                result.update({'counts': counts, 'eventCount': c.execute('SELECT count(*) FROM identity_events').fetchone()[0],
                               'commandCount': c.execute('SELECT count(*) FROM identity_commands').fetchone()[0]})
            else:
                result['identity'] = self._summary_in(c, user)
            return result

    def _event(self, c, user, command_id, kind, code, challenge=None, after_state=None,
               changes=None, before_email=None, after_email=None):
        head, now = self._head(c, user['id'], True), self._now()
        next_head = {**head, **(changes or {})}
        identifier, version = new_id('identityevent'), head['version']+1
        event = {'id': identifier, 'user_id': user['id'], 'version': version,
                 'previous_event_id': head['last_event_id'], 'command_id': command_id, 'kind': kind,
                 'challenge_id': challenge['id'] if challenge else None,
                 'challenge_version': challenge.get('version', 0)+1 if challenge else None,
                 'previous_challenge_event_id': challenge.get('last_event_id') if challenge else None,
                 'before_state': challenge.get('state') if challenge else None, 'after_state': after_state,
                 'credential_epoch': next_head['credential_epoch'], 'verified_email_hash': next_head['verified_email_hash'],
                 'verified_at': next_head['verified_at'], 'before_email_hash': address_hash(before_email or user['email']),
                 'after_email_hash': address_hash(after_email or user['email']), 'code': code, 'created_at': now}
        self._insert(c, 'identity_events', event)
        changed = c.execute('UPDATE identity_states SET version=?,credential_epoch=?,verified_email_hash=?,verified_at=?,last_event_id=?,updated_at=? WHERE user_id=? AND version=?',
                            (version, next_head['credential_epoch'], next_head['verified_email_hash'], next_head['verified_at'],
                             identifier, now, user['id'], head['version']))
        require(changed.rowcount == 1, 'conflict', 409)
        return event

    def _transition(self, c, user, row, command_id, state, kind, code, changes=None, before_email=None, after_email=None):
        event = self._event(c, user, command_id, kind, code, row, state, changes, before_email, after_email)
        changed = c.execute('UPDATE identity_challenge_states SET version=version+1,state=?,last_event_id=?,used_at=?,revoked_at=?,updated_at=? WHERE challenge_id=? AND version=?',
                            (state, event['id'], event['created_at'] if state == 'consumed' else None,
                             event['created_at'] if state in ('revoked', 'expired') else None, event['created_at'], row['id'], row['version']))
        require(changed.rowcount == 1, 'conflict', 409)
        return event

    def _revoke_pending(self, c, user, command_id, purpose=None):
        rows = c.execute("SELECT q.id FROM identity_challenges q JOIN identity_challenge_states s ON s.challenge_id=q.id "
                         "WHERE q.user_id=? AND s.state='pending' AND (? IS NULL OR q.purpose=?) ORDER BY q.created_at,q.id",
                         (user['id'], purpose, purpose)).fetchall()
        for identifier, in rows:
            row = self._challenge(c, identifier)
            expired = row['expires_at'] <= self._now()
            self._transition(c, user, row, command_id, 'expired' if expired else 'revoked',
                             'challenge_expired' if expired else 'challenge_revoked',
                             'expired' if expired else ('superseded' if purpose else 'security_change'))

    def _request_hash(self, user_id, operation, data, reference):
        value = {'userId': user_id, 'operation': operation, 'data': data}
        return digest(value) if operation == 'profile' else self._keys().fingerprint('command', value, reference)

    def _original(self, c, user, operation, data):
        require(type(data) is dict and isinstance(data.get('requestKey'), str) and KEY.fullmatch(data['requestKey']))
        row = c.execute('SELECT * FROM identity_commands WHERE user_id=? AND request_key=?', (user['id'], data['requestKey'])).fetchone()
        if row is None:
            return None
        require(row['operation'] == operation and hmac.compare_digest(
            row['request_hash'], self._request_hash(user['id'], operation, data, row['key_ref'])), 'conflict', 409)
        return self._reply(c, user, row, True)

    def _reply(self, c, user, command, replayed):
        receipt = json.loads(command['result'])
        result = {'replayed': replayed, 'receipt': receipt, 'current': self._summary_in(c, user)}
        if receipt.get('challengeId'):
            challenge = self._challenge(c, receipt['challengeId'])
            require(challenge['user_id'] == user['id'], 'context_changed', 409)
            result['challenge'] = self._challenge_view(challenge)
        return result

    def _finish(self, c, user, command_id, operation, data, event, extra=None):
        reference = None if operation == 'profile' else self._keys().active
        receipt = {'id': command_id, 'eventId': event['id'], 'kind': operation,
                   'identityVersion': self._head(c, user['id'])['version'], 'profileVersion': user['version'],
                   'createdAt': self._now(), **(extra or {})}
        command = {'id': command_id, 'user_id': user['id'], 'request_key': data['requestKey'], 'operation': operation,
                   'key_ref': reference, 'request_hash': self._request_hash(user['id'], operation, data, reference),
                   'event_id': event['id'], 'result': canonical(receipt), 'created_at': self._now()}
        self._insert(c, 'identity_commands', command)
        return self._reply(c, user, command, False)

    def _valid_pending(self, c, user, row):
        require(row['state'] == 'pending' and row['created_at'] <= self._now() < row['expires_at'], 'invalid_token')
        require(row['source_email'] == address(user['email']) and
                row['credential_epoch'] == self._head(c, user['id'])['credential_epoch'] and
                hmac.compare_digest(row['credential_stamp'], self._keys().credential_stamp(user, row['key_ref'])),
                'invalid_token')
        self._keys().check(self._keys().token(row), row)

    def _read_token(self, c, token, purpose, actor):
        require(purpose in PURPOSES, 'invalid_token')
        identifier, reference = self._keys().parse(token)
        row = self._challenge(c, identifier)
        require(row['purpose'] == purpose and row['key_ref'] == reference, 'invalid_token')
        self._keys().check(token, row)
        user = self._user(c, row['user_id'])
        if purpose != 'reset_password' or actor is not None:
            require(self._actor(c, actor)['id'] == user['id'], 'forbidden', 403)
        return user, row

    def _mail_for_challenge(self, c, user, row):
        require(self.mail is not None, 'not_ready', 503)
        result = self.mail.enqueue_in(c, user['id'], {'requestKey': 'identity_'+row['id'], 'templateId': row['purpose'],
            'templateVersion': 1, 'source': {'kind': 'identity_challenge', 'id': row['id'], 'version': 1},
            'expiresAt': row['expires_at']})
        self._insert(c, 'identity_mail_links', {'job_id': result['job']['id'], 'user_id': user['id'],
                     'challenge_id': row['id'], 'notice_id': None, 'created_at': self._now()})
        return result['job']['id']

    def _notice(self, c, user, event, target, role):
        require(self.mail is not None, 'not_ready', 503)
        identifier = new_id('identitynotice')
        self._insert(c, 'identity_notices', {'id': identifier, 'user_id': user['id'], 'event_id': event['id'],
            'target_role': role, 'recipient_address': address(target), 'recipient_hash': address_hash(target), 'created_at': self._now()})
        result = self.mail.enqueue_in(c, user['id'], {'requestKey': 'identity_'+identifier, 'templateId': 'account_notice',
            'templateVersion': 1, 'source': {'kind': 'identity_notice', 'id': identifier, 'version': 1},
            'expiresAt': self._now()+86400})
        self._insert(c, 'identity_mail_links', {'job_id': result['job']['id'], 'user_id': user['id'],
                     'challenge_id': None, 'notice_id': identifier, 'created_at': self._now()})
        return result['job']['id']
