"""Entrada de un solo uso al panel local; la clave maestra nunca llega al navegador."""
import hashlib
import secrets

from app.providers.stremio import ProviderError


def digest(value): return hashlib.sha256(value.encode()).hexdigest()


class AdminSession:
    def __init__(self,store): self.store=store

    def issue(self):
        token=secrets.token_urlsafe(32)
        with self.store.connect() as conn:
            conn.execute('DELETE FROM admin_ticket WHERE expires_at<now()')
            conn.execute("INSERT INTO admin_ticket VALUES (%s,'login',now()+interval '2 minutes')",(digest(token),))
        return token

    def exchange(self,token):
        key=secrets.token_urlsafe(36)
        with self.store.connect() as conn:
            row=conn.execute("DELETE FROM admin_ticket WHERE token_hash=%s AND purpose='login' AND expires_at>now() RETURNING token_hash",(digest(token),)).fetchone()
            if not row: raise ProviderError('login_expired','Vuelve a abrir el panel con tu acceso.',401)
            conn.execute("INSERT INTO admin_ticket VALUES (%s,'session',now()+interval '12 hours')",(digest(key),))
        self.store.record('admin-local','admin.login')
        return {'api_key':key}

    def valid(self,key):
        with self.store.connect() as conn:
            return bool(conn.execute("SELECT 1 FROM admin_ticket WHERE token_hash=%s AND purpose='session' AND expires_at>now()",(digest(key),)).fetchone())
