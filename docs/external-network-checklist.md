# External Network Assessment Checklist (bars)

Generated from the same source as `external-network-library.json` (bars library export format: import via Library > Import or `python backend/load_library_seed.py external-network-library.json`, the script REPLACES the whole library — prefer the UI import, which appends).

## Global rules

- Tool names are examples; every step states WHAT to check so tools can be swapped.
- Any step that touches an asset requires the asset to be confirmed in scope (`scope_required: true`). Out-of-scope assets go into bars **Excluded hosts**; flows drop them automatically.
- Every step has a `DONE WHEN` condition.
- STOP + notify engagement lead on: out-of-scope asset, signs of production impact, real-user data exposure, active incident at client.

## Field mapping (requested schema -> bars schema)

bars already has a checklist schema, so it is reproduced exactly; no new fields were added. Requested fields map as follows:

| Requested field | Where it lives in bars |
|---|---|
| `phase` | checklist group (`EXT P<n> - ...`) + tag `ext-p<n>` |
| `step_id` | item name prefix (`EXT P1-S6 ...`) + tag `step-p1-s6` |
| `step_name` | item `name` |
| `objective` | first line of item `description` (the line the UI list shows) |
| `check_criteria` | `[ ]` lines in `description` (CHECK CRITERIA block) |
| `opsec_note` | `!` lines in `description` (OPSEC / RESTRICTIONS block) |
| `validation_output` | `DONE WHEN` line in `description`; machine side: `output_regex` keys + `alert_patterns` |
| `depends_on` / `feeds_into` | `DEPENDS_ON` / `FEEDS_INTO` lines; executable chain = flow step `input_mapping` (`{<item name>.<output key>}`) |
| `tools` | `TOOLS` line + the `command_template` |
| `scope_required` | description header + tag `scope-required` / `no-target-contact` |
| `severity_hint` | description header; `finding_template.severity` where an item maps to one finding type |
| `status` | bars execution state: todo = no execution, in_progress = running, done = completed (exit 0), skipped = flow-skipped, n/a = item disabled (`enabled: false`) |
| `evidence_required` | `EVIDENCE REQUIRED` line; evidence attaches to the execution / finding |

## Variables to set before running

| Key | Type | Default | Purpose |
|---|---|---|---|
| `ext_roe_ref` | string | `` | Signed RoE / authorization reference (doc ID + signer + date). Empty = gate fails. |
| `ext_window_start` | string | `` | Test window start, ISO 8601 with offset, e.g. 2026-11-20T09:00:00+03:00 |
| `ext_window_end` | string | `` | Test window end, ISO 8601 with offset, e.g. 2026-11-28T18:00:00+03:00 |
| `ext_domain` | string | `` | Primary in-scope apex domain (e.g. example.com) |
| `ext_domains` | file | `` | All in-scope apex domains, one per line |
| `ext_scope` | file | `` | In-scope IPs/CIDRs (IPv4/IPv6), one per line. Out-of-scope assets go to bars Excluded hosts, never here. |
| `ext_asn` | string | `` | In-scope ASN(s), space separated, e.g. AS64500 AS64501 (optional) |
| `ext_rate` | string | `300` | Max nmap packets/sec (RoE rate limit) |
| `ext_req_rate` | string | `10` | Max HTTP requests/sec for httpx/nuclei (RoE rate limit) |
| `ext_udp_ports` | string | `53,67,69,123,137,161,500,623,1194,1900,3478,4500,5060,5353,11211,51820` | Critical UDP ports for P3-S2 |
| `ext_web_ports` | string | `80,81,443,591,2082,2083,2086,2087,3000,4443,5000,5601,7001,7443,8000,8008,8080,8081,8088,8443,8834,8888,9000,9090,9200,9443,10000,10443` | Fallback web ports probed in P4-S1 in addition to discovered open ports |
| `ext_nuclei_exclude_tags` | string | `dos,intrusive,fuzz,brute-force,default-login,bruteforce` | Nuclei tags excluded unless RoE approves (remove default-login only with written approval) |
| `ext_nessus_export` | string | `` | Path (on the host_agent machine) to the Nessus CSV export for P5-S2 |

## Data flow (what each step consumes and hands on)

| Step | Consumes | Produces (output key) | Feeds |
|---|---|---|---|
| P0-S1 | - | `gate_status`, `scope_nets`, `scope_domains` | P0-S2, P0-S3, P1-*, P2-S1 |
| P0-S2 | P0-S1 | `egress_ip` | P3-S1, P6-S1 |
| P0-S3 | P0-S1 | `announced_not_in_scope` | P0-S1 (scope update), P1-S1 |
| P1-S1 | P0-S1 | `shodan_ips`, `shodan_cves`, `shodan_hostnames` | P3-S1 (expected ports baseline), P3-S3 (CVE leads), P2-S1 (hostnames) |
| P1-S2 | P0-S1 | `whois_emails`, `registrant_org` | P0-S1 (related domains to confirm), P1-S7 (employee identities), P6-S1 |
| P1-S3 | P0-S1, P1-S6 (optional: re-run on key subdomains) | (manual record) | P1-S7 (secrets leads), P4-S5 (URLs to re-check live), P6-S1 |
| P1-S4 | P0-S1 | `archive_hosts`, `archive_interesting` | P1-S6 (archive hostnames), P4-S5 (interesting URLs to re-check live) |
| P1-S5 | P0-S1 | `sf_emails` | P1-S6 (extra hosts), P1-S7 (emails, leaks), P0-S1 (related domains) |
| P1-S6 | P0-S1, P1-S4 (archive hosts) | `ext_subdomains` | P2-S1 |
| P1-S7 | P0-S1, P1-S2 (identities), P1-S5 (emails) | (manual record) | P6-S1, P4-S3 (only with written approval) |
| P2-S1 | `P1-S6.ext_subdomains` | `ext_scan_targets`, `ext_inscope_names`, `ext_inscope_ips`, `ext_outscope_ips`, `ext_cname_hosts`, `ext_dangling` | P2-S2 (PTR), P2-S4 (CNAMEs), P3-S1 (scan targets), P4-S1/P4-S5 (in-scope names) |
| P2-S2 | `P2-S1.ext_inscope_ips` | `axfr_open`, `ns_soa` | P2-S3 (MX/TXT), P2-S4, P3-S1 (NS/MX hosts if IN scope), P6-S1 |
| P2-S3 | P2-S2 (MX/TXT) | `spf_weak`, `dmarc_weak` | P3-S4 (SMTP hosts), P6-S1 |
| P2-S4 | `P2-S1.ext_cname_hosts` | `takeover_candidates` | P6-S1 |
| P3-S1 | `P2-S1.ext_scan_targets` | `ext_open_ports`, `ext_live_ips`, `filtered_note` | P3-S3, P3-S4, P3-S5, P4-S1 |
| P3-S2 | `P2-S1.ext_scan_targets` | `udp_open` | P3-S3, P3-S4 |
| P3-S3 | `P3-S1.ext_live_ips`, `P3-S1.ext_open_ports` | `cve_refs`, `services` | P3-S4, P4-S1, P4-S4, P5-S2 |
| P3-S4 | `P3-S1.ext_live_ips`, `P3-S1.ext_open_ports` | `anon_ftp`, `open_relay` | P5-S2, P6-S1 |
| P3-S5 | `P3-S1.ext_live_ips`, `P3-S1.ext_open_ports` | `weak_tls`, `least_strength` | P6-S1 |
| P4-S1 | `P2-S1.ext_inscope_names`, `P3-S1.ext_live_ips`, `P3-S1.ext_open_ports` | `ext_web_urls` | P4-S2, P4-S3, P4-S4, P5-S1 |
| P4-S2 | `P4-S1.ext_web_urls` | `panels` | P4-S3, P4-S4 |
| P4-S3 | P4-S2 (panel fingerprints), P1-S7 (only with written approval) | (manual record) | P6-S1 |
| P4-S4 | `P4-S1.ext_web_urls` | `crit_cves` | P5-S2, P6-S1 |
| P4-S5 | `P1-S4.archive_interesting`, `P2-S1.ext_inscope_names` | `live_archived` | P5-S2, P6-S1 |
| P5-S1 | `P4-S1.ext_web_urls` | `nuclei_hits` | P5-S2, P6-S1 |
| P5-S2 | Nessus scan of P2-S1 targets, P3-S3, P3-S4, P4-S4, P5-S1 | `version_only` | P6-S1 |
| P6-S1 | P0-S1..P5-S2 (all) | (manual record) | Report |

