# Jail Roster Coverage — Tier-2 Broken Sources Assessment

Generated: 2026-08-31. Counties with a configured `jail_booking_sources` row but
no successful ingest. Classified by fixability. Root causes from `latest_error`
in blotter.db + earlier live curl re-scouts.

## Summary table

| County | Root cause | Class | Concrete next step |
|--------|-----------|-------|--------------------|
| Cascade | Roster PDF behind SharePoint sign-in | AGENCY-ACTION | FOIA / ask county to publish public PDF; Zuercher not used here |
| Carbon | Zuercher portal no public API (HTML maintenance) | AGENCY-ACTION | Re-test portal; if stable, rebuild Zuercher adapter |
| Gallatin | Zuercher portal returns HTML (maintenance mode) | AGENCY-ACTION | Same as Carbon |
| Broadwater | Ingest-box TCP to 34.94.199.155:443 fails | NETWORK/US-FIXABLE | Test roster URL from alt egress/proxy; if reachable, add proxy to fetcher |
| Big Horn | CitizenRIMS public inmate access disabled by agency | AGENCY-ACTION | FOIA / request re-enable |
| Madison | No roster link on sheriff site (re-scout 2026-08-31) | AGENCY-ACTION | FOIA — sheriff page is landing only |
| Meagher | Cloudflare 403 (meaghercountyjail.org, re-scout 2026-08-31) | DEFERRED | Cloudflare bypass (shares Powder River pattern) |
| Rosebud | Cloudflare 403 (rosebudcountyjailmt.org, re-scout 2026-08-31) | DEFERRED | Cloudflare bypass (shares Powder River pattern) |
| Stillwater | Cloudflare 403 (stillwatercountyjailmt.org, re-scout 2026-08-31) | DEFERRED | Cloudflare bypass (shares Powder River pattern) |
| Wheatland | No official roster; only third-party aggregator (re-scout 2026-08-31) | AGENCY-ACTION | FOIA — only montanajailroster.com (excluded) |
| Chouteau | Wix-hosted rotating PDF (re-discovery works) | US-FIXABLE (DONE) | Scheduled; fetcher re-discovers link each run |

## Detail

### Cascade — SharePoint auth (AGENCY-ACTION)
The configured Inmate Roster page serves a SharePoint login wall. No public
data without credentials. Leave is_enabled=1 so it records the failure cleanly
and resumes if the county opens the PDF. Outreach: ask the county to publish an
unauthenticated PDF.

### Carbon / Gallatin — Zuercher portal (AGENCY-ACTION)
Both point at `*-so-mt.zuercherportal.com`. Carbon returns no public API;
Gallatin returns the portal HTML (maintenance). If the portal comes back up and
exposes a public inmate JSON endpoint, rebuild `fetch_zuercher_bookings` against
the live schema. Low effort once the endpoint is reachable.

### Broadwater — network block from ingest box (NETWORK/US-FIXABLE)
`https://www.broadwatercountysheriff.org/roster.php` is reachable from a normal
browser but TCP-connect fails from this VPS (34.94.199.155:443). The parser is
ready. Next step: test the URL from an egress that isn't blocked, or add an
optional `MB_HTTPS_PROXY` to the fetcher and point it at a residential proxy.

### Big Horn — CitizenRIMS disabled (AGENCY-ACTION)
Agency turned off public inmate access. Only path is outreach.

### Madison / Meagher / Rosebud / Stillwater / Wheatland — re-scouted 2026-08-31 (NOT 404)

Earlier `latest_error` said 404, but a live re-scout shows the real situation:

- **Madison** — sheriff page (madisoncountymt.gov/154/Sheriffs-Office) has NO roster/inmate
  link at all. Landing page only. → FOIA candidate.
- **Meagher** — real roster at meaghercountyjail.org/inmate-search/ but **Cloudflare 403**
  even after headless render. Same class as Powder River. → Deferred.
- **Rosebud** — real roster at rosebudcountyjailmt.org but **Cloudflare 403** even after
  headless render. → Deferred.
- **Stillwater** — real roster at stillwatercountyjailmt.org but **Cloudflare 403** even
  after headless render. → Deferred.
- **Wheatland** — no official roster; only a third-party aggregator
  (montanajailroster.com) which is excluded per AGENTS.md (non-official, PII/ToS risk).
  → FOIA candidate.

Net: re-scouting these five yielded **zero** new fetchable official rosters. The
Cloudflare trio (Meagher/Rosebud/Stillwater) share the Powder River/Wibaux block
pattern — a single Cloudflare-bypass investment would cover all five if pursued.

### Chouteau — DONE
Wix-hosted PDF with a rotating URL. Fetcher re-discovers the link from the
landing page each run; scheduled in crontab.txt (daily 06:15). No action.

## Effort ranking (do these next)

1. Re-scout the 5 x 404 counties (Madison, Meagher, Rosebud, Stillwater,
   Wheatland) — pure research, likely yields 1–3 more real rosters.
2. Broadwater proxy test — one network tweak, parser ready.
3. Carbon/Gallatin Zuercher rebuild — pending portal stability.
4. Cascade / Big Horn / (FOIA 11) — agency outreach only.
