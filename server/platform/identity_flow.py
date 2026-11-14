"""Atomic trusted profile/challenge commands; credentials never enter receipts."""
import secrets
import sqlite3

from ..core import new_id, password_field, text_field
from ..identity_contract import PURPOSES, IdentityError, require, token_digest
from ..mail_contract import address, address_hash


class IdentityFlow:
    def save_profile(self, actor, data):
        require(type(data) is dict and set(data) == {'requestKey', 'userVersion', 'name'})
        require(type(data['userVersion']) is int and data['userVersion'] >= 1)
        name = text_field(data, 'name', 2, 50)
        require(all(ord(x) >= 32 and ord(x) != 127 for x in name))
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            user = self._actor(c, actor)
            original = self._original(c, user, 'profile', data)
            if original is not None:
                return original
            require(user['version'] == data['userVersion'], 'conflict', 409)
            changed = c.execute('UPDATE users SET name=?,version=version+1 WHERE id=? AND version=? AND status=?',
                                (name, user['id'], user['version'], 'active'))
            require(changed.rowcount == 1, 'conflict', 409)
            user = self._user(c, user['id'])
            command_id = new_id('identitycmd')
            event = self._event(c, user, command_id, 'profile_saved', 'profile_saved')
            return self._finish(c, user, command_id, 'profile', data, event)

    def issue(self, actor, data):
        require(type(data) is dict and data.get('purpose') in ('verify_email', 'change_email'))
        purpose = data['purpose']
        expected = {'requestKey', 'purpose', 'identityVersion'} | ({'candidateEmail', 'currentPassword'} if purpose == 'change_email' else set())
        require(set(data) == expected)
        proof = None
        with self.app.db.connect() as c:
            user = self._actor(c, actor)
            if purpose == 'change_email':
                from ..app import verify_password
                require(isinstance(data['currentPassword'], str) and 1 <= len(data['currentPassword']) <= 128)
                require(verify_password(data['currentPassword'], user['password_hash']), 'forbidden', 403)
                proof = user['password_hash']
        return self._issue(user['id'], purpose, data, actor, proof)

    def request_recovery(self, user_id, data):
        """Trusted lookup result only. Public non-enumerating HTTP belongs to API02."""
        require(type(data) is dict and set(data) == {'requestKey', 'identityVersion'})
        return self._issue(user_id, 'reset_password', data, None, None)

    def _issue(self, user_id, purpose, data, actor, proof):
        self._keys()
        require(type(data['identityVersion']) is int and data['identityVersion'] >= 0)
        candidate = address(data['candidateEmail']) if purpose == 'change_email' else None
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            return self._issue_in(c, user_id, purpose, data, actor, proof, candidate)

    def request_recovery_in(self, c, user_id, data):
        """Trusted admission worker joins its caller-owned transaction."""
        require(c.in_transaction and type(data) is dict and set(data) == {'requestKey', 'identityVersion'})
        self._keys()
        require(type(data['identityVersion']) is int and data['identityVersion'] >= 0)
        return self._issue_in(c, user_id, 'reset_password', data, None, None, None)

    def _issue_in(self, c, user_id, purpose, data, actor, proof, candidate):
        require(c.in_transaction and purpose in PURPOSES)
        operation = purpose+'.issue'
        user = self._actor(c, actor) if actor is not None else self._user(c, user_id)
        require(user['id'] == user_id, 'forbidden', 403)
        original = self._original(c, user, operation, data)
        if original is not None:
            return original
        head = self._head(c, user_id, True)
        require(head['version'] == data['identityVersion'], 'conflict', 409)
        if purpose == 'change_email':
            require(user['password_hash'] == proof, 'context_changed', 409)
            require(candidate != address(user['email']) and not c.execute('SELECT 1 FROM users WHERE email=?', (candidate,)).fetchone(), 'conflict', 409)
        self.app.limiter.check_in(c, ('identity-issue', user_id, purpose), 3, 900, current=self._now())
        self.app.limiter.check_in(c, ('identity-issue-all', user_id), 10, 3600, current=self._now())
        command_id = new_id('identitycmd')
        self._revoke_pending(c, user, command_id, purpose)
        head = self._head(c, user_id)
        row = {'id': new_id('ichallenge'), 'user_id': user_id, 'purpose': purpose,
               'source_email': address(user['email']), 'candidate_email': candidate,
               'credential_stamp': self._keys().credential_stamp(user, self.keyring.active),
               'credential_epoch': head['credential_epoch'], 'key_ref': self.keyring.active,
               'nonce': secrets.token_hex(16), 'created_at': self._now(), 'expires_at': self._now()+1800}
        row['token_hash'] = token_digest(self._keys().token(row))
        self._insert(c, 'identity_challenges', row)
        event = self._event(c, user, command_id, 'challenge_issued', 'issued', row, 'pending')
        self._insert(c, 'identity_challenge_states', {'challenge_id': row['id'], 'version': 1, 'state': 'pending',
            'last_event_id': event['id'], 'used_at': None, 'revoked_at': None, 'updated_at': self._now()})
        job_id = self._mail_for_challenge(c, user, row)
        return self._finish(c, user, command_id, operation, data, event, {'challengeId': row['id'], 'mailJobId': job_id})

    def consume(self, token, purpose, data, actor=None):
        require(purpose in PURPOSES and type(data) is dict)
        require(set(data) == ({'requestKey', 'newPassword'} if purpose == 'reset_password' else {'requestKey'}))
        self._keys()
        # Validate the token before the slow password KDF, then recheck inside the writer.
        with self.app.db.connect() as c:
            self._read_token(c, token, purpose, actor)
        new_hash = None
        if purpose == 'reset_password':
            from ..app import hash_password
            password = password_field({'password': data['newPassword']}, registering=True)
            new_hash = hash_password(password)
        operation = purpose+'.consume'
        # The credential is only an input to a keyed command fingerprint, never a plain digest.
        intent = {**data, 'tokenHash': token_digest(token)}
        with self.app.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            user, row = self._read_token(c, token, purpose, actor)
            original = self._original(c, user, operation, intent)
            if original is not None:
                return original
            self._valid_pending(c, user, row)
            command_id, old_email = new_id('identitycmd'), user['email']
            head = self._head(c, user['id'])
            changes = {}
            if purpose == 'verify_email':
                changes = {'verified_email_hash': address_hash(user['email']), 'verified_at': self._now()}
                kind, code = 'email_verified', 'verified'
            else:
                next_email = row['candidate_email'] if purpose == 'change_email' else user['email']
                next_hash = new_hash if purpose == 'reset_password' else user['password_hash']
                try:
                    changed = c.execute('UPDATE users SET email=?,password_hash=?,version=version+1 WHERE id=? AND version=? AND email=? AND password_hash=? AND status=?',
                                        (next_email, next_hash, user['id'], user['version'], user['email'], user['password_hash'], 'active'))
                except sqlite3.IntegrityError:
                    raise IdentityError(409, 'conflict') from None
                require(changed.rowcount == 1, 'conflict', 409)
                user = self._user(c, user['id'])
                changes = {'credential_epoch': head['credential_epoch']+1}
                if purpose == 'change_email':
                    changes.update(verified_email_hash=address_hash(next_email), verified_at=self._now())
                kind, code = ('email_changed', 'changed') if purpose == 'change_email' else ('password_reset', 'reset')
                c.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
            event = self._transition(c, user, row, command_id, 'consumed', kind, code, changes, old_email, user['email'])
            if purpose in ('change_email', 'reset_password'):
                self._revoke_pending(c, user, command_id)
            jobs = []
            if purpose == 'change_email':
                jobs.append(self._notice(c, user, event, old_email, 'previous'))
            jobs.append(self._notice(c, user, event, user['email'], 'current'))
            return self._finish(c, user, command_id, operation, intent, event,
                                {'challengeId': row['id'], 'noticeJobIds': jobs, 'sessionsRevoked': purpose != 'verify_email'})