```
P0-S1 gate ──> P1-S4 archive ──hosts──> P1-S6 subdomains ──> P2-S1 resolve + IN/OUT classify
                    │                                          ├─ ext_inscope_ips ──> P2-S2 PTR
                    │                                          ├─ ext_cname_hosts ──> P2-S4 takeover
                    │                                          ├─ ext_scan_targets ─> P3-S1 TCP / P3-S2 UDP
                    │                                          └─ ext_inscope_names ┐
                    └─ archive_interesting ───────────────────────────────> P4-S5 ◄─┤
P3-S1 ── ext_live_ips + ext_open_ports ──> P3-S3 versions, P3-S4 hardening, P3-S5 TLS, P4-S1 web ◄┘
P4-S1 ── ext_web_urls ──> P4-S2 panels ──> P4-S3 default creds (manual) ; P4-S4 KEV/CVE ; P5-S1 nuclei
P3-S3/P3-S4/P4-S4/P5-S1 + Nessus ──> P5-S2 FP triage ──> P6-S1 report
```

Flows shipped: **EXT Passive Recon Flow** (P0-P2, no port/web probing) and **EXT Full External Assessment Pipeline** (all automated steps). Manual steps (P1-S3 dorking, P1-S7 leaks, P4-S3 default creds, P5-S2 triage, P6-S1 reporting) run outside flows; P0-S1 has `on_failure: stop`, so a failed gate halts the whole flow.

## P0 - Pre-Engagement and Scope Gate

_Must be complete before any Phase 1 activity. Confirms authorization, scope, windows, rate limits and data handling._

### P0-S1 — Authorization and Scope Gate

**Objective:** Hard gate: refuse to proceed unless RoE reference, test window and in-scope lists are present and valid.

- **scope_required:** false · **severity_hint:** n/a · **mode:** automated
- **depends_on:** -
- **feeds_into:** P0-S2, P0-S3, P1-*, P2-S1
- **tools (examples):** sh, python3 (ipaddress)

**Check criteria**

- [ ] Written authorization / RoE signed; reference stored in ext_roe_ref
- [ ] In-scope domains (ext_domains), IP ranges (ext_scope) and ASNs (ext_asn) listed
- [ ] Out-of-scope assets listed explicitly AND added to the bars project as Excluded hosts
- [ ] Testing window (ext_window_start/end), rate limits (ext_rate, ext_req_rate) and emergency contact confirmed
- [ ] Third-party hosted assets (cloud, CDN, SaaS, MSP) identified; provider permission/policy checked
- [ ] Data handling plan for PII/credentials (storage, encryption, retention) agreed
- [ ] Out-of-scope hit rule acknowledged: stop, log, report to engagement lead, never test

**OpSec / restrictions**

> ⚠ Flow step on_failure=stop: nothing downstream runs if the gate fails  
> ⚠ Gate checks the clock: re-run at the start of every testing day  
> ⚠ STOP + notify engagement lead on: out-of-scope asset, signs of production impact, real-user data exposure, active incident at client.  

**Done when (validation_output):** Output contains SCOPE_GATE_OK, window state INSIDE, every scope line parsed as valid IP/CIDR, and the domain list is non-empty.

**Evidence required:** Gate output (SCOPE_GATE_OK + normalized scope list), RoE document reference

<details><summary>bars command</summary>

```sh
python3 - {ext_scope} {ext_domains} '{ext_roe_ref}' '{ext_window_start}' '{ext_window_end}' <<'PY'
import sys, ipaddress, datetime
scope, doms, roe, ws, we = sys.argv[1:6]
ok = True
if not roe.strip():
    print('FAIL roe: ext_roe_ref empty'); ok = False
now = datetime.datetime.now(datetime.timezone.utc)
try:
    s = datetime.datetime.fromisoformat(ws); e = datetime.datetime.fromisoformat(we)
    state = 'INSIDE' if s <= now <= e else 'OUTSIDE'
    print('WINDOW %s %s -> %s now=%s' % (state, ws, we, now.isoformat()))
    ok = ok and state == 'INSIDE'
except Exception as x:
    print('FAIL window: %s' % x); ok = False
nets = []
for line in open(scope).read().split():
    try:
        nets.append(ipaddress.ip_network(line, strict=False)); print('SCOPE_NET %s' % line)
    except ValueError:
        print('FAIL scope line: %s' % line); ok = False
d = [x.strip().lower() for x in open(doms).read().split() if x.strip()]
for x in d: print('SCOPE_DOMAIN %s' % x)
if not nets and not d:
    print('FAIL scope: no networks and no domains'); ok = False
print('ADDRESSES_IN_SCOPE %d' % sum(n.num_addresses for n in nets))
print('SCOPE_GATE_OK' if ok else 'SCOPE_GATE_FAIL')
sys.exit(0 if ok else 1)
PY
```

</details>

### P0-S2 — Egress IP and Engagement Log

**Objective:** Record the scan source (egress) IP and UTC start time so the client SOC can correlate and whitelist/attribute traffic.

- **scope_required:** false · **severity_hint:** n/a · **mode:** automated
- **depends_on:** P0-S1
- **feeds_into:** P3-S1, P6-S1
- **tools (examples):** curl, date

**Check criteria**

- [ ] Egress IP recorded and shared with client contact / SOC per RoE
- [ ] Emergency contact phone/email verified reachable
- [ ] Daily start/stop times logged (re-run each day)

**OpSec / restrictions**

> ⚠ Run from the same host_agent that will scan; a different egress invalidates SOC correlation  
> ⚠ Uses a third-party IP echo service (api.ipify.org); swap if RoE forbids  

**Done when (validation_output):** EGRESS_IP line present with a valid IP and UTC timestamp.

**Evidence required:** Egress IP + timestamp shared with client contact

<details><summary>bars command</summary>

```sh
echo "UTC_START $(date -u +%Y-%m-%dT%H:%M:%SZ)"; echo "EGRESS_IP $(curl -s --max-time 15 https://api.ipify.org)"
```

</details>

### P0-S3 — ASN Prefix Ownership Cross-Check

**Objective:** Compare prefixes announced by the in-scope ASN(s) with ext_scope; anything announced but not listed needs an explicit scope decision.

- **scope_required:** false · **severity_hint:** info · **mode:** automated
- **depends_on:** P0-S1
- **feeds_into:** P0-S1 (scope update), P1-S1
- **tools (examples):** whois (RADb), python3

**Check criteria**

- [ ] Every ANNOUNCED_NOT_IN_SCOPE prefix sent to engagement lead for decision
- [ ] Every IN_SCOPE_NOT_ANNOUNCED range verified as really client-owned (WHOIS/RIR)
- [ ] ext_scope updated only after written confirmation

**OpSec / restrictions**

> ⚠ Passive (RADb whois). Never add a prefix to scope on your own judgement  

**Done when (validation_output):** Diff produced; each ANNOUNCED_NOT_IN_SCOPE prefix has a recorded decision (add / exclude).

**Evidence required:** Prefix diff (ANNOUNCED_NOT_IN_SCOPE list) and lead's decision

<details><summary>bars command</summary>

```sh
for a in {ext_asn}; do whois -h whois.radb.net -- "-i origin $a"; done | awk '/^route6?:/ {print $2}' | sort -u > ext_asn_prefixes.txt; python3 - ext_asn_prefixes.txt {ext_scope} <<'PY'
import sys, ipaddress
ann = set(ipaddress.ip_network(x, strict=False) for x in open(sys.argv[1]).read().split())
sc = set(ipaddress.ip_network(x, strict=False) for x in open(sys.argv[2]).read().split())
for n in sorted(ann, key=str):
    tag = 'COVERED' if any(n.version == s.version and n.subnet_of(s) for s in sc) else 'ANNOUNCED_NOT_IN_SCOPE'
    print(tag, n)
for s in sorted(sc, key=str):
    if not any(s.version == n.version and s.subnet_of(n) for n in ann):
        print('IN_SCOPE_NOT_ANNOUNCED', s)
PY
```

</details>

## P1 - Passive OSINT and Digital Footprint

_No packets to target assets. Third-party data sources only. Results may be stale: record timestamps._

### P1-S1 — Internet Asset Inventory

**Objective:** Passive inventory of in-scope IPs from internet-scan datasets: open ports, banners/CPEs, hostnames, known CVE matches.

- **scope_required:** false · **severity_hint:** info · **mode:** automated
- **depends_on:** P0-S1
- **feeds_into:** P3-S1 (expected ports baseline), P3-S3 (CVE leads), P2-S1 (hostnames)
- **tools (examples):** Shodan InternetDB (keyless), Shodan/Censys/FOFA/BinaryEdge UI or API (manual cross-check)

**Check criteria**

- [ ] Current open ports per IP recorded
- [ ] Historical ports/services checked in Shodan host history (manual, API key)
- [ ] Service banners / CPEs recorded
- [ ] Known CVE matches (vulns) recorded as leads only, not findings
- [ ] Cross-checked with Censys / FOFA / BinaryEdge-style sources; discrepancies noted
- [ ] Each result's fetch timestamp recorded (datasets can be weeks old)
- [ ] Hostnames found here added to the P2-S1 candidate list if under in-scope domains

**OpSec / restrictions**

> ⚠ Passive: queries go to the data provider, not the target  
> ⚠ Expansion capped at 4096 addresses; query larger ranges via provider net: search instead  
> ⚠ Dataset CVEs are version-based guesses: never report without P3/P5 verification  

