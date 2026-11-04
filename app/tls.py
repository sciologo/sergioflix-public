"""Certificado privado del hogar. La app confía en su certificado público empaquetado."""
import ipaddress
from datetime import datetime,timedelta,timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.settings import ROOT


def generate(host_ip):
    directory=ROOT / 'private'
    directory.mkdir(exist_ok=True)
    cert_path,key_path=directory/'server.crt',directory/'server.key'
    if cert_path.exists() or key_path.exists():
        if not cert_path.exists() or not key_path.exists(): raise RuntimeError('Falta parte del certificado local.')
        return cert_path,key_path
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Videoteca en casa')])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
          .not_valid_after(now+timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
          .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1')),x509.IPAddress(ipaddress.ip_address(host_ip))]),critical=False)
          .sign(key,hashes.SHA256()))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path,key_path

