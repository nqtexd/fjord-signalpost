import unittest

from core import changes, claim, evidence, financial_claims, identity_claims, org_number


def statement(org="810034882", year=2025, kind="SELSKAP", revenue=0):
    return {"virksomhet": {"organisasjonsnummer": org}, "regnskapstype": kind,
            "regnskapsperiode": {"fraDato": f"{year}-01-01", "tilDato": f"{year}-12-31"},
            "valuta": "NOK", "resultatregnskapResultat": {"driftsresultat": {
                "driftsinntekter": {"sumDriftsinntekter": revenue}}}}


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.source = evidence(b"public fixture", "https://example.no/accounts", "2026-10-16T12:00:00Z", {})

    def test_mixed_accounts_never_attach_parent_or_group_values(self):
        rows = [statement(year=2023, revenue=12), statement(org="999999999", revenue=999),
                statement(kind="KONSERN", revenue=888), statement(revenue=34)]
        claims = financial_claims("810034882", rows, self.source)
        revenue = [c for c in claims if c["field"] == "financial.revenue"]
        self.assertEqual([c["value"]["amount"] for c in revenue], [34, 12])
        self.assertEqual(revenue[0]["reporting_period"]["tilDato"], "2025-12-31")

    def test_absent_financial_is_not_zero_and_real_zero_survives(self):
        rows = financial_claims("810034882", [statement(revenue=0)], self.source)
        values = {c["field"]: c for c in rows}
        self.assertEqual(values["financial.revenue"]["value"]["amount"], 0)
        self.assertEqual(values["financial.debt"]["availability"], "not_available")
        self.assertIsNone(values["financial.debt"]["value"])

    def test_invalid_financial_numbers_cannot_be_published(self):
        for value in [True, "120", float("nan"), float("inf")]:
            rows = financial_claims("810034882", [statement(revenue=value)], self.source)
            row = next(c for c in rows if c["field"] == "financial.revenue")
            self.assertEqual(row["availability"], "failed")
            self.assertIsNone(row["value"])

    def test_registry_cannot_be_silently_cross_matched(self):
        with self.assertRaises(ValueError):
            identity_claims("810034882", {"organisasjonsnummer": "999999999", "navn": "Same Name AS"}, self.source)

    def test_refresh_ignores_retrieval_timestamp_and_source_id_changes(self):
        original = claim("employees", 10, self.source)
        fresh_source = dict(self.source, id="ev-new-retrieval", retrieved_at="2026-10-17T12:00:00Z")
        current = claim("employees", 10, fresh_source)
        old = {"organisation_number": "810034882", "claims": [original]}
        new = {"organisation_number": "810034882", "claims": [current]}
        self.assertEqual(changes(old, new), [])
        new["claims"] = [claim("employees", 11, fresh_source)]
        delta = changes(old, new)
        self.assertEqual(len(delta), 1)
        self.assertEqual(delta[0]["before"]["value"], 10)
        self.assertEqual(delta[0]["after"]["value"], 11)
        new["claims"] = [claim("employees", None, None, availability="failed")]
        self.assertEqual(changes(old, new)[0]["kind"], "availability_changed")

    def test_rejects_scraped_text_as_an_organisation_number(self):
        self.assertEqual(org_number("810 034 882"), "810034882")
        for text in ["company: 810034882", "81003488299", "810.034.882", None]:
            with self.assertRaises(ValueError):
                org_number(text)


if __name__ == "__main__":
    unittest.main()