**Done when (validation_output):** Every in-scope IP (up to cap) queried; HIT lines list ports/cpes/vulns with timestamp; cross-check notes present.

**Evidence required:** Per-IP HIT lines with fetch timestamp; screenshots of Censys/FOFA discrepancies

<details><summary>bars command</summary>

```sh
python3 - {ext_scope} <<'PY'
import sys, ipaddress, json, time, urllib.request, datetime, itertools
ips = []
for x in open(sys.argv[1]).read().split():
    n = ipaddress.ip_network(x, strict=False)
    ips += [str(a) for a in itertools.islice(n.hosts() if n.num_addresses > 2 else iter(n), 4096 - len(ips))]
    if len(ips) >= 4096: break
for ip in ips[:4096]:
    try:
        d = json.load(urllib.request.urlopen('https://internetdb.shodan.io/' + ip, timeout=15))
        ts = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        print('HIT', ip, 'fetched=' + ts, 'ports=' + ','.join(map(str, d.get('ports', []))), 'cpes=' + ','.join(d.get('cpes', [])), 'hostnames=' + ','.join(d.get('hostnames', [])), 'vulns=' + ','.join(d.get('vulns', [])))
        for h in d.get('hostnames', []): print('HOSTNAME', h)
    except Exception:
        pass
    time.sleep(1)
PY
```

</details>

### P1-S2 — WHOIS and Registrant PII Review

**Objective:** Review domain and IP registration data; flag personal contact data (not generic role addresses) and pivot on registrant org to find related domains.

- **scope_required:** false · **severity_hint:** low · **mode:** automated
- **depends_on:** P0-S1
- **feeds_into:** P0-S1 (related domains to confirm), P1-S7 (employee identities), P6-S1
- **tools (examples):** whois, WHOIS history services (manual), reverse WHOIS (manual)

**Check criteria**

- [ ] Domain WHOIS reviewed for every in-scope domain
- [ ] IP/RIR WHOIS reviewed for every in-scope network (org, abuse contact, admin-c/tech-c)
- [ ] Generic role addresses (info@, iletisim@, noc@, abuse@, hostmaster@) treated as normal
- [ ] Personal email (free-mail or name.surname@), mobile numbers, employee names flagged
- [ ] WHOIS history checked for past registrant data leaks (manual)
- [ ] Registrant-organization / email pivots run; related domains sent to lead for scope decision

**OpSec / restrictions**

> ⚠ Passive. Store PII only in redacted form per data handling plan  
> ⚠ Related domains found by pivot are OUT of scope until the lead confirms  

**Done when (validation_output):** WHOIS reviewed for all domains and networks; PII hits listed (redacted); related-domain list handed to lead.

**Evidence required:** Redacted WHOIS excerpt for every PII hit

<details><summary>bars command</summary>

```sh
for d in $(cat {ext_domains}); do echo "=== DOMAIN $d"; whois "$d"; sleep 2; done; for n in $(cat {ext_scope}); do echo "=== NET $n"; whois "${n%/*}"; sleep 2; done
```

</details>

### P1-S3 — Search Engine Dorking Google and Bing

**Objective:** Generate and manually run Google and Bing dork sets per in-scope domain to find forgotten documents, listings and sensitive exposures.

- **scope_required:** false · **severity_hint:** medium · **mode:** manual
- **depends_on:** P0-S1, P1-S6 (optional: re-run on key subdomains)
- **feeds_into:** P1-S7 (secrets leads), P4-S5 (URLs to re-check live), P6-S1
- **tools (examples):** Google, Bing, search engine cache (manual)

**Check criteria**

- [ ] Google and Bing evaluated SEPARATELY (indexes differ); record results per engine
- [ ] File extensions: pdf
- [ ] File extensions: doc / docx
- [ ] File extensions: xls / xlsx
- [ ] File extensions: ppt / pptx
- [ ] File extensions: csv
- [ ] File extensions: sql
- [ ] File extensions: txt
- [ ] File extensions: env
- [ ] File extensions: log
- [ ] File extensions: bak / old / orig / save
- [ ] File extensions: conf / cfg / ini
- [ ] File extensions: xml
- [ ] File extensions: json
- [ ] File extensions: yml / yaml
- [ ] File extensions: zip / tar / gz / 7z / rar
- [ ] File extensions: pem / key / pfx (FLAG ONLY, never download)
- [ ] Directory listings: intitle:"index of"
- [ ] Exposed admin / login pages
- [ ] Error messages and stack traces
- [ ] Exposed .git / .svn / .DS_Store
- [ ] Cached pages reviewed for removed content
- [ ] Document metadata (author, software, internal paths) noted from public docs

**OpSec / restrictions**

> ⚠ Run queries by hand in a browser; automated scraping violates engine ToS and gets the egress IP blocked  
> ⚠ Never download key/pfx/pem/env content; record URL + existence only  
> ⚠ Opening an indexed URL on the target is active contact: only for in-scope hosts  

**Done when (validation_output):** Every query in the generated list executed on both engines; hits recorded with engine + date; sensitive hits escalated.

**Evidence required:** Per query: engine, date, hit count, screenshot/URL list (no downloaded secrets)

<details><summary>bars command</summary>

```sh
for d in $(cat {ext_domains}); do for e in pdf doc docx xls xlsx ppt pptx csv sql txt env log bak old orig save conf cfg ini xml json yml yaml zip tar gz 7z rar pem key pfx; do q="site:$d filetype:$e"; echo "DORK ext=$e GOOGLE https://www.google.com/search?q=$(printf %s "$q" | jq -sRr @uri)"; echo "DORK ext=$e BING https://www.bing.com/search?q=$(printf %s "$q" | jq -sRr @uri)"; done; q="site:$d intitle:\"index of\""; echo "DORK cat=dirlist GOOGLE https://www.google.com/search?q=$(printf %s "$q" | jq -sRr @uri)"; echo "DORK cat=dirlist BING https://www.bing.com/search?q=$(printf %s "$q" | jq -sRr @uri)"; q="site:$d inurl:login OR inurl:admin OR inurl:signin OR intitle:login"; echo "DORK cat=login GOOGLE https://www.google.com/search?q=$(printf %s "$q" | jq -sRr @uri)"; echo "DORK cat=login BING https://www.bing.com/search?q=$(printf %s "$q" | jq -sRr @uri)"; q="site:$d \"stack trace\" OR \"Traceback (most recent call last)\" OR \"Warning: mysql\" OR \"SQLSTATE\" OR \"Exception in thread\""; echo "DORK cat=errors GOOGLE https://www.google.com/search?q=$(printf %s "$q" | jq -sRr @uri)"; echo "DORK cat=errors BING https://www.bing.com/search?q=$(printf %s "$q" | jq -sRr @uri)"; q="site:$d inurl:.git OR inurl:.svn OR inurl:.DS_Store OR inurl:.hg"; echo "DORK cat=vcs GOOGLE https://www.google.com/search?q=$(printf %s "$q" | jq -sRr @uri)"; echo "DORK cat=vcs BING https://www.bing.com/search?q=$(printf %s "$q" | jq -sRr @uri)"; q="site:$d inurl:wp-config OR inurl:web.config OR inurl:phpinfo OR intitle:phpinfo"; echo "DORK cat=config GOOGLE https://www.google.com/search?q=$(printf %s "$q" | jq -sRr @uri)"; echo "DORK cat=config BING https://www.bing.com/search?q=$(printf %s "$q" | jq -sRr @uri)"; q="site:$d \"internal use only\" OR \"confidential\" OR \"gizli\" OR \"hizmete özel\""; echo "DORK cat=docs-internal GOOGLE https://www.google.com/search?q=$(printf %s "$q" | jq -sRr @uri)"; echo "DORK cat=docs-internal BING https://www.bing.com/search?q=$(printf %s "$q" | jq -sRr @uri)"; done
```

</details>

### P1-S4 — Web Archive Analysis

**Objective:** Mine Wayback Machine and URLScan for removed endpoints, old configs, forgotten hosts and parameters.

- **scope_required:** false · **severity_hint:** medium · **mode:** automated
- **depends_on:** P0-S1
- **feeds_into:** P1-S6 (archive hostnames), P4-S5 (interesting URLs to re-check live)
- **tools (examples):** Wayback CDX API, URLScan search API, CommonCrawl index (manual)

**Check criteria**

- [ ] Wayback CDX pulled for *.domain on every in-scope domain
- [ ] URLScan results pulled
- [ ] CommonCrawl index checked (manual, index.commoncrawl.org)
- [ ] Removed endpoints / admin paths noted
- [ ] Old config / backup / dump files noted (ARCHIVE_INTERESTING)
- [ ] Parameters collected for later web testing
- [ ] Forgotten hostnames passed to P1-S6
- [ ] Liveness of archived paths is NOT checked here (active): deferred to P4-S5

**OpSec / restrictions**

> ⚠ Passive: requests go to archive.org / urlscan.io only  
> ⚠ Do not open archived URLs on the live target in this phase  

