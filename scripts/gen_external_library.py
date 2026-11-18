#!/usr/bin/env python3
"""Generate the bars External Network Assessment library (JSON) + Markdown checklist.

Single source of truth: STEPS below. Output JSON follows the existing
/api/v1/library/export schema exactly (checklists.groups[].items[], flows.flows[],
variables.variables[]); the extra checklist metadata (step_id, depends_on,
feeds_into, opsec, validation, ...) lives inside `description` + `tags`.

Usage: python3 scripts/gen_external_library.py external-network-library.json docs/external-network-checklist.md
"""
import json
import sys

# ---------------------------------------------------------------- variables
# All keys prefixed ext_ : library import upserts by key, so never reuse the
# internal library's keys (scope, targets, domain, ...).
VARIABLES = [
    ("ext_roe_ref", "", "string", "Signed RoE / authorization reference (doc ID + signer + date). Empty = gate fails."),
    ("ext_window_start", "", "string", "Test window start, ISO 8601 with offset, e.g. 2026-11-20T09:00:00+03:00"),
    ("ext_window_end", "", "string", "Test window end, ISO 8601 with offset, e.g. 2026-11-28T18:00:00+03:00"),
    ("ext_domain", "", "string", "Primary in-scope apex domain (e.g. example.com)"),
    ("ext_domains", "", "file", "All in-scope apex domains, one per line"),
    ("ext_scope", "", "file", "In-scope IPs/CIDRs (IPv4/IPv6), one per line. Out-of-scope assets go to bars Excluded hosts, never here."),
    ("ext_asn", "", "string", "In-scope ASN(s), space separated, e.g. AS64500 AS64501 (optional)"),
    ("ext_rate", "300", "string", "Max nmap packets/sec (RoE rate limit)"),
    ("ext_req_rate", "10", "string", "Max HTTP requests/sec for httpx/nuclei (RoE rate limit)"),
    ("ext_udp_ports", "53,67,69,123,137,161,500,623,1194,1900,3478,4500,5060,5353,11211,51820", "string", "Critical UDP ports for P3-S2"),
    ("ext_web_ports", "80,81,443,591,2082,2083,2086,2087,3000,4443,5000,5601,7001,7443,8000,8008,8080,8081,8088,8443,8834,8888,9000,9090,9200,9443,10000,10443", "string", "Fallback web ports probed in P4-S1 in addition to discovered open ports"),
    ("ext_nuclei_exclude_tags", "dos,intrusive,fuzz,brute-force,default-login,bruteforce", "string", "Nuclei tags excluded unless RoE approves (remove default-login only with written approval)"),
    ("ext_nessus_export", "", "string", "Path (on the host_agent machine) to the Nessus CSV export for P5-S2"),
]

IP_RE = r"((?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3})"

MANUAL_CMD = "echo 'MANUAL STEP {sid}: work through CHECK CRITERIA in the item description, attach evidence to the execution/finding, then mark done.'"

# ---------------------------------------------------------------- phases
PHASES = [
    ("P0", "EXT P0 - Pre-Engagement and Scope Gate",
     "Must be complete before any Phase 1 activity. Confirms authorization, scope, windows, rate limits and data handling."),
    ("P1", "EXT P1 - Passive OSINT and Digital Footprint",
     "No packets to target assets. Third-party data sources only. Results may be stale: record timestamps."),
    ("P2", "EXT P2 - DNS Configuration and Infrastructure",
     "DNS queries against public resolvers / authoritative NS of in-scope domains. Classifies every asset IN/OUT of scope before active testing."),
    ("P3", "EXT P3 - Active Port Scanning and Service Audit",
     "Active. Only P2-S1 in-scope targets. Honor RoE rate limits and test window."),
    ("P4", "EXT P4 - Web and Management Panels (External View)",
     "External attack surface only, not a full web application test."),
    ("P5", "EXT P5 - Automated Vulnerability Scanning and FP Validation",
     "Automated scanners plus manual verification. Only verified findings go to reporting."),
    ("P6", "EXT P6 - Consolidation and Reporting Hand-off",
     "Merge, dedupe, score, evidence, remediation, exceptions log."),
]

GLOBAL_STOP = ("STOP + notify engagement lead on: out-of-scope asset, signs of production impact, "
               "real-user data exposure, active incident at client.")

# ---------------------------------------------------------------- steps
# Keys:
#  sid, phase, name, objective, depends_on, feeds_into, tools, scope_required,
#  severity_hint, evidence_required, checks, opsec, validation, cmd, regex,
#  params, timeout, alerts, finding, tags, manual, flow_inputs, on_failure,
#  passive_flow (bool: include in passive flow)
STEPS = []


def step(**kw):
    kw.setdefault("regex", {})
    kw.setdefault("params", {})
    kw.setdefault("timeout", 3600)
    kw.setdefault("alerts", {})
    kw.setdefault("finding", {})
    kw.setdefault("tags", [])
    kw.setdefault("manual", False)
    kw.setdefault("flow_inputs", {})
    kw.setdefault("on_failure", "continue")
    kw.setdefault("passive_flow", False)
    kw.setdefault("depends_on", [])
    kw.setdefault("feeds_into", [])
    if kw["manual"]:
        kw["cmd"] = MANUAL_CMD.replace("{sid}", kw["sid"])
    STEPS.append(kw)


# ============================== P0 ==============================
step(
    sid="P0-S1", phase="P0", name="EXT P0-S1 Authorization and Scope Gate",
    objective="Hard gate: refuse to proceed unless RoE reference, test window and in-scope lists are present and valid.",
    depends_on=[], feeds_into=["P0-S2", "P0-S3", "P1-*", "P2-S1"],
    tools=["sh", "python3 (ipaddress)"], scope_required=False, severity_hint="n/a",
    evidence_required="Gate output (SCOPE_GATE_OK + normalized scope list), RoE document reference",
    checks=[
        "Written authorization / RoE signed; reference stored in ext_roe_ref",
        "In-scope domains (ext_domains), IP ranges (ext_scope) and ASNs (ext_asn) listed",
        "Out-of-scope assets listed explicitly AND added to the bars project as Excluded hosts",
        "Testing window (ext_window_start/end), rate limits (ext_rate, ext_req_rate) and emergency contact confirmed",
        "Third-party hosted assets (cloud, CDN, SaaS, MSP) identified; provider permission/policy checked",
        "Data handling plan for PII/credentials (storage, encryption, retention) agreed",
        "Out-of-scope hit rule acknowledged: stop, log, report to engagement lead, never test",
    ],
    opsec=[
        "Flow step on_failure=stop: nothing downstream runs if the gate fails",
        "Gate checks the clock: re-run at the start of every testing day",
        GLOBAL_STOP,
    ],
    validation="Output contains SCOPE_GATE_OK, window state INSIDE, every scope line parsed as valid IP/CIDR, and the domain list is non-empty.",
    cmd=(
        "python3 - {ext_scope} {ext_domains} '{ext_roe_ref}' '{ext_window_start}' '{ext_window_end}' <<'PY'\n"
        "import sys, ipaddress, datetime\n"
        "scope, doms, roe, ws, we = sys.argv[1:6]\n"
        "ok = True\n"
        "if not roe.strip():\n"
        "    print('FAIL roe: ext_roe_ref empty'); ok = False\n"
        "now = datetime.datetime.now(datetime.timezone.utc)\n"
        "try:\n"
        "    s = datetime.datetime.fromisoformat(ws); e = datetime.datetime.fromisoformat(we)\n"
        "    state = 'INSIDE' if s <= now <= e else 'OUTSIDE'\n"
        "    print('WINDOW %s %s -> %s now=%s' % (state, ws, we, now.isoformat()))\n"
        "    ok = ok and state == 'INSIDE'\n"
        "except Exception as x:\n"
        "    print('FAIL window: %s' % x); ok = False\n"
        "nets = []\n"
        "for line in open(scope).read().split():\n"
        "    try:\n"
        "        nets.append(ipaddress.ip_network(line, strict=False)); print('SCOPE_NET %s' % line)\n"
        "    except ValueError:\n"
        "        print('FAIL scope line: %s' % line); ok = False\n"
        "d = [x.strip().lower() for x in open(doms).read().split() if x.strip()]\n"
        "for x in d: print('SCOPE_DOMAIN %s' % x)\n"
        "if not nets and not d:\n"
        "    print('FAIL scope: no networks and no domains'); ok = False\n"
        "print('ADDRESSES_IN_SCOPE %d' % sum(n.num_addresses for n in nets))\n"
        "print('SCOPE_GATE_OK' if ok else 'SCOPE_GATE_FAIL')\n"
        "sys.exit(0 if ok else 1)\n"
        "PY"
    ),
    regex={"gate_status": r"^(SCOPE_GATE_OK|SCOPE_GATE_FAIL)$",
           "scope_nets": r"^SCOPE_NET (\S+)$",
           "scope_domains": r"^SCOPE_DOMAIN (\S+)$"},
    params={"ext_scope": {"type": "file"}, "ext_domains": {"type": "file"}},
    timeout=60, alerts={"critical": ["SCOPE_GATE_FAIL"]},
    tags=["external", "ext-p0", "gate", "scope"], on_failure="stop", passive_flow=True,
)
step(
    sid="P0-S2", phase="P0", name="EXT P0-S2 Egress IP and Engagement Log",
    objective="Record the scan source (egress) IP and UTC start time so the client SOC can correlate and whitelist/attribute traffic.",
    depends_on=["P0-S1"], feeds_into=["P3-S1", "P6-S1"],
    tools=["curl", "date"], scope_required=False, severity_hint="n/a",
    evidence_required="Egress IP + timestamp shared with client contact",
    checks=[
        "Egress IP recorded and shared with client contact / SOC per RoE",
        "Emergency contact phone/email verified reachable",
        "Daily start/stop times logged (re-run each day)",
    ],
    opsec=["Run from the same host_agent that will scan; a different egress invalidates SOC correlation",
           "Uses a third-party IP echo service (api.ipify.org); swap if RoE forbids"],
    validation="EGRESS_IP line present with a valid IP and UTC timestamp.",
    cmd="echo \"UTC_START $(date -u +%Y-%m-%dT%H:%M:%SZ)\"; echo \"EGRESS_IP $(curl -s --max-time 15 https://api.ipify.org)\"",
    regex={"egress_ip": r"^EGRESS_IP (\S+)$"},
    timeout=60, tags=["external", "ext-p0", "opsec"], passive_flow=True,
)
step(
    sid="P0-S3", phase="P0", name="EXT P0-S3 ASN Prefix Ownership Cross-Check",
    objective="Compare prefixes announced by the in-scope ASN(s) with ext_scope; anything announced but not listed needs an explicit scope decision.",
    depends_on=["P0-S1"], feeds_into=["P0-S1 (scope update)", "P1-S1"],
    tools=["whois (RADb)", "python3"], scope_required=False, severity_hint="info",
    evidence_required="Prefix diff (ANNOUNCED_NOT_IN_SCOPE list) and lead's decision",
    checks=[
        "Every ANNOUNCED_NOT_IN_SCOPE prefix sent to engagement lead for decision",
        "Every IN_SCOPE_NOT_ANNOUNCED range verified as really client-owned (WHOIS/RIR)",
        "ext_scope updated only after written confirmation",
    ],
    opsec=["Passive (RADb whois). Never add a prefix to scope on your own judgement"],
    validation="Diff produced; each ANNOUNCED_NOT_IN_SCOPE prefix has a recorded decision (add / exclude).",
    cmd=(
        "for a in {ext_asn}; do whois -h whois.radb.net -- \"-i origin $a\"; done | awk '/^route6?:/ {print $2}' | sort -u > ext_asn_prefixes.txt; "
        "python3 - ext_asn_prefixes.txt {ext_scope} <<'PY'\n"
        "import sys, ipaddress\n"
        "ann = set(ipaddress.ip_network(x, strict=False) for x in open(sys.argv[1]).read().split())\n"
        "sc = set(ipaddress.ip_network(x, strict=False) for x in open(sys.argv[2]).read().split())\n"
        "for n in sorted(ann, key=str):\n"
        "    tag = 'COVERED' if any(n.version == s.version and n.subnet_of(s) for s in sc) else 'ANNOUNCED_NOT_IN_SCOPE'\n"
        "    print(tag, n)\n"
        "for s in sorted(sc, key=str):\n"
        "    if not any(s.version == n.version and s.subnet_of(n) for n in ann):\n"
        "        print('IN_SCOPE_NOT_ANNOUNCED', s)\n"
        "PY"
    ),
    regex={"announced_not_in_scope": r"^ANNOUNCED_NOT_IN_SCOPE (\S+)$"},
    params={"ext_scope": {"type": "file"}},
    timeout=300, alerts={"warning": ["ANNOUNCED_NOT_IN_SCOPE"]},
    tags=["external", "ext-p0", "scope", "asn"], passive_flow=True,
)

