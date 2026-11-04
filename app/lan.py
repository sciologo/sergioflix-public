"""Qué se considera «dentro de casa» para las excepciones de transporte.

`ipaddress.is_private` incluye rangos reservados que no son una red doméstica
(por ejemplo los de documentación), así que aquí se enumeran las redes reales.
"""
import ipaddress

TEST_CLIENT='testclient'
HOME_NETWORKS=tuple(ipaddress.ip_network(cidr) for cidr in
    ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','169.254.0.0/16','fc00::/7','fe80::/10'))


def lan_client(host):
    """Loopback y redes privadas de casa. Una dirección pública nunca entra por aquí."""
    if host==TEST_CLIENT:return True
    try:address=ipaddress.ip_address(host)
    except ValueError:return False
    if address.version==6 and address.ipv4_mapped:address=address.ipv4_mapped
    return address.is_loopback or any(address in network for network in HOME_NETWORKS
                                      if network.version==address.version)