**Done when (validation_output):** CDX + URLScan completed for every domain; ARCHIVE_INTERESTING and ARCHIVE_HOST lists produced (may be empty, recorded as such).

**Evidence required:** Interesting archived URL list with archive timestamp

<details><summary>bars command</summary>

```sh
for d in $(cat {ext_domains}); do curl -s --max-time 300 "https://web.archive.org/cdx/search/cdx?url=*.$d/*&output=txt&fl=original&collapse=urlkey&limit=100000"; curl -s --max-time 60 "https://urlscan.io/api/v1/search/?q=domain:$d&size=1000" | jq -r '.results[].page.url' 2>/dev/null; sleep 2; done | sort -u > ext_archive_urls.txt; echo "ARCHIVE_URL_COUNT $(wc -l < ext_archive_urls.txt)"; grep -Eio '^https?://[^/:?#]+' ext_archive_urls.txt | sed -E 's#^https?://##' | tr 'A-Z' 'a-z' | sort -u | sed 's/^/ARCHIVE_HOST /'; grep -Ei '\.(sql|bak|old|orig|save|env|log|conf|cfg|ini|xml|json|ya?ml|zip|tar|gz|7z|rar|pem|key|pfx|csv|xlsx?|docx?|pdf)(\?|$)|/(admin|backup|debug|api|swagger|graphql|\.git)' ext_archive_urls.txt | sed 's/^/ARCHIVE_INTERESTING /'; echo 'ARCHIVE_INTERESTING -'; grep -Eo '[?&][A-Za-z0-9_.-]+=' ext_archive_urls.txt | tr -d '?&=' | sort | uniq -c | sort -rn | head -100 | sed 's/^/ARCHIVE_PARAM /'
```

</details>

### P1-S5 — SpiderFoot Passive Correlation

**Objective:** Low-priority supporting step: automated OSINT correlation for secondary leaks, related domains and third-party links.

- **scope_required:** false · **severity_hint:** info · **mode:** automated
- **depends_on:** P0-S1
- **feeds_into:** P1-S6 (extra hosts), P1-S7 (emails, leaks), P0-S1 (related domains)
- **tools (examples):** SpiderFoot (-u passive)

**Check criteria**

- [ ] Run in passive use-case only
- [ ] Secondary data leaks reviewed
- [ ] Related / affiliate domains listed and sent to lead (out of scope until confirmed)
- [ ] Third-party services (SaaS, CDN, mail, analytics) listed for P0 provider-permission review
- [ ] Duplicates with P1-S1..S4 ignored; only new leads carried forward

**OpSec / restrictions**

> ⚠ -u passive only: footprint/investigate/all modules touch the target  
> ⚠ Low priority: skip if time-boxed; mark n/a with reason  

**Done when (validation_output):** Passive scan finished; new leads (if any) copied to the relevant steps.

**Evidence required:** SpiderFoot CSV export

<details><summary>bars command</summary>

```sh
spiderfoot -s {ext_domain} -u passive -o csv -q -n 2>/dev/null
```

</details>

### P1-S6 — CT and Passive Subdomain Discovery

**Objective:** Build the deduplicated subdomain list from Certificate Transparency, passive DNS sources and archive hostnames.

- **scope_required:** false · **severity_hint:** info · **mode:** automated
- **depends_on:** P0-S1, P1-S4 (archive hosts)
- **feeds_into:** P2-S1
- **tools (examples):** crt.sh, subfinder (passive sources), Wayback CDX hosts

**Check criteria**

- [ ] CT logs (crt.sh) queried for every in-scope domain
- [ ] Passive DNS / aggregator sources queried (subfinder -all, API keys configured)
- [ ] Archive hostnames (P1-S4) merged
- [ ] Wildcards stripped, lowercased, deduplicated
- [ ] Only names under in-scope apex domains kept; others sent to lead
- [ ] Apex domains themselves included so P2 never starts empty

**OpSec / restrictions**

> ⚠ Passive: no queries to target nameservers here  
> ⚠ A name under an in-scope domain can still be hosted out of IP scope: P2-S1 classifies  

**Done when (validation_output):** SUB list produced, deduplicated, only in-scope suffixes; count recorded.

**Evidence required:** Final SUB list with count

<details><summary>bars command</summary>

```sh
{ for d in $(cat {ext_domains}); do echo "$d"; curl -s --max-time 120 "https://crt.sh/?q=%25.$d&output=json" | jq -r '.[].name_value' 2>/dev/null; subfinder -d "$d" -all -silent 2>/dev/null; curl -s --max-time 300 "https://web.archive.org/cdx/search/cdx?url=*.$d&output=txt&fl=original&collapse=urlkey&limit=100000" | grep -Eio '^https?://[^/:?#]+' | sed -E 's#^https?://##'; sleep 2; done; } | tr 'A-Z' 'a-z' | sed 's/^\*\.//' | sort -u > ext_subs_raw.txt; python3 - ext_subs_raw.txt {ext_domains} <<'PY'
import sys
doms = [d.strip().lower() for d in open(sys.argv[2]).read().split() if d.strip()]
n = 0
for h in open(sys.argv[1]).read().split():
    if any(h == d or h.endswith('.' + d) for d in doms):
        print('SUB', h); n += 1
    else:
        print('OFFSCOPE_NAME', h)
print('SUB_COUNT', n)
print('SUB -')  # placeholder: keeps the chain alive on IP-only engagements
PY
```

</details>

### P1-S7 — Leaked Credentials and Code Exposure

**Objective:** Check breach corpora, public code repos, paste sites and public cloud storage for corporate accounts and exposed secrets/configs.

- **scope_required:** false · **severity_hint:** high · **mode:** manual
- **depends_on:** P0-S1, P1-S2 (identities), P1-S5 (emails)
- **feeds_into:** P6-S1, P4-S3 (only with written approval)
- **tools (examples):** HIBP domain search / breach intel (manual), GitHub / GitLab code search, grep.app, paste-site search, public bucket search (manual)

**Check criteria**

- [ ] Breach corpora checked for @domain accounts; record exposure + breach name/date only
- [ ] Passwords / hashes NOT stored in bars or the report
- [ ] Public code repos searched (org name, domain, internal hostnames, API key patterns)
- [ ] Paste sites searched
- [ ] Public cloud storage (S3/Azure Blob/GCS) checked for company-named buckets (listing only, no download of data)
- [ ] Exposed secrets reported to lead immediately for rotation
- [ ] No discovered credential used without explicit written scope approval

**OpSec / restrictions**

> ⚠ NEVER validate a found credential against any live system without written approval  
> ⚠ Redact secrets in evidence (first 4 chars max)  
> ⚠ STOP + notify engagement lead on: out-of-scope asset, signs of production impact, real-user data exposure, active incident at client.  

**Done when (validation_output):** All sources checked; exposure list (accounts/secrets by location, redacted) recorded; lead notified of live secrets.

**Evidence required:** Count of exposed accounts per breach (no passwords), redacted secret location (repo/file/line)

<details><summary>bars command</summary>

```sh
for d in $(cat {ext_domains}); do o=${d%%.*}; echo "SEARCH github https://github.com/search?type=code&q=%22$d%22"; echo "SEARCH github-org https://github.com/search?type=users&q=$o"; echo "SEARCH gitlab https://gitlab.com/search?search=%22$d%22"; echo "SEARCH grepapp https://grep.app/search?q=$d"; echo "SEARCH postman https://www.postman.com/search?q=$d"; echo "SEARCH buckets https://buckets.grayhatwarfare.com/files?keywords=$o"; echo "SEARCH hibp https://haveibeenpwned.com/DomainSearch (verify $d ownership via client)"; done
```

</details>

## P2 - DNS Configuration and Infrastructure

_DNS queries against public resolvers / authoritative NS of in-scope domains. Classifies every asset IN/OUT of scope before active testing._

### P2-S1 — Subdomain Resolution and Scope Classification

**Objective:** Resolve every candidate name (A/AAAA/CNAME) and classify each IP IN / OUT of ext_scope; build the active-testing target list.

- **scope_required:** true · **severity_hint:** info · **mode:** automated
- **depends_on:** P1-S6
- **feeds_into:** P2-S2 (PTR), P2-S4 (CNAMEs), P3-S1 (scan targets), P4-S1/P4-S5 (in-scope names)
- **tools (examples):** dig, python3 ipaddress

**Check criteria**

- [ ] Every P1-S6 name resolved (A, AAAA, CNAME chain)
- [ ] Each resolved IP classified IN (inside ext_scope) / OUT / PRIVATE
- [ ] OUT names/IPs logged and reported to lead; never forwarded to active steps
- [ ] Private / internal IPs in public DNS flagged (information leak)
- [ ] CNAMEs without final address recorded as dangling candidates for P2-S4
- [ ] Scan target list = ext_scope networks + IN-scope resolved IPs

**OpSec / restrictions**

> ⚠ Uses the agent's configured resolver; no zone-walking here  
> ⚠ CDN/WAF/SaaS IPs (OUT) are third-party: do not scan without provider permission (P0)  

