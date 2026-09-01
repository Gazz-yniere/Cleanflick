"""Routes d'authentification (/login) et page d'accueil (/)."""
from flask import Blueprint, redirect, render_template, request, session

from ..auth import get_password, login_required
from ..utils import _static_version

bp = Blueprint('auth', __name__)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if not get_password():
        return redirect('/')
    if request.method == 'POST':
        if request.form.get('password', '') == get_password():
            session['logged_in'] = True
            return redirect('/')
        return render_template('login.html', error='Mot de passe incorrect')
    return render_template('login.html', error=None)


@bp.route('/')
@login_required
def index():
    return render_template('index.html', cache_bust=_static_version())
