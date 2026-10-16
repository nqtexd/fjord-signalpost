# Sources and permissions

Fjord retrieves public official APIs under the [Norwegian Licence for Open Government Data, NLOD 2.0](https://data.norge.no/nlod/en/2.0), as described by [Brønnøysundregistrene's open-data documentation](https://data.brreg.no/enhetsregisteret/api/dokumentasjon/en/index.html). Attribution: Brønnøysundregistrene. These sources do not endorse Fjord.

| Source | Use |
|---|---|
| `https://data.brreg.no/enhetsregisteret/api/enheter/{org}` | Exact identity, form, address, industry, employees, flags and website candidate |
| `https://data.brreg.no/regnskapsregisteret/regnskap/{org}?regnskapstype=SELSKAP` | Legal-company accounts, filtered again by organisation number and account type; each reporting period retained |
| `https://data.brreg.no/enhetsregisteret/api/enheter/{org}/roller` | Active public business roles; birth dates and personal identifiers discarded before persistence |
| `https://data.brreg.no/enhetsregisteret/api/underenheter?overordnetEnhet={org}&size=1000` | Workplaces with exact registered parent; paginated results withheld instead of presented as complete |

The accounts schema was inspected at `https://data.brreg.no/regnskapsregisteret/regnskap/v3/api-docs`. Records may be missing, delayed, amended or unavailable. No zero or estimate is inferred from absence.

Each available claim carries a source reference, retrieval time, raw content SHA-256, saved snapshot and its own hash, extraction location and reporting period where relevant. Role snapshots remove birth dates and identifiers. Their raw response hash is retained, but raw role bodies are not persisted; the snapshot hash describes the privacy projection. The inspector states this distinction. Repeated evidence retains observation times; refresh preserves previous source IDs.

## Frozen official identity

An evaluator can supply JSONL containing direct BRREG entity objects and its timezone-qualified timestamp. This replaces live identity lookup. A stable evidence URN includes the supplied file's SHA-256. Missing frozen identities never fall back to live data. Other modules still fetch their public endpoints; no hidden cache is substituted. Other evaluator packaging needs an explicit adapter.

## External sources and sample

No search API, LLM, company website, social platform, jobs feed or news source is currently accessed. Registry website values remain unverified discovery candidates. External recall is unmeasured. Before adding connectors, review access terms, robots, retention rights, exact-identity gates and held-out attribution tests. No paid account or entrant-owned secret is needed by the current agent.

The sample's 100 organisation numbers were drawn without replacement from the organizer's public universe with Python `random.Random(20261016)`. Its source archive SHA-256 was verified as `1c89710e5b01f8617e86d09fbdff4a52f2f8dbbba297e74f7164b5984f5a0384`. This is a local smoke sample, not the official evaluation batch or an accuracy corpus.

Research: [evaluation contract](https://www.builderr.ai/docs/signalpost-evaluation-harness.md), [source policy](https://www.builderr.ai/starter-briefs/signalpost-sources.md), [challenge](https://www.builderr.ai/challenges/signalpost). The organizer's starter was read separately; its tests/results are not claimed as this project's work. Original project code is MIT; registry observations retain NLOD 2.0 attribution.
