"""Découverte réseau (postes LAN, partages SMB, domaines, LDAP).

Windows : groupe de travail (net view) + annuaire SMB (WNetEnumResource sur
« \\domaine\\IPC$ ») + LDAP anonyme (ldap3, si DC déclaré).
Linux/Docker : smbclient -L //domaine + LDAP anonyme (aucun binaire Windows)."""
import os
import re
import time
import logging
import threading

from . import state

logger = logging.getLogger(__name__)

_NET_SCAN_LOCK = threading.Lock()
_NET_SCAN = (0, (None, None, []))  # (timestamp, (postes, erreur, domaines))
_COMPUTER_SHARES_CACHE = {}
_COMPUTER_SHARES_TTL = 600
_PDCACHE = {}                       # clé -> (ts, valeur) ; clé : 'dom:<nom>'
_CURRENT_DOMAIN = None
_LDAP3 = None

# ── Découverte réseau multi-domaines ─────────────────────────────────────────
# Windows : groupe de travail (net view) + annuaire SMB (WNetEnumResource sur
# « \\domaine\IPC$ ») + LDAP anonyme (ldap3, si DC déclaré).
# Linux/Docker : smbclient -L //domaine + LDAP anonyme (aucun binaire Windows).
_PWS = None                  # (win32api, win32con) ou ()
_PWS_TRIED = False


def _pws():
    """win32api/win32con (Windows). () si indisponible."""
    global _PWS, _PWS_TRIED
    if _PWS_TRIED:
        return _PWS
    _PWS_TRIED = True
    try:
        import win32api
        import win32con
        _PWS = (win32api, win32con)
    except Exception:
        _PWS = ()
    return _PWS


