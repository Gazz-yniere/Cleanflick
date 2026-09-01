"""Authentification simple par mot de passe (session Flask)."""
import os
from functools import wraps

from flask import jsonify, redirect, request, session


def get_password():
    return os.environ.get('CLEANFLICK_PASSWORD', '').strip()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if get_password() and not session.get('logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated
