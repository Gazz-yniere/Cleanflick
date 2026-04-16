#!/usr/bin/env python3
"""
Script de réparation du fichier rename_history.json corrompu
Exécuter ce script si vous voyez l'erreur "Extra data: line X column Y"
"""

import json
import os
from pathlib import Path
from datetime import datetime

RENAME_HISTORY_FILE = "rename_history.json"

def repair_rename_history():
    """Répare le fichier rename_history.json s'il est corrompu"""
    
    if not os.path.exists(RENAME_HISTORY_FILE):
        print(f"✓ {RENAME_HISTORY_FILE} n'existe pas ou est déjà propre.")
        return
    
    try:
        # Essayer de charger le fichier
        with open(RENAME_HISTORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"✓ {RENAME_HISTORY_FILE} est valide.")
        return
    except json.JSONDecodeError as e:
        print(f"✗ {RENAME_HISTORY_FILE} est corrompu: {e}")
        
        # Créer une sauvegarde avec timestamp
        backup_name = f"{RENAME_HISTORY_FILE}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            with open(RENAME_HISTORY_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
            with open(backup_name, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"✓ Sauvegarde créée: {backup_name}")
        except Exception as e:
            print(f"✗ Impossible de créer la sauvegarde: {e}")
        
        # Remplacer par un JSON vide valide
        try:
            with open(RENAME_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump({}, f, indent=2, ensure_ascii=False)
            print(f"✓ {RENAME_HISTORY_FILE} a été réinitialisé avec un contenu valide.")
        except Exception as e:
            print(f"✗ Impossible de réinitialiser le fichier: {e}")

if __name__ == '__main__':
    repair_rename_history()
