"""Source authority for identity mail; callers never supply a recipient or URL."""
from ..identity_contract import IdentityError
from ..mail_contract import AccountMailSource, MailError, TrustedMailRecipient, address, address_hash, digest


class IdentityMailSource(AccountMailSource):
    def __init__(self, identity):
        self.identity = identity

    @property
    def identity_ready(self):
        return self.identity.keyring is not None

    def _resolve(self, c, user, source, template):
        if source['kind'] == 'account':
            fingerprint = super().fingerprint_in(c, user, source, template)
            return (address(user['email']), fingerprint, None) if fingerprint else None
        try:
            # Mail's public member projection deliberately omits password_hash.
            # Credential context is read privately from the current database row.
            user = self.identity._user(c, user['id'])
            if source['kind'] == 'identity_challenge':
                row = self.identity._challenge(c, source['id'])
                if row['user_id'] != user['id'] or row['version'] != source['version'] or row['purpose'] != template.id or not template.identity:
                    return None
                self.identity._valid_pending(c, user, row)
                if row['purpose'] == 'change_email' and c.execute(
                        'SELECT 1 FROM users WHERE email=? AND id!=?', (row['candidate_email'], user['id'])).fetchone():
                    return None
                recipient = row['candidate_email'] if row['purpose'] == 'change_email' else row['source_email']
                fingerprint = digest({'source': source, 'purpose': template.id, 'target': recipient,
                    'tokenHash': row['token_hash'], 'credentialStamp': row['credential_stamp'],
                    'epoch': row['credential_epoch'], 'expiresAt': row['expires_at']})
                return recipient, fingerprint, row
            if source['kind'] == 'identity_notice' and source['version'] == 1 and template.id == 'account_notice':
                row = c.execute('SELECT n.*,e.kind,e.before_email_hash,e.after_email_hash FROM identity_notices n '
                                'JOIN identity_events e ON e.id=n.event_id AND e.user_id=n.user_id WHERE n.id=? AND n.user_id=?',
                                (source['id'], user['id'])).fetchone()
                if row is None:
                    return None
                row = dict(row)
                expected = row['before_email_hash'] if row['target_role'] == 'previous' else row['after_email_hash']
                if row['recipient_hash'] != expected or address_hash(row['recipient_address']) != expected or row['kind'] not in ('email_verified', 'email_changed', 'password_reset') or (row['target_role'] == 'previous' and row['kind'] != 'email_changed'):
                    return None
                return address(row['recipient_address']), digest({'source': source, 'notice': row}), None
            return None
        except IdentityError as error:
            if error.status == 503:
                raise MailError(503, 'source_unavailable') from None
            return None

    def fingerprint_in(self, c, user, source, template):
        value = self._resolve(c, user, source, template)
        return value[1] if value else None

    def recipient_in(self, c, user, source, template):
        value = self._resolve(c, user, source, template)
        if value is None:
            raise MailError(409, 'context_changed')
        return TrustedMailRecipient(value[0], value[1])

    def url_in(self, c, job, source, template, profile):
        if not template.identity:
            return profile.application_origin+'/#'+template.path
        user = self.identity._user(c, job['user_id'])
        value = self._resolve(c, user, source, template)
        if value is None or value[2] is None:
            raise MailError(409, 'context_changed')
        token = self.identity._keys().token(value[2])
        return profile.application_origin+'/#'+template.path+'?token='+token
