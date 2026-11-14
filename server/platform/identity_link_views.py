"""Purpose-bound, non-consuming views for member identity screens."""
from ..identity_contract import IdentityError, PURPOSES, require
from ..mail_contract import address


class IdentityLinkViews:
    def __init__(self, identity):
        self.identity = identity

    def requests_in(self, c, user):
        model = self.identity
        head = model._head(c, user['id'])
        result = {}
        for purpose, name in (('verify_email', 'verifyEmail'), ('change_email', 'emailChange')):
            found = c.execute("SELECT q.id FROM identity_challenges q JOIN identity_challenge_states s "
                              "ON s.challenge_id=q.id WHERE q.user_id=? AND q.purpose=? AND "
                              "q.source_email=? AND q.credential_epoch=? AND s.state='pending' "
                              "ORDER BY q.created_at DESC,q.rowid DESC LIMIT 1",
                              (user['id'], purpose, address(user['email']), head['credential_epoch'])).fetchone()
            view = None
            if found:
                row = model._challenge(c, found[0])
                view = {'state': 'pending', 'expiresAt': row['expires_at'],
                        'expired': row['expires_at'] <= model._now()}
                if purpose == 'change_email':
                    view['candidateEmail'] = row['candidate_email']
            result[name] = view
        return result

    def inspect(self, token, purpose, actor):
        model = self.identity
        require(purpose in PURPOSES)
        model._keys()
        with model.app.db.connect() as c:
            c.execute('BEGIN')
            try:
                user, row = model._read_token(c, token, purpose, actor)
            except IdentityError as error:
                if error.code == 'context_changed':
                    raise IdentityError(400, 'invalid_token') from None
                raise
            current = model._now()
            if row['state'] == 'consumed':
                state = 'used'
            elif row['state'] == 'expired' or (row['state'] == 'pending' and row['expires_at'] <= current):
                state = 'expired'
            elif row['state'] == 'revoked':
                state = 'revoked'
            else:
                try:
                    model._valid_pending(c, user, row)
                    state = 'ready'
                except IdentityError as error:
                    if error.code != 'invalid_token':
                        raise
                    state = 'invalid'
            return {'link': {'purpose': purpose, 'state': state, 'expiresAt': row['expires_at']},
                    'serverTime': current}
