"""Emparejamiento de un solo uso y claves revocables por dispositivo."""
import hashlib
import secrets

from app.providers.stremio import ProviderError


def digest(value): return hashlib.sha256(value.encode()).hexdigest()


class Devices:
    def __init__(self,store): self.store=store

    def identify(self,key):
        with self.store.connect() as conn:
            row=conn.execute("SELECT id FROM paired_device WHERE key_hash=%s AND NOT revoked",(digest(key),)).fetchone()
        return "device:"+row[0] if row else None

    def pairing(self):
        code=secrets.token_urlsafe(24)
        with self.store.connect() as conn:
            conn.execute("DELETE FROM pairing_code WHERE expires_at<now() OR used")
            conn.execute("INSERT INTO pairing_code(code_hash) VALUES (%s)",(digest(code),))
        return code

    def short_pairing(self):
        """Ocho dígitos escribibles con un mando. Dura cinco minutos y sirve una vez."""
        code=''.join(secrets.choice('0123456789') for _ in range(8))
        with self.store.connect() as conn:
            conn.execute("DELETE FROM pairing_code WHERE expires_at<now() OR used")
            conn.execute("INSERT INTO pairing_code(code_hash,expires_at) VALUES (%s, now() + interval '5 minutes')",(digest(code),))
        return code

    def exchange(self,code):
        key,device=secrets.token_urlsafe(36),secrets.token_hex(8)
        with self.store.connect() as conn:
            row=conn.execute("UPDATE pairing_code SET used=true WHERE code_hash=%s AND NOT used AND expires_at>now() RETURNING code_hash",(digest(code),)).fetchone()
            if not row: raise ProviderError("invalid_pairing","El enlace de conexión venció o ya fue utilizado.",401)
            conn.execute("INSERT INTO paired_device(id,key_hash) VALUES (%s,%s)",(device,digest(key)))
            conn.execute("INSERT INTO audit_event(actor,action) VALUES (%s,'device.paired')",("device:"+device,))
        return {"api_key":key,"device_id":device}

