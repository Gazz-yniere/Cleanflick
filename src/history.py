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


def load_history_page(limit, offset):
    try:
        return db.get_history(limit=limit, offset=offset)
    except Exception as e:
        logger.error(f"Error loading history page from DB: {e}")
        return []


def load_history_entry(entry_id):
    try:
        return db.get_history_entry(entry_id)
    except Exception as e:
        logger.error(f"Error loading history entry from DB: {e}")
        return None


def load_reverted_history_ids():
    try:
        return db.get_reverted_history_ids()
    except Exception as e:
        logger.error(f"Error loading reverted history IDs from DB: {e}")
        return set()


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


def append_history_batch(entries):
    try:
        db.add_history_batch(entries)
    except Exception as e:
        logger.error(f"Error appending history batch to DB: {e}")
