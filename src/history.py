"""Historique des opérations (persisté en base)."""
import logging

from . import db

logger = logging.getLogger(__name__)


def load_history():
    try:
        return db.get_history()
    except Exception as e:
        logger.error(f"Error loading history from DB: {e}")
        return []


def save_history(history):
    # Not used: history is persisted via DB
    try:
        # Replace DB contents with provided list
        db.clear_history()
        for entry in (history or []):
            db.add_history(entry)
    except Exception as e:
        logger.error(f"Error saving history to DB: {e}")
        raise


def append_history(entry):
    try:
        db.add_history(entry)
    except Exception as e:
        logger.error(f"Error appending history to DB: {e}")
