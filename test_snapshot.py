import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from agent import SnapshotClient, retrieve
from network import BudgetError


class FrozenSnapshotTests(unittest.TestCase):
    def test_frozen_identity_has_no_live_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'registry.jsonl'
            path.write_text(json.dumps({'organisasjonsnummer': '810034882', 'navn': 'Example AS'})+'\n', encoding='utf-8')
            live = Mock()
            frozen = SnapshotClient(live, path, '2026-10-16T00:00:00Z')
            present = frozen.get('https://data.brreg.no/enhetsregisteret/api/enheter/810034882')
            missing = frozen.get('https://data.brreg.no/enhetsregisteret/api/enheter/999999999')
            self.assertEqual(present.status, 200)
            self.assertEqual(present.retrieved_at, '2026-10-16T00:00:00Z')
            self.assertIn(frozen.fingerprint, present.url)
            self.assertEqual(missing.status, 404)
            live.get.assert_not_called()

    def test_refresh_preserves_prior_evidence_observations(self):
        from network import Response
        from core import canonical
        live = Mock()
        doc = {'organisasjonsnummer': '810034882', 'navn': 'Example AS'}
        def response(url):
            if url.endswith('/810034882'):
                return Response(url, 200, canonical(doc).encode(), '2026-10-16T00:00:00Z', 'application/json', 0)
            raise BudgetError('Test budget exhausted')
        live.get.side_effect = response
        original = retrieve(live, '810034882', None, 'first')
        doc['navn'] = 'Changed Name AS'
        refreshed = retrieve(live, '810034882', original, 'second')
        self.assertEqual(refreshed['run']['previous_run_id'], 'first')
        self.assertTrue(set(s['id'] for s in original['evidence']).issubset(s['id'] for s in refreshed['evidence']))
        self.assertTrue(any(c['kind']=='value_changed' and c['after']['field']=='legal_name' for c in refreshed['changes']))


if __name__ == '__main__':
    unittest.main()
