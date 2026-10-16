"""Evidence and exact-identity primitives. No network or inferred financial values."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any

STATES = frozenset({"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"})


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def org_number(value: Any) -> str:
    """Accept presentation spaces, never silently strip unrelated text."""
    text = str(value).strip().replace(" ", "")
    if not re.fullmatch(r"[0-9]{9}", text):
        raise ValueError("Expected a nine-digit Norwegian organisation number")
    return text


def at(document: Any, path: str) -> Any:
    node = document
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def evidence(raw: bytes, url: str, retrieved_at: str, document: Any, *, source_class: str = "official_registry") -> dict:
    fingerprint = hashlib.sha256(raw).hexdigest()
    return {"id": "ev-" + digest([url, fingerprint])[:24], "source_url": url,
            "source_class": source_class, "retrieved_at": retrieved_at,
            "content_sha256": fingerprint, "snapshot": document,
            "snapshot_sha256": digest(document)}


def claim(field: str, value: Any, source: dict | None, path: str | None = None,
          *, period: dict | None = None, availability: str | None = None, reason: str | None = None) -> dict:
    state = availability or ("not_available" if value is None else "available")
    if state not in STATES:
        raise ValueError("Unknown availability state")
    if state == "available" and (value is None or not source):
        raise ValueError("Available facts need a value and source")
    if state != "available" and value is not None:
        raise ValueError("Unavailable facts cannot carry a value")
    result = {"field": field, "availability": state, "value": value,
              "evidence_ids": [source["id"]] if source else []}
    if path:
        result["extraction_path"] = path
    if period:
        result["reporting_period"] = period
    if reason:
        result["reason"] = reason
    return result


IDENTITY_FIELDS = {
    "legal_name": "navn", "legal_form": "organisasjonsform.kode",
    "industry": "naeringskode1", "registered_address": "forretningsadresse",
    "employees": "antallAnsatte", "bankrupt": "konkurs",
    "liquidating": "underAvvikling", "registry_website_candidate": "hjemmeside",
    "latest_filing_year": "sisteInnsendteAarsregnskap",
}


def identity_claims(org: str, document: dict, source: dict) -> list[dict]:
    if document.get("organisasjonsnummer") != org:
        raise ValueError("Registry response belongs to another legal entity")
    return [claim(field, at(document, path), source, path) for field, path in IDENTITY_FIELDS.items()]


FINANCIAL_FIELDS = {
    "revenue": "resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter",
    "operating_result": "resultatregnskapResultat.driftsresultat.driftsresultat",
    "annual_result": "resultatregnskapResultat.aarsresultat",
    "assets": "eiendeler.sumEiendeler", "equity": "egenkapitalGjeld.egenkapital.sumEgenkapital",
    "debt": "egenkapitalGjeld.gjeldOversikt.sumGjeld",
}


def financial_claims(org: str, records: Any, source: dict) -> list[dict]:
    """Keep legal-company statements separate from group accounts and preserve each period."""
    if not isinstance(records, list):
        raise ValueError("Expected an accounts array")
    eligible = []
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            continue
        if at(row, "virksomhet.organisasjonsnummer") != org or row.get("regnskapstype") != "SELSKAP":
            continue
        period = row.get("regnskapsperiode")
        if not isinstance(period, dict) or not period.get("fraDato") or not period.get("tilDato"):
            continue
        eligible.append((index, row))
    eligible.sort(key=lambda pair: pair[1]["regnskapsperiode"]["tilDato"], reverse=True)
    if not eligible:
        return [claim("financials", None, source, availability="not_available",
                      reason="No matching legal-company statement with a reporting period")]
    claims = []
    for index, row in eligible:
        period = row["regnskapsperiode"]
        for field, path in FINANCIAL_FIELDS.items():
            value = at(row, path)
            if isinstance(value, bool) or (value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value))):
                claims.append(claim("financial." + field, None, source,
                                    f"[{index}].{path}", period=period, availability="failed",
                                    reason="Source value is not numeric"))
                continue
            published = None if value is None else {"amount": value, "currency": row.get("valuta")}
            claims.append(claim("financial." + field, published, source, f"[{index}].{path}", period=period))
    return claims


def claim_key(item: dict) -> str:
    return canonical([item["field"], item.get("reporting_period")])


def redact_roles(document: dict) -> dict:
    """Retain public business roles, never birth dates or personal identifiers."""
    groups = []
    for group in document.get("rollegrupper", []):
        roles = []
        for role in group.get("roller", []):
            if role.get("avregistrert"):
                continue
            person = role.get("person", {})
            entity = role.get("enhet", {})
            roles.append({"type": role.get("type"), "person": {"navn": person.get("navn")},
                          "enhet": {key: entity[key] for key in ("organisasjonsnummer", "navn") if key in entity}})
        groups.append({"type": group.get("type"), "sistEndret": group.get("sistEndret"), "roller": roles})
    return {"_links": document.get("_links", {}), "rollegrupper": groups}


def role_claims(org: str, document: dict, source: dict) -> list[dict]:
    linked = at(document, "_links.enhet.href")
    if not isinstance(linked, str) or linked.rstrip("/").split("/")[-1] != org:
        raise ValueError("Roles endpoint does not identify this exact legal entity")
    roles = []
    for group in document.get("rollegrupper", []):
        for role in group.get("roller", []):
            name = at(role, "person.navn")
            entity = role.get("enhet")
            if name or (entity and entity.get("organisasjonsnummer")):
                roles.append({"role_code": at(role, "type.kode"), "role": at(role, "type.beskrivelse"),
                              "name": name, "corporate_holder": entity if entity.get("organisasjonsnummer") else None,
                              "effective_date": group.get("sistEndret")})
    return [claim("registered_roles", roles, source, "rollegrupper[*].roller (privacy projection)")]


def workplace_claims(org: str, document: dict, source: dict) -> list[dict]:
    rows = at(document, "_embedded.underenheter") or []
    if not isinstance(rows, list):
        raise ValueError("Expected registered workplaces array")
    if any(row.get("overordnetEnhet") != org for row in rows):
        raise ValueError("Workplace response includes another parent company")
    if at(document, "page.totalPages") not in (None, 0, 1):
        return [claim("registered_workplaces", None, source, availability="ambiguous",
                      reason="Workplace result is paginated; no partial count is published")]
    locations = [{key: row[key] for key in ("organisasjonsnummer", "navn", "beliggenhetsadresse", "antallAnsatte") if key in row}
                 for row in rows]
    return [claim("registered_workplaces", locations, source, "_embedded.underenheter (exact parent only)")]


def changes(previous: dict | None, current: dict) -> list[dict]:
    if not previous:
        return []
    if previous["organisation_number"] != current["organisation_number"]:
        raise ValueError("Cannot compare different legal companies")
    old = {claim_key(c): c for c in previous.get("claims", [])}
    found = []
    for item in current.get("claims", []):
        before = old.get(claim_key(item))
        if not before:
            if item["availability"] == "available":
                found.append({"kind": "new_evidence", "after": item, "before": None})
        elif before["availability"] == "available" and item["availability"] == "available":
            if canonical(before["value"]) != canonical(item["value"]):
                found.append({"kind": "value_changed", "before": before, "after": item})
        elif before["availability"] != item["availability"]:
            found.append({"kind": "availability_changed", "before": before, "after": item})
    observed = {claim_key(item) for item in current.get("claims", [])}
    for key, before in old.items():
        if key not in observed and before["availability"] == "available":
            found.append({"kind": "not_observed_in_refresh", "before": before, "after": None,
                          "reason": "Previously observed claim absent from refresh; this is not proof that its value changed."})
    return found
