"""Optional TinyFish discovery. Candidates never silently become company facts."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import os
import re
import threading
import time
import unicodedata
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.robotparser import RobotFileParser

from core import claim, evidence, now
from network import BudgetError, Client, PolicyError, public_addresses

RESTRICTED = ('linkedin.com', 'facebook.com', 'instagram.com', 'glassdoor.com', 'indeed.com',
              'google.com', 'proff.no', 'purehelp.no', 'brreg.no', '1881.no', 'gulesider.no',
              'opencorporates.com', 'yelp.com', 'trustpilot.com', 'x.com', 'twitter.com')

DEFAULT_RIGHTS = {'www.wikidata.org': {'path_pattern': r'^/wiki/Q[0-9]+$',
    'basis': 'Wikidata structured entity facts in the main namespace, CC0; no article prose or other namespaces.',
    'license_url': 'https://www.wikidata.org/wiki/Wikidata:Licensing',
    'reviewed_at': '2026-10-16', 'reviewer': 'AI-assisted source review', 'source_class': 'licensed_structured_entity'}}


def candidate_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise PolicyError('Invalid candidate URL')
    value = value.strip()
    if '://' not in value:
        value = 'https://' + value
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise PolicyError('Candidate must be public HTTPS without credentials or a custom port')
    host = parsed.hostname.encode('idna').decode('ascii').lower()
    if any(host == domain or host.endswith('.'+domain) for domain in RESTRICTED):
        raise PolicyError('Restricted platform or directory is not a company-owned source')
    public_addresses(host)
    return urlunsplit(('https', host, parsed.path or '/', parsed.query, ''))


class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.skip += 1
        elif tag in ('p', 'div', 'li', 'br', 'footer', 'h1', 'h2'):
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript') and self.skip:
            self.skip -= 1

    def handle_data(self, text):
        if not self.skip:
            self.parts.append(text)


def normalized(text):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w]', ' ', unicodedata.normalize('NFKC', text).casefold())).strip()


def identity_proof(text: str, org: str, legal_name: str) -> dict:
    parser = Text(); parser.feed(text)
    plain = re.sub(r'\s+', ' ', ''.join(parser.parts))
    if not plain:
        plain = re.sub(r'\s+', ' ', text)
    pattern = r'(?:organisasjonsnummer|organisation\s+number|organization\s+number|org(?:anisasjons)?\.?\s*(?:nr|nummer|number)\.?)[\s:#]*(?:NO\s*)?([0-9](?:[\s.]*[0-9]){8})(?![\s.]*[0-9])'
    matches = list(re.finditer(pattern, plain, flags=re.I))
    numbers = sorted(set(re.sub(r'\D', '', match.group(1)) for match in matches))
    same_name = bool(normalized(legal_name)) and (' '+normalized(legal_name)+' ') in (' '+normalized(plain)+' ')
    matching = next((m for m in matches if re.sub(r'\D', '', m.group(1)) == org), None)
    excerpt = plain[max(0, matching.start()-130):matching.end()+130] if matching else None
    strong = numbers == [org] and same_name
    reason = ('Exact legal name and a unique labelled organisation number found in this excerpt. Operating-site ownership is not established.'
              if strong else 'Conflicting labelled organisation numbers; do not merge this page with the company.'
              if len(numbers) > 1 else 'Exact legal identity proof was not established.')
    return {'requested_org': org, 'labelled_org_numbers': numbers, 'legal_name_present': same_name,
            'identity_status': 'exact_number_name_excerpt' if strong else 'ambiguous',
            'proof_excerpt': excerpt, 'reason': reason}


def wikidata_mapping(org, document, source):
    bindings = document.get('results', {}).get('bindings')
    if not isinstance(bindings, list):
        raise ValueError('Invalid Wikidata identifier-query response')
    urls = set()
    for row in bindings:
        if row.get('org', {}).get('value') != org:
            raise ValueError('Wikidata query returned a different organisation number')
        uri = row.get('item', {}).get('value', '')
        match = re.fullmatch(r'https?://www\.wikidata\.org/entity/(Q[0-9]+)', uri)
        if not match:
            raise ValueError('Unexpected Wikidata entity URI')
        urls.add('https://www.wikidata.org/wiki/'+match.group(1))
    if len(urls) == 1:
        url = next(iter(urls))
        return claim('external.licensed_entity_profile', {'url': url,
            'assertion': 'Wikidata P2333 maps this exact organisation number. This is not a verified operating website.'},
            source, 'results.bindings[*].org.value / item.value; exact P2333 literal and unique entity'), [url]
    return claim('external.licensed_entity_profile', None, source, availability='ambiguous' if urls else 'not_available',
        reason='Multiple Wikidata entities share this organisation number' if urls else 'No exact P2333 mapping returned by this query; broader web presence is unknown'), []


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TinyFish:
    def __init__(self, *, max_requests=50, deadline=None, key=None):
        self.key = key or os.environ.get('TINYFISH_API_KEY')
        self.max_requests = max_requests
        self.deadline = deadline or time.monotonic()+600
        self.requests = 0; self.searches = 0; self.fetches = 0
        self.last_search = 0; self.guard = threading.Lock()
        self.opener = build_opener(NoRedirect())

    def call(self, kind, payload):
        if not self.key:
            raise PolicyError('TINYFISH_API_KEY is absent; no web research performed')
        with self.guard:
            if kind == 'search':
                delay = max(0, self.last_search+2.05-time.monotonic())
                if delay:
                    time.sleep(min(delay, max(0, self.deadline-time.monotonic())))
            remaining = self.deadline-time.monotonic()
            if remaining <= 0 or self.requests >= self.max_requests:
                raise BudgetError('TinyFish request/time budget exhausted')
            self.requests += 1
            if kind == 'search':
                self.searches += 1; self.last_search = time.monotonic()
            else:
                self.fetches += 1
        headers = {'X-API-Key': self.key, 'Accept': 'application/json', 'User-Agent': 'FjordResearch/0.2'}
        if kind == 'search':
            request = Request('https://api.search.tinyfish.ai?'+urlencode(payload), headers=headers)
        elif kind == 'fetch':
            headers['Content-Type'] = 'application/json'
            request = Request('https://api.fetch.tinyfish.ai', headers=headers,
                              data=json.dumps(payload).encode('utf-8'), method='POST')
        else:
            raise ValueError('Only free Search and Fetch endpoints are supported')
        try:
            with self.opener.open(request, timeout=min(45, remaining)) as response:
                raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise PolicyError('Provider response exceeds size limit')
            return json.loads(raw), hashlib.sha256(raw).hexdigest()
        except HTTPError as error:
            raise RuntimeError(f'TinyFish {kind} returned HTTP {error.code}; no credential or response body logged') from None


def research(org: str, legal_name: str, registry_candidate, network: Client, provider: TinyFish, rights=None) -> dict:
    result = {'organisation_number': org, 'collected_at': now(), 'provider': 'TinyFish Search + Fetch',
              'publication_status': 'licensed_facts_only_other_candidates_review_only', 'candidates': [], 'claims': [], 'evidence': [], 'errors': [],
              'limitations': ['Discovery is not claim evidence. Source-rights review is required before publication.',
                              'No operating website, jobs, sentiment or news facts are automatically published.']}
    rights = DEFAULT_RIGHTS if rights is None else rights
    candidates = [registry_candidate] if registry_candidate else []
    try:
        data, _ = provider.call('search', {'query': f'"{org}" "{legal_name}"', 'location': 'NO', 'language': 'no',
                    'exclude_domains': ','.join(RESTRICTED), 'purpose': 'Find exact legal-company website candidates for evidence review; no personal contact data.'})
        candidates.extend(item.get('url') for item in data.get('results', [])[:8] if isinstance(item, dict))
        # Search snippets, titles, rankings and raw provider output are deliberately not persisted.
        licensed, _ = provider.call('search', {'query': f'"{legal_name}"', 'include_domains': 'wikidata.org',
            'location': 'NO', 'language': 'en', 'purpose': 'Discover a CC0 entity profile for exact organisation-number verification.'})
        licensed_urls = [item.get('url') for item in licensed.get('results', []) if isinstance(item, dict)]
        scoped = [url for url in licensed_urls if isinstance(url, str) and re.fullmatch(r'/wiki/Q[0-9]+', urlsplit(url).path)]
        candidates = scoped[:2] + candidates
    except Exception as error:
        result['errors'].append({'module': 'discovery', 'kind': type(error).__name__, 'message': str(error)[:180]})
    try:
        query = f'SELECT ?item ?org WHERE {{ ?item wdt:P2333 ?org . FILTER(?org = "{org}") }} LIMIT 3'
        query_url = 'https://query.wikidata.org/sparql?'+urlencode({'query': query, 'format': 'json'})
        response = network.get(query_url, accept='application/sparql-results+json')
        if response.status != 200:
            raise RuntimeError(f'Licensed identifier query returned HTTP {response.status}')
        document = json.loads(response.body)
        source = evidence(response.body, response.url, response.retrieved_at, document, source_class='licensed_structured_entity_api')
        source['license_url'] = 'https://www.wikidata.org/wiki/Wikidata:Licensing'
        mapped, urls = wikidata_mapping(org, document, source)
        result['claims'].append(mapped); result['evidence'].append(source)
        candidates = urls + candidates
    except Exception as error:
        result['errors'].append({'module': 'licensed_identifier_query', 'kind': type(error).__name__, 'message': str(error)[:180]})
    seen = set()
    for raw_url in candidates:
        if len(result['candidates']) >= 3:
            break
        item = {'candidate_url': raw_url, 'availability': 'ambiguous', 'source_rights': 'not_reviewed', 'identity_status': 'not_checked'}
        try:
            url = candidate_url(raw_url)
            host = urlsplit(url).hostname
            if host in seen:
                continue
            seen.add(host)
            item['candidate_url'] = url
            robots_url = f'https://{host}/robots.txt'
            robots = network.get(robots_url, accept='text/plain')
            if urlsplit(robots.url).hostname != host:
                raise PolicyError('Robots redirected to another host; access policy is unclear')
            if robots.status == 200:
                if 'html' in robots.content_type.lower():
                    raise PolicyError('Robots returned HTML; access policy is unclear')
                policy = RobotFileParser(); policy.parse(robots.body.decode('utf-8', errors='replace').splitlines())
                if not all(policy.can_fetch(agent, url) for agent in ('*', 'FjordResearch', 'TinyFish')):
                    raise PolicyError('Robots denies this access pattern')
            elif robots.status not in (404, 410):
                raise PolicyError(f'Robots policy unavailable: HTTP {robots.status}')
            item['robots'] = {'url': robots.url, 'http_status': robots.status, 'retrieved_at': robots.retrieved_at,
                              'sha256': hashlib.sha256(robots.body).hexdigest()}
            rule = rights.get(host)
            if not rule or not re.fullmatch(rule.get('path_pattern', r'(?!)'), urlsplit(url).path):
                item['availability'] = 'blocked'
                item['reason'] = 'Candidate discovered and robots checked. An explicit source-rights allowlist is required before fetching this content.'
                result['candidates'].append(item)
                continue
            if not all(rule.get(key) for key in ('basis', 'license_url', 'reviewed_at', 'reviewer')):
                raise PolicyError('Incomplete source-rights review record')
            item['source_rights'] = rule
            data, provider_hash = provider.call('fetch', {'urls': [url], 'format': 'html', 'ttl': 0,
                'per_url_timeout_ms': 35000, 'purpose': 'Check the exact legal organisation number and company name on a licensed public entity page; no personal contact discovery.'})
            pages = data.get('results', [])
            if not pages:
                raise RuntimeError('TinyFish returned no successful page; content not inferred from search')
            page = pages[0]
            final = candidate_url(page.get('final_url') or page.get('url') or url)
            if urlsplit(final).hostname != host or not re.fullmatch(rule['path_pattern'], urlsplit(final).path):
                raise PolicyError('Redirect destination falls outside reviewed source scope')
            text = page.get('text')
            if not isinstance(text, str):
                raise ValueError('Expected extracted page text')
            proof = identity_proof(text, org, legal_name)
            item.update(proof)
            item['final_url'] = final
            item['retrieved_at'] = now()
            item['provider_response_sha256'] = provider_hash
            item['availability'] = 'available' if proof['identity_status'] == 'exact_number_name_excerpt' else 'ambiguous'
            if item['availability'] == 'available':
                projection = {'url': final, 'title': page.get('title'), 'identity_proof': proof,
                              'rights': rule, 'retrieval_provider': 'TinyFish Fetch', 'provider_response_sha256': provider_hash}
                raw = json.dumps(projection, sort_keys=True, ensure_ascii=False).encode('utf-8')
                source = evidence(raw, final, item['retrieved_at'], projection, source_class=rule.get('source_class', 'reviewed_external_source'))
                source['hash_basis'] = 'content_sha256 hashes the saved evidence projection, not the raw origin HTML; provider response hash is recorded separately.'
                source['retrieval_provider'] = 'TinyFish Fetch'
                result['evidence'].append(source)
                result['claims'].append(claim('external.rendered_identity_proof', {'url': final, 'identity_match': 'exact labelled legal number and normalized legal name'}, source,
                    'TinyFish extracted page → deterministic labelled-number and legal-name check; explicit source-rights rule'))
        except Exception as error:
            item['availability'] = 'blocked' if isinstance(error, PolicyError) else 'failed'
            item['reason'] = str(error)[:180]
        result['candidates'].append(item)
    result['completed_at'] = now()
    return result