**Done when (validation_output):** Every candidate name has a RESOLVED/NORES line; SCAN_TARGET list non-empty; OUT list sent to lead.

**Evidence required:** Classification table (name, record, IP, IN/OUT/PRIVATE)

<details><summary>bars command</summary>

```sh
python3 - {subdomains} {ext_scope} <<'PY'
import sys, subprocess, ipaddress
names = sorted(set(x for x in open(sys.argv[1]).read().split() if '.' in x))
nets = [ipaddress.ip_network(x, strict=False) for x in open(sys.argv[2]).read().split()]
for n in nets: print('SCAN_TARGET', n)
seen = set()
for h in names:
    ips, cn = [], []
    for t in ('A', 'AAAA'):
        out = subprocess.run(['dig', '+noall', '+answer', '+time=3', '+tries=2', t, h], capture_output=True, text=True).stdout
        for line in out.splitlines():
            p = line.split()
            if len(p) >= 5 and p[3] == 'CNAME': cn.append(p[4].rstrip('.'))
            if len(p) >= 5 and p[3] in ('A', 'AAAA'): ips.append(p[4])
    for c in sorted(set(cn)): print('CNAME', h, c)
    if not ips:
        print('NORES', h, ('DANGLING_CANDIDATE ' + cn[-1]) if cn else '')
        continue
    inscope = False
    for ip in sorted(set(ips)):
        a = ipaddress.ip_address(ip)
        cls = 'PRIVATE' if (a.is_private or a.is_loopback or a.is_link_local) else ('IN' if any(a in n for n in nets) else 'OUT')
        print('RESOLVED', cls, h, ip)
        if cls == 'IN':
            inscope = True
            if ip not in seen: seen.add(ip); print('SCAN_TARGET', ip); print('INSCOPE_IP', ip)
    if inscope: print('INSCOPE_NAME', h)
    if cn: print('CNAME_HOST', h)
for k in ('INSCOPE_NAME', 'INSCOPE_IP', 'CNAME_HOST'): print(k, '-')  # placeholders, consumers skip '-'
PY
```

</details>

### P2-S2 — DNS Records and Zone Transfer

**Objective:** Enumerate every record type on each in-scope domain, test AXFR/IXFR on every authoritative NS, check NS consistency, DNSSEC and PTRs.

- **scope_required:** true · **severity_hint:** high (AXFR) / low (others) · **mode:** automated
- **depends_on:** P0-S1, P2-S1 (in-scope IPs for PTR)
- **feeds_into:** P2-S3 (MX/TXT), P2-S4, P3-S1 (NS/MX hosts if IN scope), P6-S1
- **tools (examples):** dig

**Check criteria**

- [ ] Record type: A
- [ ] Record type: AAAA
- [ ] Record type: MX
- [ ] Record type: TXT
- [ ] Record type: NS
- [ ] Record type: CNAME
- [ ] Record type: SRV (common service labels)
- [ ] Record type: PTR (reverse DNS of in-scope IPs)
- [ ] Record type: SOA (serial, contact, timers)
- [ ] Record type: CAA (present? which CAs?)
- [ ] Record type: NAPTR
- [ ] Record type: DNSKEY / DS (DNSSEC signed and chained?)
- [ ] AXFR tested on EVERY authoritative NS (not just the first)
- [ ] IXFR tested on every authoritative NS
- [ ] NS responses consistent across all authoritative servers (SOA serial match)
- [ ] Internal hostnames / private IPs in public records flagged

**OpSec / restrictions**

> ⚠ AXFR is a single, non-destructive query; still only against in-scope domains' NS  
> ⚠ If a NS is third-party (registrar/DNS provider), the query is acceptable but the finding is the client's config  

**Done when (validation_output):** All record types queried for every domain; AXFR/IXFR result recorded per NS (REFUSED / SUCCESS); SOA serials compared.

**Evidence required:** dig output per record type; AXFR transcript per NS

<details><summary>bars command</summary>

```sh
q() { dig +short +time=3 +tries=2 "$@" | grep -v '^;'; }; for d in $(cat {ext_domains}); do echo "##### DOMAIN $d"; for t in A AAAA MX TXT NS CNAME SOA CAA NAPTR DNSKEY DS; do echo "=== $t $d"; dig +noall +answer +multiline "$d" "$t"; done; for s in _sip._tcp _sip._udp _sips._tcp _xmpp-server._tcp _autodiscover._tcp _ldap._tcp _kerberos._tcp _vpn._tcp _submission._tcp _imaps._tcp; do echo "=== SRV $s.$d"; q SRV "$s.$d"; done; for ns in $(q NS "$d"); do echo "=== SOA@$ns $(q SOA "$d" @"$ns" | awk '{print $3}')"; if dig AXFR "$d" @"$ns" +time=5 | grep -qE '\sIN\s+SOA\s'; then echo "AXFR_SUCCESS $d @$ns"; dig AXFR "$d" @"$ns" | head -200; else echo "AXFR_REFUSED $d @$ns"; fi; s=$(q SOA "$d" @"$ns" | awk '{print $3}'); if dig IXFR=$((s-1)) "$d" @"$ns" +time=5 | grep -qE '\sIN\s+(A|AAAA|CNAME|MX)\s'; then echo "IXFR_SUCCESS $d @$ns"; else echo "IXFR_REFUSED $d @$ns"; fi; done; done; for ip in {inscope_ips}; do [ "$ip" = - ] && continue; echo "PTR $ip $(q -x "$ip")"; done
```

</details>

### P2-S3 — Email Security Records

**Objective:** Check presence and strictness of SPF, DKIM, DMARC, MTA-STS, TLS-RPT and BIMI; non-sending domains must publish null SPF + DMARC reject.

- **scope_required:** true · **severity_hint:** medium · **mode:** automated
- **depends_on:** P2-S2 (MX/TXT)
- **feeds_into:** P3-S4 (SMTP hosts), P6-S1
- **tools (examples):** dig, curl (MTA-STS policy)

**Check criteria**

- [ ] SPF present; qualifier: -all (good) / ~all (weak) / ?all or +all (bad)
- [ ] SPF DNS-lookup count <= 10 (include/a/mx/ptr/exists/redirect)
- [ ] SPF: single record only (multiple = permerror)
- [ ] DKIM selectors discovered (common list + headers from received mail); key length >= 2048 preferred, 1024 = weak
- [ ] DMARC present; p=none / quarantine / reject
- [ ] DMARC rua / ruf reporting addresses, pct (<100 = partial), sp (subdomain policy)
- [ ] MTA-STS TXT + https://mta-sts.<domain>/.well-known/mta-sts.txt mode (enforce/testing/none)
- [ ] TLS-RPT (_smtp._tls) present
- [ ] BIMI (default._bimi) where relevant
- [ ] Non-sending domains (no MX or null MX '0 .'): null SPF 'v=spf1 -all' and DMARC p=reject

**OpSec / restrictions**

> ⚠ DNS lookups + one HTTPS GET to mta-sts host per domain; no mail is sent  

**Done when (validation_output):** Every domain has an SPF/DKIM/DMARC/MTA-STS/TLS-RPT verdict line; weak/missing policies flagged.

**Evidence required:** Raw TXT records per domain and the policy evaluation

<details><summary>bars command</summary>

```sh
q() { dig +short +time=3 +tries=2 "$@" | grep -v '^;'; }; for d in $(cat {ext_domains}); do echo "##### DOMAIN $d"; mx=$(q MX "$d"); echo "MX $d ${mx:-NONE}"; spf=$(q TXT "$d" | tr -d '"' | grep -i '^v=spf1'); n=$(echo "$spf" | grep -c 'v=spf1'); echo "SPF $d count=$n ${spf:-MISSING}"; case "$spf" in *-all*) echo "SPF_POLICY $d HARDFAIL";; *~all*) echo "SPF_POLICY $d SOFTFAIL";; *\?all*|*+all*) echo "SPF_POLICY $d WEAK";; '') echo "SPF_POLICY $d MISSING";; *) echo "SPF_POLICY $d NO_ALL";; esac; echo "SPF_LOOKUPS $d $(echo "$spf" | grep -oiE '(include:|a:|a |mx|ptr|exists:|redirect=)' | wc -l) (top level only; expand includes manually)"; dm=$(q TXT "_dmarc.$d" | tr -d '"'); echo "DMARC $d ${dm:-MISSING}"; case "$dm" in *p=reject*) echo "DMARC_POLICY $d REJECT";; *p=quarantine*) echo "DMARC_POLICY $d QUARANTINE";; *p=none*) echo "DMARC_POLICY $d NONE";; *) echo "DMARC_POLICY $d MISSING";; esac; for s in default selector1 selector2 google k1 k2 mail dkim s1 s2 mxvault zoho smtp pm mandrill everlytickey1 sendgrid; do r=$(q TXT "$s._domainkey.$d" | tr -d '" '); [ -n "$r" ] && echo "DKIM $d selector=$s keylen_b64=$(echo "$r" | sed -n 's/.*p=\([^;]*\).*/\1/p' | wc -c) $r"; done; echo "MTA_STS_TXT $d $(q TXT _mta-sts.$d | tr -d '"')"; echo "MTA_STS_POLICY $d $(curl -s --max-time 10 https://mta-sts.$d/.well-known/mta-sts.txt | tr '\n' ' ')"; echo "TLSRPT $d $(q TXT _smtp._tls.$d | tr -d '"')"; echo "BIMI $d $(q TXT default._bimi.$d | tr -d '"')"; case "$mx" in ''|'0 .') echo "NON_SENDING_CHECK $d spf=$(echo "$spf" | grep -q '^v=spf1 -all$' && echo NULL_OK || echo NOT_NULL) dmarc=$(echo "$dm" | grep -q 'p=reject' && echo REJECT_OK || echo NOT_REJECT)";; esac; done
```

