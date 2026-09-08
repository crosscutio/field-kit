"""Append-only action journal with undo.

Every mutating GUI action records ``{time, text, inverse}`` in
``output/history.jsonl``. ``inverse`` is an ``{op, args}`` dict the project
layer knows how to replay; entries without one (pipeline runs, undos) are
log-only.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class History:
    def __init__(self, path):
        self.path = Path(path)

    def entries(self) -> List[Dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def add(self, text: str, inverse: Optional[Dict] = None) -> Dict:
        entry = {'time': datetime.now().strftime('%H:%M'), 'text': text, 'inverse': inverse}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')
        return entry

    def undoable(self) -> Optional[Dict]:
        """Newest entry that still carries an inverse."""
        for e in reversed(self.entries()):
            if e.get('inverse'):
                return e
        return None

    def consume(self, entry: Dict):
        """Strip the inverse from a journal entry after it has been undone."""
        entries = self.entries()
        for i in range(len(entries) - 1, -1, -1):
            if entries[i] == entry:
                entries[i] = {**entry, 'inverse': None, 'undone': True}
                break
        with open(self.path, 'w', encoding='utf-8') as f:
            for e in entries:
                f.write(json.dumps(e) + '\n')

    def recent(self, n=2) -> List[Dict]:
        return list(reversed(self.entries()))[:n]
