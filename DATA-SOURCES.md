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

## Optional TinyFish and licensed external sources

Default registry mode does not call external discovery services. Optional `--web-research` uses [TinyFish Search](https://docs.tinyfish.ai/search-api/reference) and [Fetch](https://docs.tinyfish.ai/fetch-api/reference) via their official APIs. These endpoints were advertised free on 16 October 2026. No paid Agent/Browser/Monitor endpoint, wallet top-up or payment method is used. The operator supplies `TINYFISH_API_KEY` server-side. Search snippets, titles, ranking and raw search output are transient and not persisted.

The [TinyFish terms](https://www.tinyfish.ai/terms) distinguish customer-specific outputs from the service's own content and preserve third-party source rights. The connector therefore does not assume API access grants source scraping/publication permission. Candidate origins are checked for public HTTPS, robots and an explicit rights scope before content is fetched. Restricted platforms/directories are excluded, and other unreviewed websites stay blocked from content publication. No login, security barrier or restricted-source bypass is implemented.

The built-in scope permits structured Wikidata entity facts, [released under CC0](https://www.wikidata.org/wiki/Wikidata:Licensing). A public query to `https://query.wikidata.org/sparql` uses [P2333](https://www.wikidata.org/wiki/Property:P2333), checks the returned organisation-number literal and requires one unique entity. No match is scoped to that query, not proof the company has no web presence. Multiple entities stay ambiguous. This typed identifier anchor prevents a similarly named search result or a property's example list becoming an exact-company profile.

TinyFish Fetch supplies a readable entity-page excerpt. A rendered identity observation requires the legal name and a unique explicitly labelled organisation number. Its source snapshot is a small evidence projection. `content_sha256` hashes that projection, **not raw-origin HTML**; the full provider-response hash is separate. This is stated in `hash_basis` and the inspector. Wikidata query evidence separately hashes its received API bytes. A licensed entity profile is an attributed Wikidata mapping, not a verified operating website.

No company-site jobs, independent news, social activity or sentiment facts are collected. External recall and independently measured matching precision are unmeasured. A later connector needs its own source-rights review and attribution tests.

## Samples and measurement

The sample's 100 organisation numbers were drawn without replacement from the organizer's public universe with Python `random.Random(20261016)`. Its source archive SHA-256 was verified as `1c89710e5b01f8617e86d09fbdff4a52f2f8dbbba297e74f7164b5984f5a0384`. This is a local smoke sample, not the official evaluation batch or an accuracy corpus.

`reports/tinyfish-discovery-2.json` is an earlier two-company discovery test before the exact-number query was added: four searches, one fetch and no published match. `reports/tinyfish-live-ui.json` is the later one-company actual browser workflow: two searches, one fetch, nine direct source requests, a unique P2333 mapping and a matching rendered excerpt. `dist/web-sample.json` preserves its company envelope. Do not combine these with the original 100-company run or interpret them as a measured precision/recall benchmark.

Research: [evaluation contract](https://www.builderr.ai/docs/signalpost-evaluation-harness.md), [source policy](https://www.builderr.ai/starter-briefs/signalpost-sources.md), [challenge](https://www.builderr.ai/challenges/signalpost). The organizer's starter was read separately; its tests/results are not claimed as this project's work. Original project code is MIT; registry observations retain NLOD 2.0 attribution.