</details>

### P2-S4 — Dangling DNS and Subdomain Takeover Check

**Objective:** Identify CNAME/records pointing to decommissioned cloud/SaaS resources and verify takeover condition WITHOUT claiming the resource.

- **scope_required:** true · **severity_hint:** high · **mode:** automated
- **depends_on:** P2-S1 (CNAME hosts, dangling candidates)
- **feeds_into:** P6-S1
- **tools (examples):** nuclei takeover templates, dig, can-i-take-over-xyz fingerprints (manual)

**Check criteria**

- [ ] Every CNAME host from P2-S1 checked
- [ ] NORES + CNAME (DANGLING_CANDIDATE) entries reviewed manually
- [ ] Provider fingerprint matched (S3 NoSuchBucket, Azure, GitHub Pages, Heroku, Shopify, etc.)
- [ ] Also checked: A records to released cloud IPs, NS delegations to unregistered/expired zones, MX to dead providers
- [ ] Takeover condition verified only by fingerprint/response; resource NOT registered or claimed
- [ ] Provider + fingerprint recorded

**OpSec / restrictions**

> ⚠ NEVER create/claim the bucket, app, page or zone to prove takeover  
> ⚠ One HTTP request per host; rate-limited  

**Done when (validation_output):** Each CNAME host has a verdict (not vulnerable / candidate with provider + fingerprint).

**Evidence required:** Name, CNAME target, provider, fingerprint/response snippet

<details><summary>bars command</summary>

```sh
printf '%s\n' {cname_hosts} | grep -v '^-$' > ext_cname_hosts.txt; nuclei -l ext_cname_hosts.txt -tags takeover -rl {ext_req_rate} -silent -nc; q() { dig +short +time=3 +tries=2 "$@" | grep -v '^;'; }; for h in $(cat ext_cname_hosts.txt); do c=$(q CNAME "$h" | tail -1); [ -n "$c" ] && st=$(dig +noall +comments "$c" | grep -o 'status: [A-Z]*' | head -1) && echo "CNAME_TARGET $h -> $c ($st)"; done
```

</details>

## P3 - Active Port Scanning and Service Audit

_Active. Only P2-S1 in-scope targets. Honor RoE rate limits and test window._

### P3-S1 — Full TCP Port Discovery

**Objective:** Discover every open TCP port (1-65535) on in-scope targets at an RoE-compliant packet rate.

- **scope_required:** true · **severity_hint:** info · **mode:** automated
- **depends_on:** P2-S1 (scan targets), P0-S2 (egress IP logged), P1-S1 (expected ports baseline)
- **feeds_into:** P3-S3, P3-S4, P3-S5, P4-S1
- **tools (examples):** nmap (any SYN scanner)

**Check criteria**

- [ ] All 65535 TCP ports scanned on every SCAN_TARGET
- [ ] Packet rate capped at ext_rate; timing within test window
- [ ] Scan source IP + start/end time recorded (nmap header/footer)
- [ ] Filtered / no-response counts noted separately (Not shown: lines)
- [ ] Sample re-verified with a second pass (manual, different timing) to catch drops
- [ ] Diff vs P1-S1 passive inventory: ports in Shodan but not seen now = filtering/IPS or stale data

**OpSec / restrictions**

> ⚠ SYN scan needs root or CAP_NET_RAW on the host_agent  
> ⚠ --max-rate {ext_rate} -T3; lower ext_rate if IPS blocking or latency rises  
> ⚠ bars drops Excluded hosts from targets automatically; verify none slipped through  
> ⚠ Stop immediately on any sign of service degradation: STOP + notify engagement lead on: out-of-scope asset, signs of production impact, real-user data exposure, active incident at client.  

**Done when (validation_output):** Scan completed for all targets (no 'QUITTING'), open ports per host parsed into bars hosts/services, filtered counts recorded.

**Evidence required:** Scan output with start/end timestamp and egress IP

<details><summary>bars command</summary>

```sh
nmap -sS -Pn -n -p- -T3 --max-rate {ext_rate} --max-retries 2 --open --reason -iL {targets}
```

</details>

### P3-S2 — Critical UDP Port Discovery

**Objective:** Scan critical UDP services (DNS, NTP, SNMP, IKE, IPMI, SSDP, memcached, VPNs, SIP...) on in-scope targets.

- **scope_required:** true · **severity_hint:** medium · **mode:** automated
- **depends_on:** P2-S1 (scan targets)
- **feeds_into:** P3-S3, P3-S4
- **tools (examples):** nmap -sU (with version probes)

**Check criteria**

- [ ] ext_udp_ports scanned on every target
- [ ] open vs open|filtered distinguished (-sV confirms)
- [ ] Amplification-capable services exposed (DNS open resolver, NTP monlist, SSDP, memcached, SNMP) flagged
- [ ] IKE (500/4500) -> note VPN gateway for P4-S1
- [ ] IPMI 623 exposure flagged (should never be internet-facing)

**OpSec / restrictions**

> ⚠ UDP is slow and lossy: keep rate low, --max-retries 1  
> ⚠ Do not run amplification tests (monlist etc.) that generate large responses without approval  

**Done when (validation_output):** UDP scan complete; open services listed with version or marked open|filtered.

**Evidence required:** UDP scan output

<details><summary>bars command</summary>

```sh
nmap -sU -sV --version-intensity 2 -Pn -n -p {ext_udp_ports} -T3 --max-rate {ext_rate} --max-retries 1 --open --reason -iL {targets}
```

</details>

### P3-S3 — Service and Version Detection

**Objective:** Identify product and version on every open port (non-80/443 included) and gather CVE leads per version.

- **scope_required:** true · **severity_hint:** varies · **mode:** automated
- **depends_on:** P3-S1 (open ports, live IPs), P1-S1 (CVE leads)
- **feeds_into:** P3-S4, P4-S1, P4-S4, P5-S2
- **tools (examples):** nmap -sV -sC

**Check criteria**

- [ ] Every open port fingerprinted (product + version)
- [ ] Known critical CVEs researched per identified version (NVD, vendor advisories, CISA KEV)
- [ ] Unidentified services noted for manual banner grab
- [ ] End-of-life software versions flagged
- [ ] bars host tags (ftp, ssh, smb, rdp, smtp, snmp, http...) populated for filtered steps

**OpSec / restrictions**

> ⚠ -sC runs nmap 'default' category scripts (safe/non-intrusive); never add 'intrusive'/'vuln'/'brute' without approval  

**Done when (validation_output):** All open ports carry a service/version (or 'unknown' with manual note); CVE leads list created.

**Evidence required:** Service/version table; CVE research notes per version

<details><summary>bars command</summary>

```sh
nmap -sS -sV -sC -Pn -n -T3 --max-rate {ext_rate} -p $(echo {ports} | tr ' ' '\n' | sort -un | paste -sd, -) -iL {targets}
```

</details>

### P3-S4 — Anonymous Access and Service Hardening Checks

**Objective:** Per-service misconfiguration checks: anonymous/null access, open relay, weak algorithms, missing NLA/signing, default SNMP communities, exposed datastores.

- **scope_required:** true · **severity_hint:** high · **mode:** automated
- **depends_on:** P3-S1, P3-S2, P3-S3
- **feeds_into:** P5-S2, P6-S1
- **tools (examples):** nmap NSE (portrule-driven), netexec / ssh-audit / snmpwalk (manual follow-up)

**Check criteria**

- [ ] FTP: anonymous login (ftp-anon), cleartext auth
- [ ] SMB: exposed to internet at all? SMBv1 enabled? signing not required? null session (smb-protocols, smb2-security-mode)
- [ ] SMTP: open relay (smtp-open-relay), VRFY/EXPN enabled (smtp-commands), STARTTLS offered
- [ ] SSH: weak kex/ciphers/MACs (ssh2-enum-algos), password auth enabled (ssh-auth-methods)
- [ ] RDP: NLA/CredSSP enforced? (rdp-enum-encryption); BlueKeep-era versions
- [ ] SNMP: default communities public/private (snmp-info, manual snmpwalk with RoE-approved list)
- [ ] Databases (MySQL, MSSQL, PostgreSQL, Oracle, MongoDB, Redis, Elasticsearch, Memcached): internet-exposed at all? unauthenticated access?
- [ ] Telnet / VNC / rsync / NFS / LDAP exposure (vnc-info, rsync-list-modules, nfs-showmount, ldap-rootdse)
- [ ] Each issue confirmed by a second, manual method before reporting