# ============================== P1 ==============================
step(
    sid="P1-S1", phase="P1", name="EXT P1-S1 Internet Asset Inventory",
    objective="Passive inventory of in-scope IPs from internet-scan datasets: open ports, banners/CPEs, hostnames, known CVE matches.",
    depends_on=["P0-S1"], feeds_into=["P3-S1 (expected ports baseline)", "P3-S3 (CVE leads)", "P2-S1 (hostnames)"],
    tools=["Shodan InternetDB (keyless)", "Shodan/Censys/FOFA/BinaryEdge UI or API (manual cross-check)"],
    scope_required=False, severity_hint="info",
    evidence_required="Per-IP HIT lines with fetch timestamp; screenshots of Censys/FOFA discrepancies",
    checks=[
        "Current open ports per IP recorded",
        "Historical ports/services checked in Shodan host history (manual, API key)",
        "Service banners / CPEs recorded",
        "Known CVE matches (vulns) recorded as leads only, not findings",
        "Cross-checked with Censys / FOFA / BinaryEdge-style sources; discrepancies noted",
        "Each result's fetch timestamp recorded (datasets can be weeks old)",
        "Hostnames found here added to the P2-S1 candidate list if under in-scope domains",
    ],
    opsec=["Passive: queries go to the data provider, not the target",
           "Expansion capped at 4096 addresses; query larger ranges via provider net: search instead",
           "Dataset CVEs are version-based guesses: never report without P3/P5 verification"],
    validation="Every in-scope IP (up to cap) queried; HIT lines list ports/cpes/vulns with timestamp; cross-check notes present.",
    cmd=(
        "python3 - {ext_scope} <<'PY'\n"
        "import sys, ipaddress, json, time, urllib.request, datetime, itertools\n"
        "ips = []\n"
        "for x in open(sys.argv[1]).read().split():\n"
        "    n = ipaddress.ip_network(x, strict=False)\n"
        "    ips += [str(a) for a in itertools.islice(n.hosts() if n.num_addresses > 2 else iter(n), 4096 - len(ips))]\n"
        "    if len(ips) >= 4096: break\n"
        "for ip in ips[:4096]:\n"
        "    try:\n"
        "        d = json.load(urllib.request.urlopen('https://internetdb.shodan.io/' + ip, timeout=15))\n"
        "        ts = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')\n"
        "        print('HIT', ip, 'fetched=' + ts, 'ports=' + ','.join(map(str, d.get('ports', []))), 'cpes=' + ','.join(d.get('cpes', [])), 'hostnames=' + ','.join(d.get('hostnames', [])), 'vulns=' + ','.join(d.get('vulns', [])))\n"
        "        for h in d.get('hostnames', []): print('HOSTNAME', h)\n"
        "    except Exception:\n"
        "        pass\n"
        "    time.sleep(1)\n"
        "PY"
    ),
    regex={"shodan_ips": r"^HIT (\S+)", "shodan_cves": r"(CVE-\d{4}-\d{4,7})", "shodan_hostnames": r"^HOSTNAME (\S+)$"},
    params={"ext_scope": {"type": "file"}},
    timeout=7200, alerts={"warning": [r"vulns=CVE-"]},
    tags=["external", "ext-p1", "osint", "passive"], passive_flow=True,
)
step(
    sid="P1-S2", phase="P1", name="EXT P1-S2 WHOIS and Registrant PII Review",
    objective="Review domain and IP registration data; flag personal contact data (not generic role addresses) and pivot on registrant org to find related domains.",
    depends_on=["P0-S1"], feeds_into=["P0-S1 (related domains to confirm)", "P1-S7 (employee identities)", "P6-S1"],
    tools=["whois", "WHOIS history services (manual)", "reverse WHOIS (manual)"],
    scope_required=False, severity_hint="low",
    evidence_required="Redacted WHOIS excerpt for every PII hit",
    checks=[
        "Domain WHOIS reviewed for every in-scope domain",
        "IP/RIR WHOIS reviewed for every in-scope network (org, abuse contact, admin-c/tech-c)",
        "Generic role addresses (info@, iletisim@, noc@, abuse@, hostmaster@) treated as normal",
        "Personal email (free-mail or name.surname@), mobile numbers, employee names flagged",
        "WHOIS history checked for past registrant data leaks (manual)",
        "Registrant-organization / email pivots run; related domains sent to lead for scope decision",
    ],
    opsec=["Passive. Store PII only in redacted form per data handling plan",
           "Related domains found by pivot are OUT of scope until the lead confirms"],
    validation="WHOIS reviewed for all domains and networks; PII hits listed (redacted); related-domain list handed to lead.",
    cmd=(
        "for d in $(cat {ext_domains}); do echo \"=== DOMAIN $d\"; whois \"$d\"; sleep 2; done; "
        "for n in $(cat {ext_scope}); do echo \"=== NET $n\"; whois \"${n%/*}\"; sleep 2; done"
    ),
    regex={"whois_emails": r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
           "registrant_org": r"^\s*(?:Registrant Organization|org-name|OrgName):\s*(.+)$"},
    params={"ext_domains": {"type": "file"}, "ext_scope": {"type": "file"}},
    timeout=1800,
    alerts={"warning": [r"@(gmail|hotmail|outlook|yahoo|yandex|icloud|proton(mail)?|live|msn)\.", r"\+90\s?5\d{2}"]},
    finding={"title": "Personal Contact Data Exposed in WHOIS / RIR Records", "severity": "low", "cwe": "CWE-359",
             "remediation": "Use role-based contacts (e.g. hostmaster@, noc@) and registrar privacy where allowed; update RIR admin-c/tech-c objects to role objects.",
             "references": ["https://www.ripe.net/manage-ips-and-asns/db/support/documentation/ripe-database-documentation/rpsl-object-types/4-2-descriptions-of-primary-objects/4-2-7-description-of-the-role-object"]},
    tags=["external", "ext-p1", "osint", "passive", "pii"], passive_flow=True,
)

DORK_EXTS = ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "sql", "txt", "env", "log",
             "bak", "old", "orig", "save", "conf", "cfg", "ini", "xml", "json", "yml", "yaml",
             "zip", "tar", "gz", "7z", "rar", "pem", "key", "pfx"]
