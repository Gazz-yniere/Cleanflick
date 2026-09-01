"""Accès aux handlers API (TVDB, OMDb)."""
from .handler import APIHandler
from . import tvdb, omdb

__all__ = ['APIHandler', 'tvdb', 'omdb']