**OpSec / restrictions**

> ⚠ Only non-intrusive NSE scripts; no brute-force scripts  
> ⚠ smtp-open-relay issues MAIL FROM/RCPT TO only (no DATA); RoE must allow it  
> ⚠ SNMP: only 'public' tried automatically; wider community lists need approval (lockout/alerting risk)  

**Done when (validation_output):** Every exposed service family has a pass/fail verdict per host:port; positives cross-verified.

**Evidence required:** Script output per host:port

<details><summary>bars command</summary>

```sh
nmap -sS -sV -Pn -n -T3 --max-rate {ext_rate} -p $(echo {ports} | tr ' ' '\n' | sort -un | paste -sd, -) --script "ftp-anon,ftp-syst,smb-protocols,smb2-security-mode,smb-security-mode,smtp-open-relay,smtp-commands,ssh2-enum-algos,ssh-auth-methods,rdp-enum-encryption,rdp-ntlm-info,vnc-info,rsync-list-modules,nfs-showmount,ldap-rootdse,mysql-info,ms-sql-info,mongodb-info,redis-info,memcached-info,telnet-encryption" -iL {targets}; nmap -sU -Pn -n -p 161 --script snmp-info -iL {targets}
```

</details>

### P3-S5 — TLS Configuration Review

**Objective:** Review protocol versions, cipher suites and certificates on ALL TLS-enabled services (not only 443; includes STARTTLS on SMTP/IMAP/POP3/FTP/LDAP).

- **scope_required:** true · **severity_hint:** medium · **mode:** automated
- **depends_on:** P3-S1 (open ports), P3-S3 (ssl services)
- **feeds_into:** P6-S1
- **tools (examples):** nmap ssl-enum-ciphers + ssl-cert, testssl.sh (per-host deep dive)

**Check criteria**

- [ ] SSLv2 / SSLv3 / TLS 1.0 / TLS 1.1 disabled
- [ ] TLS 1.2 + 1.3 offered
- [ ] No NULL / EXPORT / RC4 / 3DES / CBC-only weak suites (ssl-enum-ciphers grade < A flagged)
- [ ] Certificate valid (not expired, not yet valid), full chain served
- [ ] Hostname matches CN/SAN
- [ ] Expiry within 30 days flagged
- [ ] Key: RSA >= 2048 / EC >= 256; signature not SHA-1/MD5
- [ ] Self-signed or internal-CA certs on public services flagged
- [ ] STARTTLS services covered (SMTP 25/587, IMAP 143, POP3 110, FTP 21, LDAP 389)

**OpSec / restrictions**

> ⚠ Handshake-only checks; no heartbleed/renegotiation stress tests without approval  

**Done when (validation_output):** Every TLS-capable host:port has protocol list, least-strength grade and certificate validity recorded.

**Evidence required:** Cipher/protocol grades and certificate details per host:port

<details><summary>bars command</summary>

```sh
nmap -sS -sV -Pn -n -T3 --max-rate {ext_rate} -p $(echo {ports} | tr ' ' '\n' | sort -un | paste -sd, -) --script ssl-cert,ssl-enum-ciphers -iL {targets}
```

</details>

## P4 - Web and Management Panels (External View)

_External attack surface only, not a full web application test._

### P4-S1 — Web and Management Interface Discovery

**Objective:** Probe every in-scope name/IP on discovered + common web ports; identify devices, IoT, firewall/VPN, remote-access, CI/CD, monitoring panels and API docs.

- **scope_required:** true · **severity_hint:** info · **mode:** automated
- **depends_on:** P2-S1 (in-scope names), P3-S1 (open ports, live IPs), P3-S3 (service names)
- **feeds_into:** P4-S2, P4-S3, P4-S4, P5-S1
- **tools (examples):** httpx (ProjectDiscovery; Kali: httpx-toolkit), screenshot tool (manual: gowitness/aquatone)

**Check criteria**

- [ ] IP cameras / NVR / DVR
- [ ] Firewall / UTM panels (FortiGate, Palo Alto, Sophos, pfSense, Check Point)
- [ ] VPN gateways / SSL-VPN portals (FortiGate SSL-VPN, GlobalProtect, Pulse/Ivanti, Cisco ASA AnyConnect)
- [ ] Printers / MFPs
- [ ] Server management (iDRAC, iLO, IPMI web, Webmin, cPanel, Plesk, vCenter/ESXi)
- [ ] Remote access portals (Citrix NetScaler/Gateway, OWA/Exchange, RD Web/RD Gateway)
- [ ] CI/CD and repos (Jenkins, GitLab, TeamCity, Argo, Harbor)
- [ ] Monitoring / logging (Grafana, Kibana, Zabbix, Nagios, Prometheus)
- [ ] API docs exposed (Swagger/OpenAPI UI, GraphQL endpoint + introspection)
- [ ] Both IP and hostname (vhost) probed: different vhosts can expose different apps

**OpSec / restrictions**

> ⚠ Rate-limited (ext_req_rate); GET only  
> ⚠ Names resolving OUT of scope were already removed in P2-S1; do not add them back  

**Done when (validation_output):** Every in-scope name + live IP probed on discovered+common web ports; WEB_URL list produced; panels categorized.

**Evidence required:** URL list with status, title, server, tech; screenshots of panels

<details><summary>bars command</summary>

```sh
printf '%s\n' {inscope_names} {live_ips} | grep -v '^-$' | sort -u > ext_web_hosts.txt; P=$(echo {ports} {ext_web_ports} | tr ' ,' '\n\n' | sort -un | paste -sd, -); httpx-toolkit -l ext_web_hosts.txt -p "$P" -sc -title -server -td -ip -fr -rl {ext_req_rate} -silent -nc
```

</details>

### P4-S2 — Panel Fingerprint and API Docs Exposure

**Objective:** Fingerprint detected panels/products precisely (vendor, product, version) and detect exposed API documentation / GraphQL introspection.

- **scope_required:** true · **severity_hint:** low-medium · **mode:** automated
- **depends_on:** P4-S1 (web URLs)
- **feeds_into:** P4-S3, P4-S4
- **tools (examples):** nuclei (panel, tech, swagger, graphql templates)

**Check criteria**

- [ ] Product + version extracted for each panel
- [ ] Swagger/OpenAPI UI or spec reachable without auth
- [ ] GraphQL introspection enabled
- [ ] Panel ownership confirmed (client vs third-party SaaS) before any login attempt

**OpSec / restrictions**

> ⚠ Detection templates only (panel/tech/exposure); intrusive/dos/fuzz/default-login excluded  

**Done when (validation_output):** Each WEB_URL has a fingerprint result or 'unknown'; API doc exposures listed.

**Evidence required:** Template match lines per URL

<details><summary>bars command</summary>

```sh
printf '%s\n' {web_urls} > ext_web_urls.txt; nuclei -l ext_web_urls.txt -tags panel,tech,swagger,graphql,exposure -etags {ext_nuclei_exclude_tags} -rl {ext_req_rate} -silent -nc
```

</details>

### P4-S3 — Default Credentials and Lockout Check

**Objective:** Check whether detected panels still accept documented VENDOR DEFAULT credentials, only after confirming lockout/blocking behaviour.

- **scope_required:** true · **severity_hint:** critical · **mode:** manual
- **depends_on:** P4-S2 (panel fingerprints), P1-S7 (only with written approval)
- **feeds_into:** P6-S1
- **tools (examples):** browser (manual), vendor documentation / default-credential lists

**Check criteria**

- [ ] Lockout / IP-blocking policy checked BEFORE first attempt (docs, login page hints, CAPTCHA, prior response headers)
- [ ] If page shows 'Remaining attempts: N' or similar counter: STOP, no attempts
- [ ] If 'admin' (or target username) is a real administrator's personal account: STOP, no attempts
- [ ] Only documented vendor defaults for the identified product/version (max 1-2 tries per account)
- [ ] No brute force / password spraying unless RoE explicitly allows it
- [ ] Every attempt logged (time, URL, user, source, result)
- [ ] On success: do not change settings, screenshot landing page only, log out, notify lead

**OpSec / restrictions**

> ⚠ CRITICAL: account lockout of a production admin = engagement incident  
> ⚠ Automated default-login templates are excluded by default (ext_nuclei_exclude_tags); remove only with written approval  
> ⚠ STOP + notify engagement lead on: out-of-scope asset, signs of production impact, real-user data exposure, active incident at client.  

**Done when (validation_output):** Every fingerprinted panel has: lockout assessment, attempt log (or documented reason for no attempt), verdict.

**Evidence required:** Attempt log: UTC time, URL, username, credential source (vendor doc), result, lockout indicators