DORK_EXTRA = [
    ("dirlist", 'intitle:"index of"'),
    ("login", "inurl:login OR inurl:admin OR inurl:signin OR intitle:login"),
    ("errors", '"stack trace" OR "Traceback (most recent call last)" OR "Warning: mysql" OR "SQLSTATE" OR "Exception in thread"'),
    ("vcs", "inurl:.git OR inurl:.svn OR inurl:.DS_Store OR inurl:.hg"),
    ("config", 'inurl:wp-config OR inurl:web.config OR inurl:phpinfo OR intitle:phpinfo'),
    ("docs-internal", '"internal use only" OR "confidential" OR "gizli" OR "hizmete özel"'),
]
step(
    sid="P1-S3", phase="P1", name="EXT P1-S3 Search Engine Dorking Google and Bing",
    objective="Generate and manually run Google and Bing dork sets per in-scope domain to find forgotten documents, listings and sensitive exposures.",
    depends_on=["P0-S1", "P1-S6 (optional: re-run on key subdomains)"], feeds_into=["P1-S7 (secrets leads)", "P4-S5 (URLs to re-check live)", "P6-S1"],
    tools=["Google", "Bing", "search engine cache (manual)"], scope_required=False, severity_hint="medium",
    evidence_required="Per query: engine, date, hit count, screenshot/URL list (no downloaded secrets)",
    checks=[
        "Google and Bing evaluated SEPARATELY (indexes differ); record results per engine",
        "File extensions: pdf",
        "File extensions: doc / docx",
        "File extensions: xls / xlsx",
        "File extensions: ppt / pptx",
        "File extensions: csv",
        "File extensions: sql",
        "File extensions: txt",
        "File extensions: env",
        "File extensions: log",
        "File extensions: bak / old / orig / save",
        "File extensions: conf / cfg / ini",
        "File extensions: xml",
        "File extensions: json",
        "File extensions: yml / yaml",
        "File extensions: zip / tar / gz / 7z / rar",
        "File extensions: pem / key / pfx (FLAG ONLY, never download)",
        "Directory listings: intitle:\"index of\"",
        "Exposed admin / login pages",
        "Error messages and stack traces",
        "Exposed .git / .svn / .DS_Store",
        "Cached pages reviewed for removed content",
        "Document metadata (author, software, internal paths) noted from public docs",
    ],
    opsec=["Run queries by hand in a browser; automated scraping violates engine ToS and gets the egress IP blocked",
           "Never download key/pfx/pem/env content; record URL + existence only",
           "Opening an indexed URL on the target is active contact: only for in-scope hosts"],
    validation="Every query in the generated list executed on both engines; hits recorded with engine + date; sensitive hits escalated.",
    cmd=(
        "for d in $(cat {ext_domains}); do "
        "for e in " + " ".join(DORK_EXTS) + "; do "
        "q=\"site:$d filetype:$e\"; echo \"DORK ext=$e GOOGLE https://www.google.com/search?q=$(printf %s \"$q\" | jq -sRr @uri)\"; "
        "echo \"DORK ext=$e BING https://www.bing.com/search?q=$(printf %s \"$q\" | jq -sRr @uri)\"; done; "
        + "".join(
            "q=\"site:$d " + q.replace('"', '\\"') + "\"; "
            "echo \"DORK cat=" + c + " GOOGLE https://www.google.com/search?q=$(printf %s \"$q\" | jq -sRr @uri)\"; "
            "echo \"DORK cat=" + c + " BING https://www.bing.com/search?q=$(printf %s \"$q\" | jq -sRr @uri)\"; "
            for c, q in DORK_EXTRA)
        + "done"
    ),
    params={"ext_domains": {"type": "file"}},
    timeout=120, tags=["external", "ext-p1", "osint", "passive", "manual", "dorking"], passive_flow=True,
)
step(
    sid="P1-S4", phase="P1", name="EXT P1-S4 Web Archive Analysis",
    objective="Mine Wayback Machine and URLScan for removed endpoints, old configs, forgotten hosts and parameters.",
    depends_on=["P0-S1"], feeds_into=["P1-S6 (archive hostnames)", "P4-S5 (interesting URLs to re-check live)"],
    tools=["Wayback CDX API", "URLScan search API", "CommonCrawl index (manual)"],
    scope_required=False, severity_hint="medium",
    evidence_required="Interesting archived URL list with archive timestamp",
    checks=[
        "Wayback CDX pulled for *.domain on every in-scope domain",
        "URLScan results pulled",
        "CommonCrawl index checked (manual, index.commoncrawl.org)",
        "Removed endpoints / admin paths noted",
        "Old config / backup / dump files noted (ARCHIVE_INTERESTING)",
        "Parameters collected for later web testing",
        "Forgotten hostnames passed to P1-S6",
        "Liveness of archived paths is NOT checked here (active): deferred to P4-S5",
    ],
    opsec=["Passive: requests go to archive.org / urlscan.io only",
           "Do not open archived URLs on the live target in this phase"],
    validation="CDX + URLScan completed for every domain; ARCHIVE_INTERESTING and ARCHIVE_HOST lists produced (may be empty, recorded as such).",
    cmd=(
        "for d in $(cat {ext_domains}); do "
        "curl -s --max-time 300 \"https://web.archive.org/cdx/search/cdx?url=*.$d/*&output=txt&fl=original&collapse=urlkey&limit=100000\"; "
        "curl -s --max-time 60 \"https://urlscan.io/api/v1/search/?q=domain:$d&size=1000\" | jq -r '.results[].page.url' 2>/dev/null; "
        "sleep 2; done | sort -u > ext_archive_urls.txt; "
        "echo \"ARCHIVE_URL_COUNT $(wc -l < ext_archive_urls.txt)\"; "
        "grep -Eio '^https?://[^/:?#]+' ext_archive_urls.txt | sed -E 's#^https?://##' | tr 'A-Z' 'a-z' | sort -u | sed 's/^/ARCHIVE_HOST /'; "
        "grep -Ei '\\.(sql|bak|old|orig|save|env|log|conf|cfg|ini|xml|json|ya?ml|zip|tar|gz|7z|rar|pem|key|pfx|csv|xlsx?|docx?|pdf)(\\?|$)|/(admin|backup|debug|api|swagger|graphql|\\.git)' ext_archive_urls.txt | sed 's/^/ARCHIVE_INTERESTING /'; "
        "echo 'ARCHIVE_INTERESTING -'; grep -Eo '[?&][A-Za-z0-9_.-]+=' ext_archive_urls.txt | tr -d '?&=' | sort | uniq -c | sort -rn | head -100 | sed 's/^/ARCHIVE_PARAM /'"
    ),
    regex={"archive_hosts": r"^ARCHIVE_HOST (\S+)$", "archive_interesting": r"^ARCHIVE_INTERESTING (\S+)$"},
    params={"ext_domains": {"type": "file"}},
    timeout=3600, alerts={"warning": [r"ARCHIVE_INTERESTING \S+\.(sql|env|bak|pem|key|pfx)\b"]},
    tags=["external", "ext-p1", "osint", "passive", "archive"], passive_flow=True,
)
step(
    sid="P1-S5", phase="P1", name="EXT P1-S5 SpiderFoot Passive Correlation",
    objective="Low-priority supporting step: automated OSINT correlation for secondary leaks, related domains and third-party links.",
    depends_on=["P0-S1"], feeds_into=["P1-S6 (extra hosts)", "P1-S7 (emails, leaks)", "P0-S1 (related domains)"],
    tools=["SpiderFoot (-u passive)"], scope_required=False, severity_hint="info",
    evidence_required="SpiderFoot CSV export",
    checks=[
        "Run in passive use-case only",
        "Secondary data leaks reviewed",
        "Related / affiliate domains listed and sent to lead (out of scope until confirmed)",
        "Third-party services (SaaS, CDN, mail, analytics) listed for P0 provider-permission review",
        "Duplicates with P1-S1..S4 ignored; only new leads carried forward",
    ],
    opsec=["-u passive only: footprint/investigate/all modules touch the target",
           "Low priority: skip if time-boxed; mark n/a with reason"],
    validation="Passive scan finished; new leads (if any) copied to the relevant steps.",
    cmd="spiderfoot -s {ext_domain} -u passive -o csv -q -n 2>/dev/null",
    regex={"sf_emails": r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"},
    timeout=7200, tags=["external", "ext-p1", "osint", "passive", "low-priority"], passive_flow=True,
)
step(
    sid="P1-S6", phase="P1", name="EXT P1-S6 CT and Passive Subdomain Discovery",
    objective="Build the deduplicated subdomain list from Certificate Transparency, passive DNS sources and archive hostnames.",
    depends_on=["P0-S1", "P1-S4 (archive hosts)"], feeds_into=["P2-S1"],
    tools=["crt.sh", "subfinder (passive sources)", "Wayback CDX hosts"], scope_required=False, severity_hint="info",
    evidence_required="Final SUB list with count",
    checks=[
        "CT logs (crt.sh) queried for every in-scope domain",
        "Passive DNS / aggregator sources queried (subfinder -all, API keys configured)",
        "Archive hostnames (P1-S4) merged",
        "Wildcards stripped, lowercased, deduplicated",
        "Only names under in-scope apex domains kept; others sent to lead",
        "Apex domains themselves included so P2 never starts empty",
    ],
    opsec=["Passive: no queries to target nameservers here",
           "A name under an in-scope domain can still be hosted out of IP scope: P2-S1 classifies"],
    validation="SUB list produced, deduplicated, only in-scope suffixes; count recorded.",
    cmd=(
        "{ for d in $(cat {ext_domains}); do echo \"$d\"; "
        "curl -s --max-time 120 \"https://crt.sh/?q=%25.$d&output=json\" | jq -r '.[].name_value' 2>/dev/null; "
        "subfinder -d \"$d\" -all -silent 2>/dev/null; "
        "curl -s --max-time 300 \"https://web.archive.org/cdx/search/cdx?url=*.$d&output=txt&fl=original&collapse=urlkey&limit=100000\" | grep -Eio '^https?://[^/:?#]+' | sed -E 's#^https?://##'; "
        "sleep 2; done; } | tr 'A-Z' 'a-z' | sed 's/^\\*\\.//' | sort -u > ext_subs_raw.txt; "
        "python3 - ext_subs_raw.txt {ext_domains} <<'PY'\n"
        "import sys\n"
        "doms = [d.strip().lower() for d in open(sys.argv[2]).read().split() if d.strip()]\n"
        "n = 0\n"
        "for h in open(sys.argv[1]).read().split():\n"
        "    if any(h == d or h.endswith('.' + d) for d in doms):\n"
        "        print('SUB', h); n += 1\n"
        "    else:\n"
        "        print('OFFSCOPE_NAME', h)\n"
        "print('SUB_COUNT', n)\n"
        "print('SUB -')  # placeholder: keeps the chain alive on IP-only engagements\n"
        "PY"
    ),
    regex={"ext_subdomains": r"^SUB (\S+)$"},
    params={"ext_domains": {"type": "file"}},
    timeout=3600, tags=["external", "ext-p1", "osint", "passive", "subdomains"], passive_flow=True,
)
step(
    sid="P1-S7", phase="P1", name="EXT P1-S7 Leaked Credentials and Code Exposure",
    objective="Check breach corpora, public code repos, paste sites and public cloud storage for corporate accounts and exposed secrets/configs.",
    depends_on=["P0-S1", "P1-S2 (identities)", "P1-S5 (emails)"], feeds_into=["P6-S1", "P4-S3 (only with written approval)"],
    tools=["HIBP domain search / breach intel (manual)", "GitHub / GitLab code search", "grep.app", "paste-site search", "public bucket search (manual)"],
    scope_required=False, severity_hint="high",
    evidence_required="Count of exposed accounts per breach (no passwords), redacted secret location (repo/file/line)",
    checks=[
        "Breach corpora checked for @domain accounts; record exposure + breach name/date only",
        "Passwords / hashes NOT stored in bars or the report",
        "Public code repos searched (org name, domain, internal hostnames, API key patterns)",
        "Paste sites searched",
        "Public cloud storage (S3/Azure Blob/GCS) checked for company-named buckets (listing only, no download of data)",
        "Exposed secrets reported to lead immediately for rotation",
        "No discovered credential used without explicit written scope approval",
    ],
    opsec=["NEVER validate a found credential against any live system without written approval",
           "Redact secrets in evidence (first 4 chars max)",
           GLOBAL_STOP],
    validation="All sources checked; exposure list (accounts/secrets by location, redacted) recorded; lead notified of live secrets.",
    cmd=(
        "for d in $(cat {ext_domains}); do o=${d%%.*}; "
        "echo \"SEARCH github https://github.com/search?type=code&q=%22$d%22\"; "
        "echo \"SEARCH github-org https://github.com/search?type=users&q=$o\"; "
        "echo \"SEARCH gitlab https://gitlab.com/search?search=%22$d%22\"; "
        "echo \"SEARCH grepapp https://grep.app/search?q=$d\"; "
        "echo \"SEARCH postman https://www.postman.com/search?q=$d\"; "
        "echo \"SEARCH buckets https://buckets.grayhatwarfare.com/files?keywords=$o\"; "
        "echo \"SEARCH hibp https://haveibeenpwned.com/DomainSearch (verify $d ownership via client)\"; done"
    ),
    params={"ext_domains": {"type": "file"}},
    timeout=60, tags=["external", "ext-p1", "osint", "passive", "manual", "leaks"], passive_flow=True,
)

# ============================== P2 ==============================
step(
    sid="P2-S1", phase="P2", name="EXT P2-S1 Subdomain Resolution and Scope Classification",
    objective="Resolve every candidate name (A/AAAA/CNAME) and classify each IP IN / OUT of ext_scope; build the active-testing target list.",
    depends_on=["P1-S6"], feeds_into=["P2-S2 (PTR)", "P2-S4 (CNAMEs)", "P3-S1 (scan targets)", "P4-S1/P4-S5 (in-scope names)"],
    tools=["dig", "python3 ipaddress"], scope_required=True, severity_hint="info",
    evidence_required="Classification table (name, record, IP, IN/OUT/PRIVATE)",
    checks=[
        "Every P1-S6 name resolved (A, AAAA, CNAME chain)",
        "Each resolved IP classified IN (inside ext_scope) / OUT / PRIVATE",
        "OUT names/IPs logged and reported to lead; never forwarded to active steps",
        "Private / internal IPs in public DNS flagged (information leak)",
        "CNAMEs without final address recorded as dangling candidates for P2-S4",
        "Scan target list = ext_scope networks + IN-scope resolved IPs",
    ],
    opsec=["Uses the agent's configured resolver; no zone-walking here",
           "CDN/WAF/SaaS IPs (OUT) are third-party: do not scan without provider permission (P0)"],
    validation="Every candidate name has a RESOLVED/NORES line; SCAN_TARGET list non-empty; OUT list sent to lead.",
    cmd=(
        "python3 - {subdomains} {ext_scope} <<'PY'\n"
        "import sys, subprocess, ipaddress\n"
        "names = sorted(set(x for x in open(sys.argv[1]).read().split() if '.' in x))\n"
        "nets = [ipaddress.ip_network(x, strict=False) for x in open(sys.argv[2]).read().split()]\n"
        "for n in nets: print('SCAN_TARGET', n)\n"
        "seen = set()\n"
        "for h in names:\n"
        "    ips, cn = [], []\n"
        "    for t in ('A', 'AAAA'):\n"
        "        out = subprocess.run(['dig', '+noall', '+answer', '+time=3', '+tries=2', t, h], capture_output=True, text=True).stdout\n"
        "        for line in out.splitlines():\n"
        "            p = line.split()\n"
        "            if len(p) >= 5 and p[3] == 'CNAME': cn.append(p[4].rstrip('.'))\n"
        "            if len(p) >= 5 and p[3] in ('A', 'AAAA'): ips.append(p[4])\n"
        "    for c in sorted(set(cn)): print('CNAME', h, c)\n"
        "    if not ips:\n"
        "        print('NORES', h, ('DANGLING_CANDIDATE ' + cn[-1]) if cn else '')\n"
        "        continue\n"
        "    inscope = False\n"
        "    for ip in sorted(set(ips)):\n"
        "        a = ipaddress.ip_address(ip)\n"
        "        cls = 'PRIVATE' if (a.is_private or a.is_loopback or a.is_link_local) else ('IN' if any(a in n for n in nets) else 'OUT')\n"
        "        print('RESOLVED', cls, h, ip)\n"
        "        if cls == 'IN':\n"
        "            inscope = True\n"
        "            if ip not in seen: seen.add(ip); print('SCAN_TARGET', ip); print('INSCOPE_IP', ip)\n"
        "    if inscope: print('INSCOPE_NAME', h)\n"
        "    if cn: print('CNAME_HOST', h)\n"
        "for k in ('INSCOPE_NAME', 'INSCOPE_IP', 'CNAME_HOST'): print(k, '-')  # placeholders, consumers skip '-'\n"
        "PY"
    ),
    regex={"ext_scan_targets": r"^SCAN_TARGET (\S+)$",
           "ext_inscope_names": r"^INSCOPE_NAME (\S+)$",
           "ext_inscope_ips": r"^INSCOPE_IP (\S+)$",
           "ext_outscope_ips": r"^RESOLVED OUT \S+ (\S+)$",
           "ext_cname_hosts": r"^CNAME_HOST (\S+)$",
           "ext_dangling": r"^NORES (\S+) DANGLING_CANDIDATE"},
    params={"subdomains": {"type": "file"}, "ext_scope": {"type": "file"}},
    timeout=3600, alerts={"warning": [r"^RESOLVED PRIVATE", r"DANGLING_CANDIDATE"]},
    tags=["external", "ext-p2", "dns", "scope"], passive_flow=True,
    flow_inputs={"subdomains": ("P1-S6", "ext_subdomains")},
)
step(
    sid="P2-S2", phase="P2", name="EXT P2-S2 DNS Records and Zone Transfer",
    objective="Enumerate every record type on each in-scope domain, test AXFR/IXFR on every authoritative NS, check NS consistency, DNSSEC and PTRs.",
    depends_on=["P0-S1", "P2-S1 (in-scope IPs for PTR)"], feeds_into=["P2-S3 (MX/TXT)", "P2-S4", "P3-S1 (NS/MX hosts if IN scope)", "P6-S1"],
    tools=["dig"], scope_required=True, severity_hint="high (AXFR) / low (others)",
    evidence_required="dig output per record type; AXFR transcript per NS",
    checks=[
        "Record type: A", "Record type: AAAA", "Record type: MX", "Record type: TXT", "Record type: NS",
        "Record type: CNAME", "Record type: SRV (common service labels)", "Record type: PTR (reverse DNS of in-scope IPs)",
        "Record type: SOA (serial, contact, timers)", "Record type: CAA (present? which CAs?)",
        "Record type: NAPTR", "Record type: DNSKEY / DS (DNSSEC signed and chained?)",
        "AXFR tested on EVERY authoritative NS (not just the first)",
        "IXFR tested on every authoritative NS",
        "NS responses consistent across all authoritative servers (SOA serial match)",
        "Internal hostnames / private IPs in public records flagged",
    ],
    opsec=["AXFR is a single, non-destructive query; still only against in-scope domains' NS",
           "If a NS is third-party (registrar/DNS provider), the query is acceptable but the finding is the client's config"],
    validation="All record types queried for every domain; AXFR/IXFR result recorded per NS (REFUSED / SUCCESS); SOA serials compared.",
    cmd=(
        "q() { dig +short +time=3 +tries=2 \"$@\" | grep -v '^;'; }; "
        "for d in $(cat {ext_domains}); do echo \"##### DOMAIN $d\"; "
        "for t in A AAAA MX TXT NS CNAME SOA CAA NAPTR DNSKEY DS; do echo \"=== $t $d\"; dig +noall +answer +multiline \"$d\" \"$t\"; done; "
        "for s in _sip._tcp _sip._udp _sips._tcp _xmpp-server._tcp _autodiscover._tcp _ldap._tcp _kerberos._tcp _vpn._tcp _submission._tcp _imaps._tcp; do echo \"=== SRV $s.$d\"; q SRV \"$s.$d\"; done; "
        "for ns in $(q NS \"$d\"); do echo \"=== SOA@$ns $(q SOA \"$d\" @\"$ns\" | awk '{print $3}')\"; "
        "if dig AXFR \"$d\" @\"$ns\" +time=5 | grep -qE '\\sIN\\s+SOA\\s'; then echo \"AXFR_SUCCESS $d @$ns\"; dig AXFR \"$d\" @\"$ns\" | head -200; else echo \"AXFR_REFUSED $d @$ns\"; fi; "
        "s=$(q SOA \"$d\" @\"$ns\" | awk '{print $3}'); if dig IXFR=$((s-1)) \"$d\" @\"$ns\" +time=5 | grep -qE '\\sIN\\s+(A|AAAA|CNAME|MX)\\s'; then echo \"IXFR_SUCCESS $d @$ns\"; else echo \"IXFR_REFUSED $d @$ns\"; fi; done; done; "
        "for ip in {inscope_ips}; do [ \"$ip\" = - ] && continue; echo \"PTR $ip $(q -x \"$ip\")\"; done"
    ),
    regex={"axfr_open": r"^AXFR_SUCCESS (\S+ @\S+)$", "ns_soa": r"^=== SOA@(\S+ \d+)$"},
    params={"ext_domains": {"type": "file"}},
    timeout=1800, alerts={"critical": ["AXFR_SUCCESS", "IXFR_SUCCESS"]},
    finding={"title": "DNS Zone Transfer (AXFR/IXFR) Allowed to Any Host", "severity": "high", "cwe": "CWE-200",
             "remediation": "Restrict zone transfers to secondary nameserver IPs (allow-transfer / TSIG) on every authoritative server.",
             "references": ["https://www.rfc-editor.org/rfc/rfc5936"]},
    tags=["external", "ext-p2", "dns", "axfr"], passive_flow=True,
    flow_inputs={"inscope_ips": ("P2-S1", "ext_inscope_ips")},
)
step(
    sid="P2-S3", phase="P2", name="EXT P2-S3 Email Security Records",
    objective="Check presence and strictness of SPF, DKIM, DMARC, MTA-STS, TLS-RPT and BIMI; non-sending domains must publish null SPF + DMARC reject.",
    depends_on=["P2-S2 (MX/TXT)"], feeds_into=["P3-S4 (SMTP hosts)", "P6-S1"],
    tools=["dig", "curl (MTA-STS policy)"], scope_required=True, severity_hint="medium",
    evidence_required="Raw TXT records per domain and the policy evaluation",
    checks=[
        "SPF present; qualifier: -all (good) / ~all (weak) / ?all or +all (bad)",
        "SPF DNS-lookup count <= 10 (include/a/mx/ptr/exists/redirect)",
        "SPF: single record only (multiple = permerror)",
        "DKIM selectors discovered (common list + headers from received mail); key length >= 2048 preferred, 1024 = weak",
        "DMARC present; p=none / quarantine / reject",
        "DMARC rua / ruf reporting addresses, pct (<100 = partial), sp (subdomain policy)",
        "MTA-STS TXT + https://mta-sts.<domain>/.well-known/mta-sts.txt mode (enforce/testing/none)",
        "TLS-RPT (_smtp._tls) present",
        "BIMI (default._bimi) where relevant",
        "Non-sending domains (no MX or null MX '0 .'): null SPF 'v=spf1 -all' and DMARC p=reject",
    ],
    opsec=["DNS lookups + one HTTPS GET to mta-sts host per domain; no mail is sent"],
    validation="Every domain has an SPF/DKIM/DMARC/MTA-STS/TLS-RPT verdict line; weak/missing policies flagged.",
    cmd=(
        "q() { dig +short +time=3 +tries=2 \"$@\" | grep -v '^;'; }; "
        "for d in $(cat {ext_domains}); do echo \"##### DOMAIN $d\"; "
        "mx=$(q MX \"$d\"); echo \"MX $d ${mx:-NONE}\"; "
        "spf=$(q TXT \"$d\" | tr -d '\"' | grep -i '^v=spf1'); n=$(echo \"$spf\" | grep -c 'v=spf1'); "
        "echo \"SPF $d count=$n ${spf:-MISSING}\"; "
        "case \"$spf\" in *-all*) echo \"SPF_POLICY $d HARDFAIL\";; *~all*) echo \"SPF_POLICY $d SOFTFAIL\";; *\\?all*|*+all*) echo \"SPF_POLICY $d WEAK\";; '') echo \"SPF_POLICY $d MISSING\";; *) echo \"SPF_POLICY $d NO_ALL\";; esac; "
        "echo \"SPF_LOOKUPS $d $(echo \"$spf\" | grep -oiE '(include:|a:|a |mx|ptr|exists:|redirect=)' | wc -l) (top level only; expand includes manually)\"; "
        "dm=$(q TXT \"_dmarc.$d\" | tr -d '\"'); echo \"DMARC $d ${dm:-MISSING}\"; "
        "case \"$dm\" in *p=reject*) echo \"DMARC_POLICY $d REJECT\";; *p=quarantine*) echo \"DMARC_POLICY $d QUARANTINE\";; *p=none*) echo \"DMARC_POLICY $d NONE\";; *) echo \"DMARC_POLICY $d MISSING\";; esac; "
        "for s in default selector1 selector2 google k1 k2 mail dkim s1 s2 mxvault zoho smtp pm mandrill everlytickey1 sendgrid; do r=$(q TXT \"$s._domainkey.$d\" | tr -d '\" '); [ -n \"$r\" ] && echo \"DKIM $d selector=$s keylen_b64=$(echo \"$r\" | sed -n 's/.*p=\\([^;]*\\).*/\\1/p' | wc -c) $r\"; done; "
        "echo \"MTA_STS_TXT $d $(q TXT _mta-sts.$d | tr -d '\"')\"; "
        "echo \"MTA_STS_POLICY $d $(curl -s --max-time 10 https://mta-sts.$d/.well-known/mta-sts.txt | tr '\\n' ' ')\"; "
        "echo \"TLSRPT $d $(q TXT _smtp._tls.$d | tr -d '\"')\"; "
        "echo \"BIMI $d $(q TXT default._bimi.$d | tr -d '\"')\"; "
        "case \"$mx\" in ''|'0 .') echo \"NON_SENDING_CHECK $d spf=$(echo \"$spf\" | grep -q '^v=spf1 -all$' && echo NULL_OK || echo NOT_NULL) dmarc=$(echo \"$dm\" | grep -q 'p=reject' && echo REJECT_OK || echo NOT_REJECT)\";; esac; "
        "done"
    ),
    regex={"spf_weak": r"^SPF_POLICY (\S+) (?:SOFTFAIL|WEAK|MISSING|NO_ALL)$",
           "dmarc_weak": r"^DMARC_POLICY (\S+) (?:NONE|MISSING)$"},
    params={"ext_domains": {"type": "file"}},
    timeout=900,
    alerts={"warning": [r"SPF_POLICY \S+ (SOFTFAIL|WEAK|MISSING|NO_ALL)", r"DMARC_POLICY \S+ (NONE|MISSING)", r"NON_SENDING_CHECK .* NOT_(NULL|REJECT)"]},
    finding={"title": "Weak or Missing Email Authentication Policy (SPF/DMARC)", "severity": "medium", "cwe": "CWE-290",
             "remediation": "Publish SPF ending in -all within 10 lookups, DKIM with >=2048-bit keys, and DMARC p=reject (sp=reject, pct=100) after monitoring; for non-sending domains publish 'v=spf1 -all' and DMARC p=reject.",
             "references": ["https://www.rfc-editor.org/rfc/rfc7208", "https://www.rfc-editor.org/rfc/rfc7489", "https://www.rfc-editor.org/rfc/rfc8461"]},
    tags=["external", "ext-p2", "dns", "email"], passive_flow=True,
)
step(
    sid="P2-S4", phase="P2", name="EXT P2-S4 Dangling DNS and Subdomain Takeover Check",
    objective="Identify CNAME/records pointing to decommissioned cloud/SaaS resources and verify takeover condition WITHOUT claiming the resource.",
    depends_on=["P2-S1 (CNAME hosts, dangling candidates)"], feeds_into=["P6-S1"],
    tools=["nuclei takeover templates", "dig", "can-i-take-over-xyz fingerprints (manual)"],
    scope_required=True, severity_hint="high",
    evidence_required="Name, CNAME target, provider, fingerprint/response snippet",
    checks=[
        "Every CNAME host from P2-S1 checked",
        "NORES + CNAME (DANGLING_CANDIDATE) entries reviewed manually",
        "Provider fingerprint matched (S3 NoSuchBucket, Azure, GitHub Pages, Heroku, Shopify, etc.)",
        "Also checked: A records to released cloud IPs, NS delegations to unregistered/expired zones, MX to dead providers",
        "Takeover condition verified only by fingerprint/response; resource NOT registered or claimed",
        "Provider + fingerprint recorded",
    ],
    opsec=["NEVER create/claim the bucket, app, page or zone to prove takeover",
           "One HTTP request per host; rate-limited"],
    validation="Each CNAME host has a verdict (not vulnerable / candidate with provider + fingerprint).",
    cmd=(
        "printf '%s\\n' {cname_hosts} | grep -v '^-$' > ext_cname_hosts.txt; "
        "nuclei -l ext_cname_hosts.txt -tags takeover -rl {ext_req_rate} -silent -nc; "
        "q() { dig +short +time=3 +tries=2 \"$@\" | grep -v '^;'; }; "
        "for h in $(cat ext_cname_hosts.txt); do c=$(q CNAME \"$h\" | tail -1); "
        "[ -n \"$c\" ] && st=$(dig +noall +comments \"$c\" | grep -o 'status: [A-Z]*' | head -1) && echo \"CNAME_TARGET $h -> $c ($st)\"; done"
    ),
    regex={"takeover_candidates": r"^\[[\w:.-]+\] \[(?:http|dns)\] \[\w+\] (\S+)"},
    timeout=1800, alerts={"critical": [r"takeover", r"status: NXDOMAIN"]},
    finding={"title": "Subdomain Takeover Possible (Dangling DNS Record)", "severity": "high", "cwe": "CWE-350",
             "remediation": "Remove or update DNS records pointing to deprovisioned resources; add decommissioning checklist so DNS is removed before the cloud resource.",
             "references": ["https://github.com/EdOverflow/can-i-take-over-xyz", "https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/02-Configuration_and_Deployment_Management_Testing/10-Test_for_Subdomain_Takeover"]},
    tags=["external", "ext-p2", "dns", "takeover"],
    flow_inputs={"cname_hosts": ("P2-S1", "ext_cname_hosts")},
)

# ============================== P3 ==============================
step(
    sid="P3-S1", phase="P3", name="EXT P3-S1 Full TCP Port Discovery",
    objective="Discover every open TCP port (1-65535) on in-scope targets at an RoE-compliant packet rate.",
    depends_on=["P2-S1 (scan targets)", "P0-S2 (egress IP logged)", "P1-S1 (expected ports baseline)"],
    feeds_into=["P3-S3", "P3-S4", "P3-S5", "P4-S1"],
    tools=["nmap (any SYN scanner)"], scope_required=True, severity_hint="info",
    evidence_required="Scan output with start/end timestamp and egress IP",
    checks=[
        "All 65535 TCP ports scanned on every SCAN_TARGET",
        "Packet rate capped at ext_rate; timing within test window",
        "Scan source IP + start/end time recorded (nmap header/footer)",
        "Filtered / no-response counts noted separately (Not shown: lines)",
        "Sample re-verified with a second pass (manual, different timing) to catch drops",
        "Diff vs P1-S1 passive inventory: ports in Shodan but not seen now = filtering/IPS or stale data",
    ],
    opsec=["SYN scan needs root or CAP_NET_RAW on the host_agent", "--max-rate {ext_rate} -T3; lower ext_rate if IPS blocking or latency rises",
           "bars drops Excluded hosts from targets automatically; verify none slipped through",
           "Stop immediately on any sign of service degradation: " + GLOBAL_STOP],
    validation="Scan completed for all targets (no 'QUITTING'), open ports per host parsed into bars hosts/services, filtered counts recorded.",
    cmd="nmap -sS -Pn -n -p- -T3 --max-rate {ext_rate} --max-retries 2 --open --reason -iL {targets}",
    regex={"ext_open_ports": r"^(\d+)/tcp\s+open", "ext_live_ips": r"^Nmap scan report for (\S+)$",
           "filtered_note": r"^Not shown: (.+)$"},
    params={"targets": {"type": "file"}},
    timeout=43200, tags=["external", "ext-p3", "portscan", "active"],
    flow_inputs={"targets": ("P2-S1", "ext_scan_targets")},
)
step(
    sid="P3-S2", phase="P3", name="EXT P3-S2 Critical UDP Port Discovery",
    objective="Scan critical UDP services (DNS, NTP, SNMP, IKE, IPMI, SSDP, memcached, VPNs, SIP...) on in-scope targets.",
    depends_on=["P2-S1 (scan targets)"], feeds_into=["P3-S3", "P3-S4"],
    tools=["nmap -sU (with version probes)"], scope_required=True, severity_hint="medium",
    evidence_required="UDP scan output",
    checks=[
        "ext_udp_ports scanned on every target",
        "open vs open|filtered distinguished (-sV confirms)",
        "Amplification-capable services exposed (DNS open resolver, NTP monlist, SSDP, memcached, SNMP) flagged",
        "IKE (500/4500) -> note VPN gateway for P4-S1",
        "IPMI 623 exposure flagged (should never be internet-facing)",
    ],
    opsec=["UDP is slow and lossy: keep rate low, --max-retries 1",
           "Do not run amplification tests (monlist etc.) that generate large responses without approval"],
    validation="UDP scan complete; open services listed with version or marked open|filtered.",
    cmd="nmap -sU -sV --version-intensity 2 -Pn -n -p {ext_udp_ports} -T3 --max-rate {ext_rate} --max-retries 1 --open --reason -iL {targets}",
    regex={"udp_open": r"^(\d+)/udp\s+open\s"},
    params={"targets": {"type": "file"}},
    timeout=14400, alerts={"warning": [r"^(623|1900|11211|161)/udp\s+open\s"]},
    tags=["external", "ext-p3", "portscan", "udp", "active"],
    flow_inputs={"targets": ("P2-S1", "ext_scan_targets")},
)
step(
    sid="P3-S3", phase="P3", name="EXT P3-S3 Service and Version Detection",
    objective="Identify product and version on every open port (non-80/443 included) and gather CVE leads per version.",
    depends_on=["P3-S1 (open ports, live IPs)", "P1-S1 (CVE leads)"], feeds_into=["P3-S4", "P4-S1", "P4-S4", "P5-S2"],
    tools=["nmap -sV -sC"], scope_required=True, severity_hint="varies",
    evidence_required="Service/version table; CVE research notes per version",
    checks=[
        "Every open port fingerprinted (product + version)",
        "Known critical CVEs researched per identified version (NVD, vendor advisories, CISA KEV)",
        "Unidentified services noted for manual banner grab",
        "End-of-life software versions flagged",
        "bars host tags (ftp, ssh, smb, rdp, smtp, snmp, http...) populated for filtered steps",
    ],
    opsec=["-sC runs nmap 'default' category scripts (safe/non-intrusive); never add 'intrusive'/'vuln'/'brute' without approval"],
    validation="All open ports carry a service/version (or 'unknown' with manual note); CVE leads list created.",
    cmd="nmap -sS -sV -sC -Pn -n -T3 --max-rate {ext_rate} -p $(echo {ports} | tr ' ' '\\n' | sort -un | paste -sd, -) -iL {targets}",
    regex={"cve_refs": r"(CVE-\d{4}-\d{4,7})", "services": r"^\d+/tcp\s+open\s+(\S+)"},
    params={"targets": {"type": "file"}},
    timeout=21600, alerts={"warning": [r"CVE-\d{4}-\d+"]},
    tags=["external", "ext-p3", "service", "active"],
    flow_inputs={"targets": ("P3-S1", "ext_live_ips"), "ports": ("P3-S1", "ext_open_ports")},
)
step(
    sid="P3-S4", phase="P3", name="EXT P3-S4 Anonymous Access and Service Hardening Checks",
    objective="Per-service misconfiguration checks: anonymous/null access, open relay, weak algorithms, missing NLA/signing, default SNMP communities, exposed datastores.",
    depends_on=["P3-S1", "P3-S2", "P3-S3"], feeds_into=["P5-S2", "P6-S1"],
    tools=["nmap NSE (portrule-driven)", "netexec / ssh-audit / snmpwalk (manual follow-up)"],
    scope_required=True, severity_hint="high",
    evidence_required="Script output per host:port",
    checks=[
        "FTP: anonymous login (ftp-anon), cleartext auth",
        "SMB: exposed to internet at all? SMBv1 enabled? signing not required? null session (smb-protocols, smb2-security-mode)",
        "SMTP: open relay (smtp-open-relay), VRFY/EXPN enabled (smtp-commands), STARTTLS offered",
        "SSH: weak kex/ciphers/MACs (ssh2-enum-algos), password auth enabled (ssh-auth-methods)",
        "RDP: NLA/CredSSP enforced? (rdp-enum-encryption); BlueKeep-era versions",
        "SNMP: default communities public/private (snmp-info, manual snmpwalk with RoE-approved list)",
        "Databases (MySQL, MSSQL, PostgreSQL, Oracle, MongoDB, Redis, Elasticsearch, Memcached): internet-exposed at all? unauthenticated access?",
        "Telnet / VNC / rsync / NFS / LDAP exposure (vnc-info, rsync-list-modules, nfs-showmount, ldap-rootdse)",
        "Each issue confirmed by a second, manual method before reporting",
    ],
    opsec=["Only non-intrusive NSE scripts; no brute-force scripts",
           "smtp-open-relay issues MAIL FROM/RCPT TO only (no DATA); RoE must allow it",
           "SNMP: only 'public' tried automatically; wider community lists need approval (lockout/alerting risk)"],
    validation="Every exposed service family has a pass/fail verdict per host:port; positives cross-verified.",
    cmd=(
        "nmap -sS -sV -Pn -n -T3 --max-rate {ext_rate} -p $(echo {ports} | tr ' ' '\\n' | sort -un | paste -sd, -) "
        "--script \"ftp-anon,ftp-syst,smb-protocols,smb2-security-mode,smb-security-mode,smtp-open-relay,smtp-commands,"
        "ssh2-enum-algos,ssh-auth-methods,rdp-enum-encryption,rdp-ntlm-info,vnc-info,rsync-list-modules,nfs-showmount,"
        "ldap-rootdse,mysql-info,ms-sql-info,mongodb-info,redis-info,memcached-info,telnet-encryption\" -iL {targets}; "
        "nmap -sU -Pn -n -p 161 --script snmp-info -iL {targets}"
    ),
    regex={"anon_ftp": r"(Anonymous FTP login allowed)", "open_relay": r"(Server is an open relay)"},
    params={"targets": {"type": "file"}},
    timeout=14400,
    alerts={"critical": ["Anonymous FTP login allowed", "Server is an open relay", r"NT LM 0\.12 \(SMBv1\)", r"redis_version", "mongodb-info"],
            "warning": ["signing enabled but not required", "message_signing: disabled", r"CredSSP \(NLA\): FALSE",
                        "Supported authentication methods:.*password", r"VRFY", r"EXPN", r"(arcfour|3des-cbc|hmac-md5|diffie-hellman-group1-sha1)"]},
    tags=["external", "ext-p3", "service", "misconfig", "active"],
    flow_inputs={"targets": ("P3-S1", "ext_live_ips"), "ports": ("P3-S1", "ext_open_ports")},
)
step(
    sid="P3-S5", phase="P3", name="EXT P3-S5 TLS Configuration Review",
    objective="Review protocol versions, cipher suites and certificates on ALL TLS-enabled services (not only 443; includes STARTTLS on SMTP/IMAP/POP3/FTP/LDAP).",
    depends_on=["P3-S1 (open ports)", "P3-S3 (ssl services)"], feeds_into=["P6-S1"],
    tools=["nmap ssl-enum-ciphers + ssl-cert", "testssl.sh (per-host deep dive)"],
    scope_required=True, severity_hint="medium",
    evidence_required="Cipher/protocol grades and certificate details per host:port",
    checks=[
        "SSLv2 / SSLv3 / TLS 1.0 / TLS 1.1 disabled",
        "TLS 1.2 + 1.3 offered",
        "No NULL / EXPORT / RC4 / 3DES / CBC-only weak suites (ssl-enum-ciphers grade < A flagged)",
        "Certificate valid (not expired, not yet valid), full chain served",
        "Hostname matches CN/SAN",
        "Expiry within 30 days flagged",
        "Key: RSA >= 2048 / EC >= 256; signature not SHA-1/MD5",
        "Self-signed or internal-CA certs on public services flagged",
        "STARTTLS services covered (SMTP 25/587, IMAP 143, POP3 110, FTP 21, LDAP 389)",
    ],
    opsec=["Handshake-only checks; no heartbleed/renegotiation stress tests without approval"],
    validation="Every TLS-capable host:port has protocol list, least-strength grade and certificate validity recorded.",
    cmd="nmap -sS -sV -Pn -n -T3 --max-rate {ext_rate} -p $(echo {ports} | tr ' ' '\\n' | sort -un | paste -sd, -) --script ssl-cert,ssl-enum-ciphers -iL {targets}",
    regex={"weak_tls": r"\|\s+(SSLv3|TLSv1\.0|TLSv1\.1):", "least_strength": r"least strength: ([A-F])"},
    params={"targets": {"type": "file"}},
    timeout=14400,
    alerts={"warning": [r"\|\s+(SSLv3|TLSv1\.0|TLSv1\.1):", r"least strength: [C-F]", r"sha1WithRSA|md5WithRSA", r"Public Key bits: (512|768|1024)$"]},
    finding={"title": "Weak TLS/SSL Configuration", "severity": "medium", "cwe": "CWE-326",
             "remediation": "Disable SSLv3/TLS1.0/TLS1.1 and weak suites (RC4, 3DES, CBC-SHA1 where possible, NULL/EXPORT); enable TLS1.2+1.3 with AEAD suites; use RSA>=2048/ECDSA>=256 certificates with SHA-256 signatures from a trusted CA.",
             "references": ["https://ssl-config.mozilla.org/", "https://www.rfc-editor.org/rfc/rfc8996"]},
    tags=["external", "ext-p3", "tls", "active"],
    flow_inputs={"targets": ("P3-S1", "ext_live_ips"), "ports": ("P3-S1", "ext_open_ports")},
)

# ============================== P4 ==============================
step(
    sid="P4-S1", phase="P4", name="EXT P4-S1 Web and Management Interface Discovery",
    objective="Probe every in-scope name/IP on discovered + common web ports; identify devices, IoT, firewall/VPN, remote-access, CI/CD, monitoring panels and API docs.",
    depends_on=["P2-S1 (in-scope names)", "P3-S1 (open ports, live IPs)", "P3-S3 (service names)"],
    feeds_into=["P4-S2", "P4-S3", "P4-S4", "P5-S1"],
    tools=["httpx (ProjectDiscovery; Kali: httpx-toolkit)", "screenshot tool (manual: gowitness/aquatone)"],
    scope_required=True, severity_hint="info",
    evidence_required="URL list with status, title, server, tech; screenshots of panels",
    checks=[
        "IP cameras / NVR / DVR",
        "Firewall / UTM panels (FortiGate, Palo Alto, Sophos, pfSense, Check Point)",
        "VPN gateways / SSL-VPN portals (FortiGate SSL-VPN, GlobalProtect, Pulse/Ivanti, Cisco ASA AnyConnect)",
        "Printers / MFPs",
        "Server management (iDRAC, iLO, IPMI web, Webmin, cPanel, Plesk, vCenter/ESXi)",
        "Remote access portals (Citrix NetScaler/Gateway, OWA/Exchange, RD Web/RD Gateway)",
        "CI/CD and repos (Jenkins, GitLab, TeamCity, Argo, Harbor)",
        "Monitoring / logging (Grafana, Kibana, Zabbix, Nagios, Prometheus)",
        "API docs exposed (Swagger/OpenAPI UI, GraphQL endpoint + introspection)",
        "Both IP and hostname (vhost) probed: different vhosts can expose different apps",
    ],
    opsec=["Rate-limited (ext_req_rate); GET only",
           "Names resolving OUT of scope were already removed in P2-S1; do not add them back"],
    validation="Every in-scope name + live IP probed on discovered+common web ports; WEB_URL list produced; panels categorized.",
    cmd=(
        "printf '%s\\n' {inscope_names} {live_ips} | grep -v '^-$' | sort -u > ext_web_hosts.txt; "
        "P=$(echo {ports} {ext_web_ports} | tr ' ,' '\\n\\n' | sort -un | paste -sd, -); "
        "httpx-toolkit -l ext_web_hosts.txt -p \"$P\" "
        "-sc -title -server -td -ip -fr -rl {ext_req_rate} -silent -nc"
    ),
    regex={"ext_web_urls": r"^(https?://\S+)"},
    timeout=7200,
    alerts={"warning": [r"(idrac|integrated lights-out|ilo\b|webmin|fortigate|fortinet|globalprotect|pulse secure|ivanti|netscaler|citrix|outlook web|owa|rd web|remote desktop web|jenkins|gitlab|grafana|kibana|zabbix|swagger|graphql|phpmyadmin|vmware|vcenter|esxi|router|camera|hikvision|dahua|printer)"]},
    tags=["external", "ext-p4", "web", "panels", "active"],
    flow_inputs={"inscope_names": ("P2-S1", "ext_inscope_names"), "live_ips": ("P3-S1", "ext_live_ips"), "ports": ("P3-S1", "ext_open_ports")},
)
step(
    sid="P4-S2", phase="P4", name="EXT P4-S2 Panel Fingerprint and API Docs Exposure",
    objective="Fingerprint detected panels/products precisely (vendor, product, version) and detect exposed API documentation / GraphQL introspection.",
    depends_on=["P4-S1 (web URLs)"], feeds_into=["P4-S3", "P4-S4"],
    tools=["nuclei (panel, tech, swagger, graphql templates)"], scope_required=True, severity_hint="low-medium",
    evidence_required="Template match lines per URL",
    checks=[
        "Product + version extracted for each panel",
        "Swagger/OpenAPI UI or spec reachable without auth",
        "GraphQL introspection enabled",
        "Panel ownership confirmed (client vs third-party SaaS) before any login attempt",
    ],
    opsec=["Detection templates only (panel/tech/exposure); intrusive/dos/fuzz/default-login excluded"],
    validation="Each WEB_URL has a fingerprint result or 'unknown'; API doc exposures listed.",
    cmd="printf '%s\\n' {web_urls} > ext_web_urls.txt; nuclei -l ext_web_urls.txt -tags panel,tech,swagger,graphql,exposure -etags {ext_nuclei_exclude_tags} -rl {ext_req_rate} -silent -nc",
    regex={"panels": r"\[([\w-]*panel[\w-]*)\]"},
    timeout=7200, alerts={"warning": [r"\[(swagger|graphql)[\w-]*\]"]},
    tags=["external", "ext-p4", "web", "panels", "active"],
    flow_inputs={"web_urls": ("P4-S1", "ext_web_urls")},
)
step(
    sid="P4-S3", phase="P4", name="EXT P4-S3 Default Credentials and Lockout Check",
    objective="Check whether detected panels still accept documented VENDOR DEFAULT credentials, only after confirming lockout/blocking behaviour.",
    depends_on=["P4-S2 (panel fingerprints)", "P1-S7 (only with written approval)"], feeds_into=["P6-S1"],
    tools=["browser (manual)", "vendor documentation / default-credential lists"], scope_required=True, severity_hint="critical",
    evidence_required="Attempt log: UTC time, URL, username, credential source (vendor doc), result, lockout indicators",
    checks=[
        "Lockout / IP-blocking policy checked BEFORE first attempt (docs, login page hints, CAPTCHA, prior response headers)",
        "If page shows 'Remaining attempts: N' or similar counter: STOP, no attempts",
        "If 'admin' (or target username) is a real administrator's personal account: STOP, no attempts",
        "Only documented vendor defaults for the identified product/version (max 1-2 tries per account)",
        "No brute force / password spraying unless RoE explicitly allows it",
        "Every attempt logged (time, URL, user, source, result)",
        "On success: do not change settings, screenshot landing page only, log out, notify lead",
    ],
    opsec=["CRITICAL: account lockout of a production admin = engagement incident",
           "Automated default-login templates are excluded by default (ext_nuclei_exclude_tags); remove only with written approval",
           GLOBAL_STOP],
    validation="Every fingerprinted panel has: lockout assessment, attempt log (or documented reason for no attempt), verdict.",
    manual=True, tags=["external", "ext-p4", "web", "credentials", "manual", "active"],
)
step(
    sid="P4-S4", phase="P4", name="EXT P4-S4 Version-Based Critical Vulnerability Check",
    objective="Verify whether identified web servers, panels, VPNs and devices carry critical unauthenticated vulnerabilities (RCE, auth bypass, KEV) using non-destructive checks.",
    depends_on=["P3-S3 (versions, CVE leads)", "P4-S1 (web URLs)", "P4-S2 (panel versions)"], feeds_into=["P5-S2", "P6-S1"],
    tools=["nuclei (kev, cve; safe templates)", "vendor advisories", "manual version/behaviour checks"],
    scope_required=True, severity_hint="critical",
    evidence_required="Version evidence + non-destructive verification output per CVE",
    checks=[
        "Each identified product/version mapped to CISA KEV and vendor critical advisories",
        "Unauthenticated RCE / auth bypass candidates listed",
        "Verification via version/behaviour or vendor-safe PoC only (no state change, no command execution)",
        "Confidence recorded: confirmed / likely (version only) / unverified",
        "Critical confirmed issues escalated to lead immediately",
    ],
    opsec=["No payloads that alter state or run commands without explicit written approval",
           "Exclude intrusive/dos templates; KEV-class edge devices (VPN/firewall) are fragile, watch for impact",
           GLOBAL_STOP],
    validation="Every product/version has a CVE verdict with method + confidence; criticals escalated.",
    cmd="printf '%s\\n' {web_urls} > ext_web_urls.txt; nuclei -l ext_web_urls.txt -tags kev,cve,rce,unauth,auth-bypass -severity critical,high -etags {ext_nuclei_exclude_tags} -rl {ext_req_rate} -silent -nc",
    regex={"crit_cves": r"\[(CVE-\d{4}-\d{4,7})[^\]]*\]"},
    timeout=10800, alerts={"critical": [r"\[critical\]"], "warning": [r"\[high\]"]},
    tags=["external", "ext-p4", "web", "cve", "active"],
    flow_inputs={"web_urls": ("P4-S1", "ext_web_urls")},
)
step(
    sid="P4-S5", phase="P4", name="EXT P4-S5 Archived Endpoint Liveness Check",
    objective="Check whether interesting archived/dorked URLs (P1-S3/P1-S4) still respond today, only on in-scope hosts.",
    depends_on=["P1-S4 (archive interesting URLs)", "P2-S1 (in-scope names)"], feeds_into=["P5-S2", "P6-S1"],
    tools=["curl"], scope_required=True, severity_hint="medium",
    evidence_required="URL, HTTP status, content-length, content-type (no sensitive body stored)",
    checks=[
        "Only URLs whose host is an INSCOPE_NAME from P2-S1 tested",
        "Status code + size recorded per URL",
        "200 on config/backup/dump/env/key paths escalated",
        "Content of secrets NOT downloaded/stored; HEAD or ranged GET only",
    ],
    opsec=["HEAD requests, 1 req/s; body never saved",
           "Out-of-scope hosts in archive list are skipped and logged"],
    validation="Every in-scope interesting URL has a status line; live sensitive files escalated.",
    cmd=(
        "python3 - {archive_urls} {inscope_names} <<'PY'\n"
        "import sys, subprocess, time, urllib.parse\n"
        "ok = set(open(sys.argv[2]).read().split())\n"
        "for u in sorted(set(open(sys.argv[1]).read().split())):\n"
        "    h = (urllib.parse.urlsplit(u).hostname or '').lower()\n"
        "    if h not in ok:\n"
        "        print('SKIP_OUT_OF_SCOPE', u); continue\n"
        "    code = subprocess.run(['curl', '-skI', '-o', '/dev/null', '--max-time', '10', '-w', '%' + chr(123) + 'http_code' + chr(125), u], capture_output=True, text=True).stdout.strip()\n"
        "    print('LIVE' if code == '200' else 'STATUS', code, u)\n"
        "    time.sleep(1)\n"
        "PY"
    ),
    regex={"live_archived": r"^LIVE 200 (\S+)$"},
    params={"archive_urls": {"type": "file"}, "inscope_names": {"type": "file"}},
    timeout=7200, alerts={"warning": [r"^LIVE 200 .*\.(sql|env|bak|old|zip|tar|gz|7z|rar|pem|key|pfx|log|conf|cfg|ini)\b"]},
    tags=["external", "ext-p4", "web", "archive", "active"],
    flow_inputs={"archive_urls": ("P1-S4", "archive_interesting"), "inscope_names": ("P2-S1", "ext_inscope_names")},
)

# ============================== P5 ==============================
step(
    sid="P5-S1", phase="P5", name="EXT P5-S1 Nuclei Vulnerability Scan",
    objective="Scan the full external web surface with current templates (exposures, misconfigurations, CVEs, panels) and classify findings.",
    depends_on=["P4-S1 (web URLs)"], feeds_into=["P5-S2", "P6-S1"],
    tools=["nuclei"], scope_required=True, severity_hint="varies",
    evidence_required="Nuclei + template version, run date, JSON/line output",
    checks=[
        "Templates updated before run (nuclei -ut); engine + template version recorded (printed at top of output)",
        "Run date recorded",
        "Categories covered: exposures, misconfiguration, cves, exposed-panels, technologies",
        "default-logins category only with written approval (excluded by default)",
        "Intrusive / DoS / fuzz templates excluded unless approved",
        "Each match classified: true positive / false positive / needs manual check (into P5-S2)",
    ],
    opsec=["Rate-limited (ext_req_rate)", "Stop on WAF block storms or service errors: " + GLOBAL_STOP],
    validation="Scan finished for all WEB_URLs with recorded template version; every match triaged in P5-S2.",
    cmd=(
        "echo \"RUN_DATE $(date -u +%Y-%m-%dT%H:%M:%SZ)\"; nuclei -version 2>&1 | tail -1; nuclei -tv 2>&1 | tail -1; "
        "printf '%s\\n' {web_urls} > ext_web_urls.txt; nuclei -l ext_web_urls.txt -etags {ext_nuclei_exclude_tags} -rl {ext_req_rate} -c 10 -silent -nc"
    ),
    regex={"nuclei_hits": r"^\[([\w.-]+)\] \[\w+\] \[(?:critical|high|medium)\]"},
    timeout=21600, alerts={"critical": [r"\[critical\]"], "warning": [r"\[high\]", r"\[medium\]"]},
    tags=["external", "ext-p5", "vulnscan", "active"],
    flow_inputs={"web_urls": ("P4-S1", "ext_web_urls")},
)
step(
    sid="P5-S2", phase="P5", name="EXT P5-S2 Nessus Triage and False-Positive Validation",
    objective="Manually verify every Nessus (and carried-over) finding; drop false positives and banner/version-only guesses that cannot be verified.",
    depends_on=["Nessus scan of P2-S1 targets", "P3-S3", "P3-S4", "P4-S4", "P5-S1"], feeds_into=["P6-S1"],
    tools=["Nessus CSV export", "python3 triage helper", "manual verification"], scope_required=True, severity_hint="varies",
    evidence_required="Per retained finding: verification method, evidence, confidence (confirmed / likely / unverified)",
    checks=[
        "Nessus policy: safe checks ON, scan source/time recorded, same rate limits as P3",
        "Every Critical/High/Medium finding reviewed",
        "Findings relying only on banner/version (VERSION_ONLY) re-verified by behaviour or backport check",
        "False positives removed with reason",
        "Retained findings: verification method + evidence + confidence recorded",
        "Only genuine, in-scope, rule-compliant risks carried to reporting",
    ],
    opsec=["Verification must stay non-destructive; same approval rules as P4-S4"],
    validation="Every finding has verdict TP/FP + method + confidence; FP list with reasons kept.",
    cmd=(
        "python3 - '{ext_nessus_export}' <<'PY'\n"
        "import sys, csv, re\n"
        "rx = re.compile(r'(installed version|reported version|fixed version|banner|version :)', re.I)\n"
        "for r in csv.DictReader(open(sys.argv[1], newline='', encoding='utf-8', errors='replace')):\n"
        "    risk = r.get('Risk') or r.get('Severity') or ''\n"
        "    if risk not in ('Critical', 'High', 'Medium'): continue\n"
        "    tag = 'VERSION_ONLY' if rx.search(r.get('Plugin Output', '')) else 'BEHAVIOUR'\n"
        "    print('VERIFY', risk, '%s:%s' % (r.get('Host'), r.get('Port')), tag, r.get('Plugin ID'), r.get('CVE', '') or '-', '|', r.get('Name'))\n"
        "PY"
    ),
    regex={"version_only": r"^VERIFY \S+ (\S+) VERSION_ONLY"},
    timeout=300, alerts={"warning": ["VERSION_ONLY"]},
    tags=["external", "ext-p5", "vulnscan", "triage", "manual"],
)

# ============================== P6 ==============================
step(
    sid="P6-S1", phase="P6", name="EXT P6-S1 Consolidation and Reporting Hand-off",
    objective="Merge and deduplicate findings across phases, score, attach redacted evidence and remediation, and document every scope exception and OpSec incident.",
    depends_on=["P0-S1..P5-S2 (all)"], feeds_into=["Report"],
    tools=["bars Findings", "report template"], scope_required=False, severity_hint="n/a",
    evidence_required="Final findings list; exceptions/incident log",
    checks=[
        "Findings merged and deduplicated across phases, mapped to asset + step ID",
        "Severity assigned (CVSS v3.1/v4 or agreed rubric) with business-impact context",
        "Evidence attached, redacted for PII and secrets, with reproduction steps",
        "Remediation recommendation per finding",
        "Out-of-scope hits (P2-S1 OUT list, OFFSCOPE names) documented as observations, untested",
        "Stopped tests, lockout events and OpSec incidents documented with time + reason",
        "Skipped / n/a steps listed with justification",
        "Temporary data (scan files, PII) handled per data handling plan (retention/deletion)",
    ],
    opsec=["Report contains no plaintext credentials or unredacted PII"],
    validation="Every retained finding has asset, step ID, severity, evidence, remediation; exceptions log complete; lead sign-off.",
    manual=True, tags=["external", "ext-p6", "reporting", "manual"],
)


# ---------------------------------------------------------------- build
def by_sid(sid):
    return next(s for s in STEPS if s["sid"] == sid)


def description(s):
    lines = [
        s["objective"],
        "",
        "STEP: %s | PHASE: %s | scope_required: %s | severity_hint: %s | mode: %s" % (
            s["sid"], s["phase"], str(s["scope_required"]).lower(), s["severity_hint"],
            "manual" if s["manual"] or "manual" in s["tags"] else "automated"),
        "DEPENDS_ON: " + (", ".join(s["depends_on"]) or "-"),
        "FEEDS_INTO: " + (", ".join(s["feeds_into"]) or "-"),
        "TOOLS (examples, swappable): " + ", ".join(s["tools"]),
        "",
        "CHECK CRITERIA:",
    ] + ["[ ] " + c for c in s["checks"]] + ["", "OPSEC / RESTRICTIONS:"] + ["! " + o for o in s["opsec"]] + [
        "",
        "DONE WHEN (validation_output): " + s["validation"],
        "EVIDENCE REQUIRED: " + s["evidence_required"],
    ]
    return "\n".join(lines)


def item(s):
    tags = list(dict.fromkeys(s["tags"] + ["step-" + s["sid"].lower(),
                                         "scope-required" if s["scope_required"] else "no-target-contact"]))
    return {
        "name": s["name"],
        "description": description(s),
        "command_template": s["cmd"],
        "output_regex": s["regex"],
        "variables": {},
        "input_definitions": {},
        "storage_policy": {},
        "parameter_schema": s["params"],
        "target_filter": {},
        "timeout": s["timeout"],
        "alert_patterns": s["alerts"],
        "finding_template": s["finding"],
        "tags": tags,
        "enabled": True,
    }


def flow(name, desc, steps, tags):
    out = []
    for i, s in enumerate(steps):
        mapping = {k: "{%s.%s}" % (by_sid(src)["name"], key) for k, (src, key) in s["flow_inputs"].items()}
        out.append({
            "checklist_item_name": s["name"],
            "order_index": i,
            "input_mapping": mapping,
            "condition": None,
            "on_failure": s["on_failure"],
            "timeout_override": None,
            "target_mode": "inherit",
            "target_filter": {},
            "ui_position": {"x": 250, "y": i * 120},
        })
    return {
        "name": name, "description": desc, "is_default_import": False, "requires_auth": False,
        "tags": tags,
        "flow_definition": {"edges": [{"source": i, "target": i + 1} for i in range(len(steps) - 1)]},
        "steps": out,
    }


def build():
    groups = []
    for pid, gname, gdesc in PHASES:
        groups.append({
            "name": gname, "description": gdesc, "is_default_import": False,
            "requires_auth": False, "speed_profile": "stealth" if pid in ("P3", "P4", "P5") else "default",
            "items": [item(s) for s in STEPS if s["phase"] == pid],
        })
    auto = [s for s in STEPS if not s["manual"] and "manual" not in s["tags"]]
    passive = [s for s in auto if s["passive_flow"]]
    flows = [
        flow("EXT Passive Recon Flow",
             "P0 gate + P1 passive OSINT + P2 DNS. No port scans or web probes. Safe to run before the active window opens (gate still enforces RoE/window).",
             passive, ["external", "passive", "sequential"]),
        flow("EXT Full External Assessment Pipeline",
             "P0 gate -> P1 OSINT -> P2 DNS + scope classification -> P3 ports/services/TLS -> P4 web/panels -> P5 nuclei. Manual steps (dorking, leaks, default creds, Nessus triage, reporting) run outside the flow.",
             auto, ["external", "pipeline", "sequential"]),
    ]
    variables = [{"key": k, "value": v, "var_type": t, "description": d, "is_default_import": False}
                 for k, v, t, d in VARIABLES]
    return {"checklists": {"groups": groups}, "flows": {"flows": flows}, "variables": {"variables": variables}}


# ---------------------------------------------------------------- markdown
def markdown(data):
    md = ["# External Network Assessment Checklist (bars)", "",
          "Generated from the same source as `external-network-library.json` (bars library export format: "
          "import via Library > Import or `python backend/load_library_seed.py external-network-library.json`, "
          "the script REPLACES the whole library — prefer the UI import, which appends).", "",
          "## Global rules", "",
          "- Tool names are examples; every step states WHAT to check so tools can be swapped.",
          "- Any step that touches an asset requires the asset to be confirmed in scope (`scope_required: true`). "
          "Out-of-scope assets go into bars **Excluded hosts**; flows drop them automatically.",
          "- Every step has a `DONE WHEN` condition.",
          "- " + GLOBAL_STOP, "",
          "## Field mapping (requested schema -> bars schema)", "",
          "bars already has a checklist schema, so it is reproduced exactly; no new fields were added. Requested fields map as follows:", "",
          "| Requested field | Where it lives in bars |",
          "|---|---|",
          "| `phase` | checklist group (`EXT P<n> - ...`) + tag `ext-p<n>` |",
          "| `step_id` | item name prefix (`EXT P1-S6 ...`) + tag `step-p1-s6` |",
          "| `step_name` | item `name` |",
          "| `objective` | first line of item `description` (the line the UI list shows) |",
          "| `check_criteria` | `[ ]` lines in `description` (CHECK CRITERIA block) |",
          "| `opsec_note` | `!` lines in `description` (OPSEC / RESTRICTIONS block) |",
          "| `validation_output` | `DONE WHEN` line in `description`; machine side: `output_regex` keys + `alert_patterns` |",
          "| `depends_on` / `feeds_into` | `DEPENDS_ON` / `FEEDS_INTO` lines; executable chain = flow step `input_mapping` (`{<item name>.<output key>}`) |",
          "| `tools` | `TOOLS` line + the `command_template` |",
          "| `scope_required` | description header + tag `scope-required` / `no-target-contact` |",
          "| `severity_hint` | description header; `finding_template.severity` where an item maps to one finding type |",
          "| `status` | bars execution state: todo = no execution, in_progress = running, done = completed (exit 0), skipped = flow-skipped, n/a = item disabled (`enabled: false`) |",
          "| `evidence_required` | `EVIDENCE REQUIRED` line; evidence attaches to the execution / finding |",
          "",
          "## Variables to set before running", "",
          "| Key | Type | Default | Purpose |", "|---|---|---|---|"]
    for v in data["variables"]["variables"]:
        md.append("| `%s` | %s | `%s` | %s |" % (v["key"], v["var_type"], v["value"] or "", v["description"]))
    md += ["", "## Data flow (what each step consumes and hands on)", "",
           "| Step | Consumes | Produces (output key) | Feeds |", "|---|---|---|---|"]
    for s in STEPS:
        cons = ", ".join("`%s.%s`" % (src, key) for _, (src, key) in s["flow_inputs"].items()) or ", ".join(s["depends_on"]) or "-"
        prod = ", ".join("`%s`" % k for k in s["regex"]) or "(manual record)"
        md.append("| %s | %s | %s | %s |" % (s["sid"], cons, prod, ", ".join(s["feeds_into"]) or "-"))
    md += ["", "```",
           "P0-S1 gate ──> P1-S4 archive ──hosts──> P1-S6 subdomains ──> P2-S1 resolve + IN/OUT classify",
           "                    │                                          ├─ ext_inscope_ips ──> P2-S2 PTR",
           "                    │                                          ├─ ext_cname_hosts ──> P2-S4 takeover",
           "                    │                                          ├─ ext_scan_targets ─> P3-S1 TCP / P3-S2 UDP",
           "                    │                                          └─ ext_inscope_names ┐",
           "                    └─ archive_interesting ───────────────────────────────> P4-S5 ◄─┤",
           "P3-S1 ── ext_live_ips + ext_open_ports ──> P3-S3 versions, P3-S4 hardening, P3-S5 TLS, P4-S1 web ◄┘",
           "P4-S1 ── ext_web_urls ──> P4-S2 panels ──> P4-S3 default creds (manual) ; P4-S4 KEV/CVE ; P5-S1 nuclei",
           "P3-S3/P3-S4/P4-S4/P5-S1 + Nessus ──> P5-S2 FP triage ──> P6-S1 report",
           "```", "",
           "Flows shipped: **EXT Passive Recon Flow** (P0-P2, no port/web probing) and "
           "**EXT Full External Assessment Pipeline** (all automated steps). Manual steps "
           "(P1-S3 dorking, P1-S7 leaks, P4-S3 default creds, P5-S2 triage, P6-S1 reporting) run outside flows; "
           "P0-S1 has `on_failure: stop`, so a failed gate halts the whole flow.", ""]
    for pid, gname, gdesc in PHASES:
        md += ["## " + gname.replace("EXT ", ""), "", "_" + gdesc + "_", ""]
        for s in [x for x in STEPS if x["phase"] == pid]:
            md += ["### %s — %s" % (s["sid"], s["name"].split(" ", 2)[2]), "",
                   "**Objective:** " + s["objective"], "",
                   "- **scope_required:** %s · **severity_hint:** %s · **mode:** %s" % (
                       str(s["scope_required"]).lower(), s["severity_hint"],
                       "manual" if s["manual"] or "manual" in s["tags"] else "automated"),
                   "- **depends_on:** " + (", ".join(s["depends_on"]) or "-"),
                   "- **feeds_into:** " + (", ".join(s["feeds_into"]) or "-"),
                   "- **tools (examples):** " + ", ".join(s["tools"]),
                   "", "**Check criteria**", ""]
            md += ["- [ ] " + c for c in s["checks"]]
            md += ["", "**OpSec / restrictions**", ""] + ["> ⚠ " + o + "  " for o in s["opsec"]]
            md += ["", "**Done when (validation_output):** " + s["validation"], "",
                   "**Evidence required:** " + s["evidence_required"], ""]
            if not s["manual"]:
                md += ["<details><summary>bars command</summary>", "", "```sh", s["cmd"], "```", "", "</details>", ""]
    return "\n".join(md)


if __name__ == "__main__":
    data = build()
    with open(sys.argv[1], "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    with open(sys.argv[2], "w") as f:
        f.write(markdown(data))
    print("items=%d flows=%d vars=%d" % (sum(len(g["items"]) for g in data["checklists"]["groups"]),
                                         len(data["flows"]["flows"]), len(data["variables"]["variables"])))
