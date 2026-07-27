#!/usr/bin/env python3
"""Répare rename_history.json si corrompu. Exécuter si vous voyez 'Extra data: line X column Y'."""

import json, os
from datetime import datetime

HISTORY_FILE = "rename_history.json"

def repair_rename_history():
    if not os.path.exists(HISTORY_FILE):
        print(f"✓ {HISTORY_FILE} n'existe pas.")
        return
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            json.load(f)
        print(f"✓ {HISTORY_FILE} est valide.")
    except json.JSONDecodeError as e:
        print(f"✗ {HISTORY_FILE} est corrompu: {e}")
        backup = f"{HISTORY_FILE}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
            with open(backup, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"✓ Sauvegarde créée: {backup}")
        except Exception as ex:
            print(f"✗ Impossible de créer la sauvegarde: {ex}")
        try:
            with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump([], f, indent=2, ensure_ascii=False)
            print(f"✓ {HISTORY_FILE} réinitialisé.")
        except Exception as ex:
            print(f"✗ Impossible de réinitialiser: {ex}")

if __name__ == '__main__':
    repair_rename_history()