### P4-S4 — Version-Based Critical Vulnerability Check

**Objective:** Verify whether identified web servers, panels, VPNs and devices carry critical unauthenticated vulnerabilities (RCE, auth bypass, KEV) using non-destructive checks.

- **scope_required:** true · **severity_hint:** critical · **mode:** automated
- **depends_on:** P3-S3 (versions, CVE leads), P4-S1 (web URLs), P4-S2 (panel versions)
- **feeds_into:** P5-S2, P6-S1
- **tools (examples):** nuclei (kev, cve; safe templates), vendor advisories, manual version/behaviour checks

**Check criteria**

- [ ] Each identified product/version mapped to CISA KEV and vendor critical advisories
- [ ] Unauthenticated RCE / auth bypass candidates listed
- [ ] Verification via version/behaviour or vendor-safe PoC only (no state change, no command execution)
- [ ] Confidence recorded: confirmed / likely (version only) / unverified
- [ ] Critical confirmed issues escalated to lead immediately

**OpSec / restrictions**

> ⚠ No payloads that alter state or run commands without explicit written approval  
> ⚠ Exclude intrusive/dos templates; KEV-class edge devices (VPN/firewall) are fragile, watch for impact  
> ⚠ STOP + notify engagement lead on: out-of-scope asset, signs of production impact, real-user data exposure, active incident at client.  

**Done when (validation_output):** Every product/version has a CVE verdict with method + confidence; criticals escalated.

**Evidence required:** Version evidence + non-destructive verification output per CVE

<details><summary>bars command</summary>

```sh
printf '%s\n' {web_urls} > ext_web_urls.txt; nuclei -l ext_web_urls.txt -tags kev,cve,rce,unauth,auth-bypass -severity critical,high -etags {ext_nuclei_exclude_tags} -rl {ext_req_rate} -silent -nc
```

</details>

### P4-S5 — Archived Endpoint Liveness Check

**Objective:** Check whether interesting archived/dorked URLs (P1-S3/P1-S4) still respond today, only on in-scope hosts.

- **scope_required:** true · **severity_hint:** medium · **mode:** automated
- **depends_on:** P1-S4 (archive interesting URLs), P2-S1 (in-scope names)
- **feeds_into:** P5-S2, P6-S1
- **tools (examples):** curl

**Check criteria**

- [ ] Only URLs whose host is an INSCOPE_NAME from P2-S1 tested
- [ ] Status code + size recorded per URL
- [ ] 200 on config/backup/dump/env/key paths escalated
- [ ] Content of secrets NOT downloaded/stored; HEAD or ranged GET only

**OpSec / restrictions**

> ⚠ HEAD requests, 1 req/s; body never saved  
> ⚠ Out-of-scope hosts in archive list are skipped and logged  

**Done when (validation_output):** Every in-scope interesting URL has a status line; live sensitive files escalated.

**Evidence required:** URL, HTTP status, content-length, content-type (no sensitive body stored)

<details><summary>bars command</summary>

```sh
python3 - {archive_urls} {inscope_names} <<'PY'
import sys, subprocess, time, urllib.parse
ok = set(open(sys.argv[2]).read().split())
for u in sorted(set(open(sys.argv[1]).read().split())):
    h = (urllib.parse.urlsplit(u).hostname or '').lower()
    if h not in ok:
        print('SKIP_OUT_OF_SCOPE', u); continue
    code = subprocess.run(['curl', '-skI', '-o', '/dev/null', '--max-time', '10', '-w', '%' + chr(123) + 'http_code' + chr(125), u], capture_output=True, text=True).stdout.strip()
    print('LIVE' if code == '200' else 'STATUS', code, u)
    time.sleep(1)
PY
```

</details>

## P5 - Automated Vulnerability Scanning and FP Validation

_Automated scanners plus manual verification. Only verified findings go to reporting._

### P5-S1 — Nuclei Vulnerability Scan

**Objective:** Scan the full external web surface with current templates (exposures, misconfigurations, CVEs, panels) and classify findings.

- **scope_required:** true · **severity_hint:** varies · **mode:** automated
- **depends_on:** P4-S1 (web URLs)
- **feeds_into:** P5-S2, P6-S1
- **tools (examples):** nuclei

**Check criteria**

- [ ] Templates updated before run (nuclei -ut); engine + template version recorded (printed at top of output)
- [ ] Run date recorded
- [ ] Categories covered: exposures, misconfiguration, cves, exposed-panels, technologies
- [ ] default-logins category only with written approval (excluded by default)
- [ ] Intrusive / DoS / fuzz templates excluded unless approved
- [ ] Each match classified: true positive / false positive / needs manual check (into P5-S2)

**OpSec / restrictions**

> ⚠ Rate-limited (ext_req_rate)  
> ⚠ Stop on WAF block storms or service errors: STOP + notify engagement lead on: out-of-scope asset, signs of production impact, real-user data exposure, active incident at client.  

**Done when (validation_output):** Scan finished for all WEB_URLs with recorded template version; every match triaged in P5-S2.

**Evidence required:** Nuclei + template version, run date, JSON/line output

<details><summary>bars command</summary>

```sh
echo "RUN_DATE $(date -u +%Y-%m-%dT%H:%M:%SZ)"; nuclei -version 2>&1 | tail -1; nuclei -tv 2>&1 | tail -1; printf '%s\n' {web_urls} > ext_web_urls.txt; nuclei -l ext_web_urls.txt -etags {ext_nuclei_exclude_tags} -rl {ext_req_rate} -c 10 -silent -nc
```

</details>

### P5-S2 — Nessus Triage and False-Positive Validation

**Objective:** Manually verify every Nessus (and carried-over) finding; drop false positives and banner/version-only guesses that cannot be verified.

- **scope_required:** true · **severity_hint:** varies · **mode:** manual
- **depends_on:** Nessus scan of P2-S1 targets, P3-S3, P3-S4, P4-S4, P5-S1
- **feeds_into:** P6-S1
- **tools (examples):** Nessus CSV export, python3 triage helper, manual verification

**Check criteria**

- [ ] Nessus policy: safe checks ON, scan source/time recorded, same rate limits as P3
- [ ] Every Critical/High/Medium finding reviewed
- [ ] Findings relying only on banner/version (VERSION_ONLY) re-verified by behaviour or backport check
- [ ] False positives removed with reason
- [ ] Retained findings: verification method + evidence + confidence recorded
- [ ] Only genuine, in-scope, rule-compliant risks carried to reporting

**OpSec / restrictions**

> ⚠ Verification must stay non-destructive; same approval rules as P4-S4  

**Done when (validation_output):** Every finding has verdict TP/FP + method + confidence; FP list with reasons kept.

**Evidence required:** Per retained finding: verification method, evidence, confidence (confirmed / likely / unverified)

<details><summary>bars command</summary>

```sh
python3 - '{ext_nessus_export}' <<'PY'
import sys, csv, re
rx = re.compile(r'(installed version|reported version|fixed version|banner|version :)', re.I)
for r in csv.DictReader(open(sys.argv[1], newline='', encoding='utf-8', errors='replace')):
    risk = r.get('Risk') or r.get('Severity') or ''
    if risk not in ('Critical', 'High', 'Medium'): continue
    tag = 'VERSION_ONLY' if rx.search(r.get('Plugin Output', '')) else 'BEHAVIOUR'
    print('VERIFY', risk, '%s:%s' % (r.get('Host'), r.get('Port')), tag, r.get('Plugin ID'), r.get('CVE', '') or '-', '|', r.get('Name'))
PY
```

</details>

## P6 - Consolidation and Reporting Hand-off

_Merge, dedupe, score, evidence, remediation, exceptions log._

### P6-S1 — Consolidation and Reporting Hand-off

**Objective:** Merge and deduplicate findings across phases, score, attach redacted evidence and remediation, and document every scope exception and OpSec incident.

- **scope_required:** false · **severity_hint:** n/a · **mode:** manual
- **depends_on:** P0-S1..P5-S2 (all)
- **feeds_into:** Report
- **tools (examples):** bars Findings, report template

**Check criteria**

- [ ] Findings merged and deduplicated across phases, mapped to asset + step ID
- [ ] Severity assigned (CVSS v3.1/v4 or agreed rubric) with business-impact context
- [ ] Evidence attached, redacted for PII and secrets, with reproduction steps
- [ ] Remediation recommendation per finding
- [ ] Out-of-scope hits (P2-S1 OUT list, OFFSCOPE names) documented as observations, untested
- [ ] Stopped tests, lockout events and OpSec incidents documented with time + reason
- [ ] Skipped / n/a steps listed with justification
- [ ] Temporary data (scan files, PII) handled per data handling plan (retention/deletion)

**OpSec / restrictions**

> ⚠ Report contains no plaintext credentials or unredacted PII  

**Done when (validation_output):** Every retained finding has asset, step ID, severity, evidence, remediation; exceptions log complete; lead sign-off.

**Evidence required:** Final findings list; exceptions/incident log
