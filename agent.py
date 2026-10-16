"""Run a company-number JSONL batch with exact-identity official evidence."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import time
from datetime import datetime

from core import (canonical, changes, claim, evidence, financial_claims, identity_claims,
                  now, org_number, redact_roles, role_claims, workplace_claims)
from network import BudgetError, Client, PolicyError, Response


class SnapshotClient:
    """Use an evaluator-provided frozen identity file, never live fallback for missing anchors."""
    def __init__(self, client, filename, as_of):
        if not as_of or datetime.fromisoformat(as_of.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("Frozen registry requires --registry-as-of with an explicit timezone")
        self.client = client
        self.as_of = as_of
        raw = Path(filename).read_bytes()
        self.fingerprint = hashlib.sha256(raw).hexdigest()
        self.records = {}
        for line in raw.decode("utf-8-sig").splitlines():
            if line.strip():
                doc = parse_json(line.encode("utf-8"))
                org = org_number(doc.get("organisasjonsnummer"))
                if org in self.records:
                    raise ValueError("Duplicate organisation number in frozen registry")
                self.records[org] = doc

    def get(self, url):
        match = re.fullmatch(r"https://data\.brreg\.no/enhetsregisteret/api/enheter/([0-9]{9})", url)
        if not match:
            return self.client.get(url)
        org = match.group(1)
        doc = self.records.get(org)
        status = 200 if doc else 404
        body = canonical(doc or {"message": "Missing in supplied frozen identity snapshot"}).encode("utf-8")
        return Response(f"urn:fjord:frozen-registry:sha256:{self.fingerprint}#{org}", status, body,
                        self.as_of, "application/json", 0)


def parse_json(raw: bytes):
    return json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON number")))


def retrieve(client: Client, org: str, previous: dict | None, run_id: str, web_provider=None) -> dict:
    started = now()
    began = time.monotonic()
    result = {"organisation_number": org, "legal_identity": None,
              "run": {"run_id": run_id, "started_at": started},
              "claims": [], "evidence": [], "changes": [], "errors": [],
              "limitations": ["Company websites are unverified candidates. Jobs, news, sentiment and social activity are not collected.",
                              "Registry roles and workplaces describe legal records, not a complete operating footprint."]}
    endpoints = [
        ("identity", f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}"),
        ("financials", f"https://data.brreg.no/regnskapsregisteret/regnskap/{org}?regnskapstype=SELSKAP"),
        ("registered_roles", f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}/roller"),
        ("registered_workplaces", f"https://data.brreg.no/enhetsregisteret/api/underenheter?overordnetEnhet={org}&size=1000"),
    ]
    local_requests = 0
    for family, url in endpoints:
        try:
            local_requests += 1
            response = client.get(url)
            if response.status != 200:
                state = "not_available" if response.status in {404, 410} else "blocked" if response.status in {401, 403, 429} else "failed"
                source = evidence(response.body, response.url, response.retrieved_at,
                                  {"http_status": response.status}, source_class="official_registry_error")
                result["evidence"].append(source)
                result["claims"].append(claim(family, None, source, availability=state, reason=f"Source returned HTTP {response.status}"))
                if family == "identity":
                    break
                continue
            document = parse_json(response.body)
            if family == "registered_roles":
                document = redact_roles(document)
            source = evidence(response.body, response.url, response.retrieved_at, document)
            if response.url.startswith("urn:fjord:frozen-registry:"):
                source["source_class"] = "official_frozen_registry"
            if family == "registered_roles":
                source["snapshot_projection"] = "Active public role names/types only; dates of birth and personal identifiers discarded. Raw response hash retained; raw body not persisted."
            if family == "identity":
                claims = identity_claims(org, document, source)
                result["legal_identity"] = {"organisation_number": org, "name": document.get("navn")}
            elif family == "financials":
                claims = financial_claims(org, document, source)
            elif family == "registered_roles":
                claims = role_claims(org, document, source)
            else:
                claims = workplace_claims(org, document, source)
            result["claims"].extend(claims)
            result["evidence"].append(source)
        except Exception as error:
            state = "blocked" if isinstance(error, PolicyError) else "failed"
            result["claims"].append(claim(family, None, None, availability=state, reason=str(error)[:200]))
            result["errors"].append({"module": family, "kind": type(error).__name__, "message": str(error)[:200]})
            if family == "identity" or isinstance(error, BudgetError):
                break
    if web_provider is not None and result['legal_identity']:
        from footprint import research
        website = next((c['value'] for c in result['claims'] if c['field'] == 'registry_website_candidate' and c['availability'] == 'available'), None)
        result['web_research'] = research(org, result['legal_identity']['name'], website, client, web_provider)
        result['claims'].extend(result['web_research']['claims'])
        result['evidence'].extend(result['web_research']['evidence'])
        result['errors'].extend(result['web_research']['errors'])
    try:
        result["changes"] = changes(previous, result)
    except ValueError as error:
        result["errors"].append({"module": "refresh", "kind": "IdentityMismatch", "message": str(error)})
    old_evidence = {item["id"]: item for item in (previous or {}).get("evidence", [])}
    for item in result["evidence"]:
        existing = old_evidence.get(item["id"])
        if existing:
            item["first_observed_at"] = existing.get("first_observed_at", existing["retrieved_at"])
            item["observed_at"] = sorted(set(existing.get("observed_at", [existing["retrieved_at"]]) + [item["retrieved_at"]]))
        old_evidence[item["id"]] = item
    result["evidence"] = list(old_evidence.values())
    if previous:
        result["run"]["previous_run_id"] = previous.get("run", {}).get("run_id")
    result["run"].update({"completed_at": now(), "terminal_status": "completed" if result["legal_identity"] else "failed"})
    result["operations"] = {"source_fetch_attempts": local_requests, "runtime_ms": int((time.monotonic() - began) * 1000), "third_party_cost_usd": 0}
    available = sum(c["availability"] == "available" for c in result["claims"])
    result["synthesis"] = {"method": "Deterministic claim-count summary; no model inference",
        "text": f"{available} available claims from official legal-company records. External operating activity is unverified.",
        "evidence_ids": [source["id"] for source in result["evidence"]]}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="JSONL strings or objects with organisation_number")
    parser.add_argument("--output", required=True)
    parser.add_argument("--previous", help="Previous output JSONL for evidence-preserving refresh")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--max-requests", type=int, default=2000)
    parser.add_argument("--seconds", type=int, default=2700)
    parser.add_argument("--registry-snapshot", help="Evaluator-provided JSONL of direct BRREG entity objects")
    parser.add_argument("--registry-as-of", help="Frozen registry timestamp with timezone; required with --registry-snapshot")
    parser.add_argument("--web-research", action='store_true', help="Optional TinyFish candidate research; requires server-side TINYFISH_API_KEY")
    parser.add_argument("--max-web-requests", type=int, default=50, help="Separate TinyFish Search/Fetch request cap; free endpoints only")
    args = parser.parse_args()
    inputs = []
    for line in Path(args.input).read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            try:
                inputs.append(json.loads(line))
            except ValueError:
                inputs.append({"organisation_number": line, "input_error": "Malformed JSON input"})
    previous = {}
    if args.previous:
        for line in Path(args.previous).read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                item = json.loads(line)
                previous[item["organisation_number"]] = item
    client = Client(max_requests=args.max_requests, seconds=args.seconds)
    fetcher = SnapshotClient(client, args.registry_snapshot, args.registry_as_of) if args.registry_snapshot else client
    web_provider = None
    if args.web_research:
        from footprint import TinyFish
        web_provider = TinyFish(max_requests=args.max_web_requests, deadline=client.deadline)
    began = time.monotonic()
    run_id = "fjord-" + now()

    def execute(value):
        raw_org = value.get("organisation_number") if isinstance(value, dict) else value
        try:
            org = org_number(raw_org)
        except ValueError as error:
            return {"organisation_number": str(raw_org), "run": {"run_id": run_id, "started_at": now(), "completed_at": now(), "terminal_status": "failed"},
                    "claims": [], "evidence": [], "changes": [], "errors": [{"kind": "InvalidInput", "message": str(error)}],
                    "operations": {"runtime_ms": 0, "third_party_cost_usd": 0}}
        try:
            return retrieve(fetcher, org, previous.get(org), run_id, web_provider) if web_provider else retrieve(fetcher, org, previous.get(org), run_id)
        except Exception as error:
            return {"organisation_number": org, "legal_identity": None,
                    "run": {"run_id": run_id, "started_at": now(), "completed_at": now(), "terminal_status": "failed"},
                    "claims": [], "evidence": [], "changes": [],
                    "errors": [{"kind": type(error).__name__, "message": str(error)[:200]}],
                    "operations": {"runtime_ms": 0, "third_party_cost_usd": 0}}

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 12))) as pool, output.open("w", encoding="utf-8") as stream:
        for result in pool.map(execute, inputs):
            stream.write(canonical(result) + "\n")
            stream.flush()
            results.append(result)
    runtimes = sorted(row.get("operations", {}).get("runtime_ms", 0) for row in results)
    field_counts = {}
    for row in results:
        for field in {c["field"] for c in row["claims"] if c["availability"] == "available"}:
            field_counts[field] = field_counts.get(field, 0) + 1
    report = {"run_id": run_id, "inputs": len(inputs), "terminal_results": len(results),
              "completed": sum(row["run"]["terminal_status"] == "completed" for row in results),
              "runtime_seconds": round(time.monotonic() - began, 3), "requests": client.requests, "bytes_received": client.bytes,
              "third_party_cost_usd": 0, "models": [], "apis": ["BRREG Enhetsregisteret", "BRREG Regnskapsregisteret"],
              "stage": "official identity, accounts, roles and workplaces", "competition_qualification": "not evaluated", "external_coverage": "not implemented",
              "companies_with_available_fields": field_counts,
              "p50_company_runtime_ms": runtimes[int((len(runtimes)-1)*.5)] if runtimes else None,
              "p95_company_runtime_ms": runtimes[int((len(runtimes)-1)*.95)] if runtimes else None,
              "companies_with_errors": sum(bool(row["errors"]) for row in results),
              "independently_measured_precision_recall": None,
              "frozen_registry_sha256": fetcher.fingerprint if args.registry_snapshot else None}
    if web_provider:
        report['web_research'] = {'provider': 'TinyFish', 'requests': web_provider.requests, 'searches': web_provider.searches,
            'fetches': web_provider.fetches, 'max_requests': args.max_web_requests,
            'licensed_profile_claims': sum(c['field'] == 'external.licensed_entity_profile' and c['availability'] == 'available' for row in results for c in row['claims']),
            'candidate_count': sum(len(row.get('web_research', {}).get('candidates', [])) for row in results),
            'paid_endpoints_used': False, 'source_rights': 'Only the built-in reviewed licensed entity source can publish a claim; other websites remain candidates.'}
        report['apis'].extend(['TinyFish Search', 'TinyFish Fetch (only if a reviewed source candidate exists)'])
        report['external_coverage'] = 'Optional licensed-profile discovery; independent precision and recall unmeasured'
    output.with_suffix(".report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
