import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent
from core import changes, claim, evidence, redact_roles, role_claims, workplace_claims
from network import Client, BudgetError, PolicyError, public_addresses


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.source = evidence(b'fixture', 'https://data.brreg.no/example', '2026-10-16T00:00:00Z', {})

    def test_privacy_projection_removes_birth_dates_and_personal_ids(self):
        raw = {'_links': {'enhet': {'href': 'https://data.brreg.no/enhetsregisteret/api/enheter/810034882'}},
               'rollegrupper': [{'type': {'kode': 'STYR'}, 'roller': [
                   {'person': {'navn': {'fornavn': 'Example'}, 'fodselsdato': '1999-01-01', 'identifikasjonsnummer': 'SECRET'}, 'type': {'kode': 'LEDE'}},
                   {'person': {'navn': {'fornavn': 'Former'}}, 'avregistrert': True}]}]}
        projected = redact_roles(raw)
        self.assertNotIn('1999', json.dumps(projected))
        self.assertNotIn('SECRET', json.dumps(projected))
        self.assertEqual(len(role_claims('810034882', projected, self.source)[0]['value']), 1)
        with self.assertRaises(ValueError):
            role_claims('999999999', projected, self.source)

    def test_workplaces_reject_other_legal_parent_and_partial_page(self):
        data = {'_embedded': {'underenheter': [{'overordnetEnhet': '999999999'}]}}
        with self.assertRaises(ValueError):
            workplace_claims('810034882', data, self.source)
        data = {'_embedded': {'underenheter': []}, 'page': {'totalPages': 2}}
        self.assertEqual(workplace_claims('810034882', data, self.source)[0]['availability'], 'ambiguous')

    def test_previous_fact_disappearance_is_not_a_value_change(self):
        before = {'organisation_number': '810034882', 'claims': [claim('employees', 9, self.source)]}
        after = {'organisation_number': '810034882', 'claims': [claim('identity', None, None, availability='failed')]}
        delta = changes(before, after)
        self.assertEqual(delta[0]['kind'], 'not_observed_in_refresh')
        self.assertIsNone(delta[0]['after'])

    def test_outbound_policy_blocks_private_dns_and_credentials(self):
        with patch('network.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(PolicyError):
                public_addresses('public-looking.example')
        for url in ['http://example.com', 'https://user:password@example.com', 'https://example.com:8080']:
            with self.assertRaises(PolicyError):
                Client().get(url)

    def test_redirect_to_private_address_is_rechecked(self):
        from network import Response
        from unittest.mock import MagicMock
        conn = MagicMock()
        response = conn.getresponse.return_value
        response.status = 302
        response.getheader.return_value = 'https://localhost/private'
        with patch('network.PinnedHTTPS', return_value=conn), patch('network.public_addresses', side_effect=[['93.184.216.34'], PolicyError('Local network host blocked')]):
            with self.assertRaises(PolicyError):
                Client().get('https://example.com')
        self.assertEqual(conn.request.call_count, 1)

    def test_run_budget_still_applies_to_retries(self):
        with patch('network.public_addresses', return_value=['93.184.216.34']):
            with self.assertRaises(BudgetError):
                Client(max_requests=0).get('https://example.com')

    def test_every_input_including_duplicate_and_invalid_gets_an_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / 'input.jsonl'
            out = Path(directory) / 'out.jsonl'
            src.write_text('"810034882"\n"810034882"\ninvalid-json\n"bad"\n', encoding='utf-8')
            def fake(client, org, previous, run_id):
                return {'organisation_number': org, 'run': {'terminal_status': 'completed'}, 'claims': [], 'evidence': [], 'changes': [], 'errors': []}
            with patch('sys.argv', ['agent.py', '--input', str(src), '--output', str(out)]), patch('agent.retrieve', side_effect=fake), patch('builtins.print'):
                agent.main()
            results = [json.loads(line) for line in out.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(results), 4)
            self.assertEqual([r['run']['terminal_status'] for r in results], ['completed', 'completed', 'failed', 'failed'])


if __name__ == '__main__':
    unittest.main()