def network_roots():
    """Connexions réseau Windows (`net use`) : lettres mappées et partages UNC rappelés."""
    import platform
    if platform.system() != 'Windows':
        return []
    try:
        import subprocess
        import string
        out = subprocess.run(['net', 'use'], capture_output=True, text=True, errors='ignore',
                             timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        roots, seen = [], set()
        letters = {c + ':' for c in string.ascii_uppercase}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[-1] in ('DENIED', 'UNKNOWN', 'OK'):
                continue
            if parts[0] in letters:
                roots.append(f'{parts[0][0]}:\\')
            elif parts[0].startswith('\\\\'):
                roots.append(parts[0])
            elif any(x.startswith('\\\\') for x in parts):
                roots.append(next(x for x in parts if x.startswith('\\\\')))
        return [r for r in dict.fromkeys(roots) if not seen.add(r)]
    except Exception:
        return []


def list_dirs(abs_path, attempts=2, delay=1.5):
    """Liste les sous-dossiers avec retry (le 1er accès à une racine réseau
    peut lever WinError carte à puce/certificat transitoire)."""
    err = None
    for i in range(attempts):
        try:
            names = []
            for e in os.scandir(abs_path):
                try:
                    if e.is_dir(follow_symlinks=False):
                        names.append(e.name)
                except OSError:
                    pass
            return sorted(names, key=str.lower), None
        except PermissionError:
            return [], 'Permission refusée'
        except OSError as e:
            err = str(e)
            if i < attempts - 1:
                time.sleep(delay)
    return [], err


def own_net_name():
    import socket
    try:
        return socket.gethostname()
    except OSError:
        return ''


def _smb_server_shares(server, timeout=10):
    """Partages publics d'un serveur SMB. None si le serveur est inatteignable,
    [] s'il est accessible mais sans partage public.

    Windows : WNetAddConnection2('\\\\server\\\\IPC$') + WNetEnumResource
    (mêmes appels que « net view »). Linux/Docker : smbclient -L."""
    pws = _pws()
    if pws:
        win32api, win32con = pws
        try:
            win32api.WNetAddConnection2(
                server, None, None,
                win32con.WP_CONNECTED_SHARENAME | win32con.WP_USE_AUTOCACHE)
            names, seen, cont = [], set(), 0
            try:
                while True:
                    res, cont = win32api.WNetEnumResource(
                        win32con.RESOURCE_REMOTE, win32con.RESOURCEDIR_CONTAINER, cont)
                    for r in res or []:
                        nm = r.get('Name')
                        if not nm:
                            nm = (r.get('Names') or [None])[0]
                        if nm and nm.upper() not in ('IPC', 'IPC$') and not nm.endswith('$') and nm not in seen:
                            seen.add(nm)
                            names.append(nm)
                    if not cont:
                        break
            finally:
                try:
                    win32api.WNetCancelConnection2(server, win32con.WP_CONNECTED_SHARENAME, False)
                except Exception:
                    pass
            return sorted(names, key=str.lower)
        except Exception:
            return None
    try:
        import subprocess
        out = subprocess.run(['smbclient', '-L', '//' + server, '-N',
                              '--queryuser=guest,'], capture_output=True,
                             timeout=timeout, text=True, errors='replace').stdout
        lines = out.splitlines()
        hdr = -1
        for i, line in enumerate(lines):
            if 'Sharename' in line:
                hdr = i
                break
        if hdr is not None:
            hdr = next((i for i in range(hdr + 1, len(lines))
                        if '----' in lines[i] and '*' not in lines[i]), hdr)
        names = []
        for line in lines[hdr + 1:]:
            line = line.strip()
            if not line or line.startswith('*'):
                continue
            name = line.split()[0]
            if name and name.upper() not in ('IPC', 'IPC$') and not name.endswith('$'):
                names.append(name)
        if names:
            return sorted(dict.fromkeys(names), key=str.lower)
    except Exception:
        pass
    return None


def _smb_domain_shares(domain):
    """Postes d'un domaine via l'annuaire SMB (« \\domaine\\IPC$ »).
    Mémoïsé 10 min. Retourne ([postes], err) ; err = None si lecture OK."""
    dom = domain.lower()
    r0 = _PDCACHE.get('dom:' + dom)
    if r0 and time.time() - r0[0] < _COMPUTER_SHARES_TTL:
        return r0[1]
    res = _smb_server_shares(domain)
    if res is None:
        out = ([], 'Domaine SMB inaccessible (port 445/139 ouvert ?)')
    else:
        out = (sorted(res, key=str.lower), None)
    _PDCACHE['dom:' + dom] = (time.time(), out)
    if len(_PDCACHE) > 500:
        _PDCACHE.clear()
        _PDCACHE['dom:' + dom] = (time.time(), out)
    return out


def _current_smb_domain():
    """Nom du domaine courant (Windows : NET_LOGON_INFO ; Linux : hostname FQDN)."""
    global _CURRENT_DOMAIN
    if _CURRENT_DOMAIN:
        return _CURRENT_DOMAIN
    d = ''
    pws = _pws()
    if pws and len(pws) == 2:
        try:
            info = pws[0].NetUserGetInfo(None, None, 1)
            d = (getattr(info, 'domain_name', '') or '').strip()
        except Exception:
            pass
        if not d:
            try:
                import subprocess
                r = subprocess.run(['net', 'config', 'workstation'],
                                   capture_output=True, timeout=5,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                out = r.stdout.decode('cp850', errors='replace')
                for line in out.splitlines():
                    line = line.strip().lstrip('\x00').strip()
                    if line.lower().startswith(('domain', 'domaine')):
                        d = line.split('=', 1)[-1].strip()
                        break
            except Exception:
                pass
    if not d:
        d = os.environ.get('CLEANFLICK_DOMAIN', '').strip()
    if not d:
        import socket
        try:
            fqdn = socket.getfqdn()
            if '.' in fqdn:
                d = fqdn.split('.', 1)[1]
        except OSError:
            pass
    _CURRENT_DOMAIN = d or 'lan'
    return _CURRENT_DOMAIN


def _ldap_available():
    global _LDAP3
    if _LDAP3 is None:
        try:
            import ldap3  # noqa: F401
            _LDAP3 = True
        except ImportError:
            _LDAP3 = False
    return _LDAP3


def _ldap_dc_host(dc):
    """Rétablit le nom d'hôte du DC depuis le nom NetBIOS du domaine.
    Le DC est déclaré dans l'environnement CLEANFLICK_LDAP_DC.
    Retourne None si le DC n'est pas déclaré LDAP."""
    dc = (dc or '').strip()
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9\-\.]*$', dc):
        return None
    host = os.environ.get('CLEANFLICK_LDAP_DC_HOST') or ''
    return host or None


def _ldap_domain(domain):
    """Postes + domaines du domaine courant via le DC LDAP (domaine courant
    seulement : un DC n'est pas accessible sans credentiaux sur un autre
    domaine). Mémoïsé 10 min. Retourne ([pcs], [domains]) ou ([], None)."""
    dk = 'dom:' + domain.lower()
    r0 = _PDCACHE.get(dk)
    if r0 and time.time() - r0[0] < _COMPUTER_SHARES_TTL:
        return r0[1]
    if not _ldap_available():
        return ([], [domain])
    import ldap3
    import ldap3.core.exceptions
    dc_host = _ldap_dc_host(domain)
    if not dc_host:
        out = ([], None)
    else:
        try:
            conn = ldap3.Server(
                dc_host, port=389, use_ssl=False, get_info=ldap3.ALL,
                referral_timeout=.5, connect_timeout=3)
            c = ldap3.Connection(conn, auto_bind=False, receipt_policy=ldap3.RECEIPT_ALL)
            c.bind()
            # La base 'cn=Computers' est toujours accessible pour les domaines Active
            # Directory standards. On tente aussi 'cn=Domains' pour le multi-domaine.
            base = 'cn=Computers,dc=' + domain.lower().replace('.', ',dc=')
            if not c.search(base, '(objectClass=computer)') or not c.entries:
                base = 'CN'
                if not c.search(base, '(objectClass=computer)'):
                    out = ([], None)
                    c.unbind()
                    return out
            pcs = sorted({e.name.value.split(',')[0] for e in c.entries if e.name and e.name.value},
                           key=str.lower)
            doms = [domain]
            c.unbind()
            out = (pcs, doms)
        except Exception:
            out = ([], None)
    _PDCACHE[dk] = (time.time(), out)
    if len(_PDCACHE) > 500:
        _PDCACHE.clear()
        _PDCACHE[dk] = (time.time(), out)
    return out


def net_scan_work():
    """Postes de la LAN : net view (groupe de travail) + domaine courant
    (annuaire SMB / LDAP) + UNC de la config + hôtes épinglés (env).
    Mémoïsé 30 min ; en cas d'échec total, nouvel essai dans ~1 min."""
    global _NET_SCAN
    t0 = time.time()
    own = own_net_name().lower()
    pcs, domains, errs = set(), [], []
    # 1. Groupe de travail (net view) — même source que l'explorateur Windows.
    try:
        import subprocess
        r = subprocess.run(['net', 'view'], capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        out = (r.stdout or b'').decode('cp850', errors='replace')
        for line in out.splitlines():
            raw = line.strip()
            if not raw.startswith('\\'):
                continue
            name = raw.lstrip('\\')
            if name and name.lower() != own:
                pcs.add(name)
    except Exception:
        pass
    # 2. Domaine courant : postes via annuaire SMB (WNetEnumResource sur
    #    « \\domaine\IPC$ ») ou LDAP anonyme si ldap3 est présent.
    dom = _current_smb_domain()
    if dom and dom.lower() != 'lan':
        try:
            r_pcs, r_err = _smb_domain_shares(dom)
        except Exception:
            r_pcs, r_err = [], None
        if r_pcs:
            pcs.update(r_pcs)
            domains.append(dom)
        else:
            p2, d2 = _ldap_domain(dom)
            pcs.update(p2 or [])
            domains.extend(d2 or [])
            if r_err:
                errs.append(r_err)
    # 3. Serveurs déjà utilisés par l'app (chemins UNC de la config).
    for key in ('input_path', 'movie_output_path', 'tv_output_path'):
        v = state.config.get(key) or ''
        if v.startswith('\\\\'):
            host = v[2:].split('\\', 1)[0]
            if host and host.lower() != own:
                pcs.add(host)
    # 4. Hôtes épinglés (env CLEANFLICK_NET_HOSTS, séparés par virgule).
    for h in (os.environ.get('CLEANFLICK_NET_HOSTS') or '').split(','):
        h = h.strip()
        if h and h.lower() != own:
            pcs.add(h)
    merged = sorted(pcs, key=str.lower)
    err = ' ; '.join(dict.fromkeys(errs)) or None
    if merged:
        err = None  # postes trouvés → on masque l'échec du domaine (cas workgroup)
    stamp = t0 if merged or err is None else t0 - 1740  # ~1 min si échec total
    with _NET_SCAN_LOCK:
        _NET_SCAN = (stamp, (merged, err, sorted(domains, key=str.lower)))


def computer_shares(host):
    """Partages publiés d'un poste (vue « \\poste » de l'explorateur), via
    net view. Mémoïsé 10 min."""
    cached = _COMPUTER_SHARES_CACHE.get(host)
    if cached and time.time() - cached[0] < _COMPUTER_SHARES_TTL:
        return cached[1]
    try:
        import subprocess
        import re as _re
        raw = subprocess.run(['net', 'view', '\\\\' + host.replace('\\', '')],
                              capture_output=True, timeout=12,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        # La sortie de `net` est codée dans la codepage console (ici cp850).
        stdout = raw.stdout.decode('cp850', errors='replace') \
            if raw.stdout else ''
        names = set()
        if raw.returncode == 0:
            # « <partage>  <type> » : le nom est la colonne de gauche, arrêtée
            # au 1er espace double (padding). On ignore l'en-tête (avant la
            # ligne de tirets, indépendant du locale) et les partages cachés X$.
            hdr = next((i for i, line in enumerate(stdout.splitlines())
                        if _re.search(r'-{10,}', line)), 0)
            for s in stdout.splitlines()[hdr + 1:]:
                s = s.rstrip()
                if not s or s.lower().startswith("l'erreur") or 'termin' in s.lower():
                    continue
                m = _re.match(r'^(.*?)\s{2,}', s)
                if m:
                    n = m.group(1).rstrip()
                    if n and not n.endswith('$') and n.upper() != 'IPC':
                        names.add(n)
        out = (sorted(names, key=str.lower), None if raw.returncode == 0 else 'Poste inaccessible ou sans partage')
    except Exception as e:
        out = ([], str(e))
    _COMPUTER_SHARES_CACHE[host] = (time.time(), out)
    if len(_COMPUTER_SHARES_CACHE) > 200:
        _COMPUTER_SHARES_CACHE.clear()
    return out


def get_net_scan():
    """État mémoïsé du scan réseau (30 min). Retourne (pcs, err, domains)."""
    with _NET_SCAN_LOCK:
        stamp, (pcs, err, doms) = _NET_SCAN
    return stamp, pcs, err, doms


def start_net_scan():
    threading.Thread(target=net_scan_work, daemon=True).start()
