"""Routes de navigation : parcourir le système de fichiers et les postes réseau."""
import os
import re
import string
import time
from pathlib import Path
from urllib.parse import unquote

import platform

from flask import Blueprint, jsonify, request

from .. import network
from ..auth import login_required

bp = Blueprint('browse', __name__)


@bp.route('/api/browse-net')
@login_required
def api_browse_net():
    """Postes de la LAN (groupe de travail + domaine + UNC de la config).
    Mémoïsé 30 min : le scan est lent (NetBIOS/SMB)."""
    stamp, pcs, err, doms = network.get_net_scan()
    if time.time() - stamp < 1800 and pcs is not None:
        return jsonify({'pcs': pcs, 'err': err, 'domains': doms, 'own': network.own_net_name()})
    network.start_net_scan()
    time.sleep(12)
    stamp, pcs, err, doms = network.get_net_scan()
    if time.time() - stamp >= 1800 and pcs is None:
        pcs, err, doms = [], None, []
    return jsonify({'pcs': pcs or [], 'err': err, 'domains': doms or [], 'own': network.own_net_name()})


@bp.route('/api/browse')
@login_required
def api_browse():
    path = unquote(request.args.get('path', '')).strip()
    if not path and platform.system() == 'Windows':
        roots = []
        for d in string.ascii_uppercase:
            try:
                if os.path.exists(f'{d}:\\'):
                    roots.append(f'{d}:\\')
            except OSError:
                pass
        net_roots = [n for n in network.network_roots() if n.startswith('\\\\')]
        for net in network.network_roots():
            if not net.startswith('\\\\') and net not in roots:
                roots.append(net)
        return jsonify({'path': '', 'dirs': [], 'roots': roots, 'net_roots': net_roots, 'parent': None})
    # Normalisation : « K: » → racine du lecteur, UNC (« \\S » ou « //S »),
    # lettres mappées ; à défaut, chemin relatif à l'app.
    if re.match(r'^[A-Za-z](:[\\/]*|)$', path):
        abs_path = path[0].upper() + ':\\'
    elif path.startswith(('\\\\', '//')) or '\\' in path or re.match(r'^[A-Za-z]:[\\/]', path):
        abs_path = os.path.abspath(path)
    else:
        abs_path = os.path.abspath(os.path.join(os.getcwd(), path))
    # « \\poste » seul : le poste expose-t-il des partages ?
    if abs_path.startswith('\\\\') and not os.path.exists(abs_path):
        parts = abs_path.replace('/', '\\').strip('\\').split('\\')
        host = parts[0] if parts else ''
        names, err = network.computer_shares(host) if host else ([], None)
        if names:
            return jsonify({'path': abs_path, 'dirs': names, 'roots': [], 'net_roots': [],
                            'parent': '', 'unc_root': True, 'is_computer': True})
        if host and len(parts) <= 1:
            # Chemin en une seule partie : si c'est une lettre de lecteur on renvoie
            # la racine (normalisation), sinon on signale que le poste est inaccessible.
            if len(host) == 1 and host.isalpha():
                abs_path = host.upper() + ':\\'
            else:
                return jsonify({'path': abs_path, 'dirs': [], 'roots': [], 'net_roots': [],
                                'parent': '', 'unc_root': True, 'is_computer': True,
                                'error': err or ('Aucun partage accessible sur \\' + host)})
    if not os.path.exists(abs_path):
        p = Path(abs_path)
        while p != p.parent:
            p = p.parent
            try:
                exists = p.exists()
            except OSError:
                exists = False
            if exists:
                abs_path = str(p)
                break
    # Le 1er accès à une racine réseau peut échouer temporairement : rechapage rapide.
    try:
        os.listdir(abs_path)
    except OSError:
        time.sleep(1.5)
        try:
            os.listdir(abs_path)
        except OSError:
            pass
    # Racine réseau (\\serveur ou \\serveur\partage) → pas de bouton ".." (on ne
    # remonte jamais jusqu'à \\serveur, qui provoque l'erreur "carte à puce")
    is_unc_root = False
    if abs_path.startswith('\\\\'):
        is_unc_root = len(abs_path.replace('/', '\\').strip('\\').split('\\')) <= 2
    if is_unc_root:
        _parts = abs_path.replace('/', '\\').strip('\\').split('\\')
        # Racine de partage (\\host\partage) : on monte vers la vue du poste
        # (\\host) ; un simple \\host ou une racine : retour à la vue d'accueil.
        _up = '' if len(_parts) <= 1 else '\\\\' + _parts[0]
        return jsonify({'path': abs_path, 'dirs': network.list_dirs(abs_path)[0], 'roots': [],
                        'net_roots': [], 'parent': _up, 'unc_root': True})
    try:
        dirs, err = network.list_dirs(abs_path)
        p = Path(abs_path)
        parent = str(p.parent) if p.parent != p else None
        # Racine d'un lecteur (« D:\ », « D: », « D ») : « parent » renvoie à la
        # vue d'accueil (disques + PC réseau) ; « D:\x » remonte vers « D:\ ».
        is_drive_root = os.name == 'nt' and bool(re.match(r'^[A-Za-z]:\\?$', abs_path))
        if is_drive_root:
            parent = ''
        elif parent and os.name == 'nt' and len(parent) == 2 and parent[1] == ':':
            parent = ''
        return jsonify({'path': abs_path, 'dirs': dirs, 'roots': [], 'net_roots': [], 'parent': parent, 'error': err})
    except PermissionError:
        parent = str(Path(abs_path).parent)
        if is_unc_root:
            _parts = abs_path.replace('/', '\\').strip('\\').split('\\')
            parent = '' if len(_parts) <= 1 else '\\\\' + _parts[0]
        elif os.name == 'nt' and re.match(r'^[A-Za-z]:\\?$', abs_path):
            parent = ''
        elif os.name == 'nt' and len(parent) == 2 and parent[1] == ':':
            parent = ''
        return jsonify({'path': abs_path, 'dirs': [], 'roots': [], 'net_roots': [], 'parent': parent})
    except Exception as e:
        return jsonify({'error': str(e)}), 400
