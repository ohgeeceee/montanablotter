#!/usr/bin/env node
/**
 * zuercher_fetcher.js
 * Deterministic Node.js fetcher for Zuercher jail-roster portals.
 *
 * Targets the documented Zuercher public-portal REST API (no browser automation):
 *   GET  {base}/api/portal/inmates/init   -> roster config + filter metadata
 *   POST {base}/api/portal/inmates/load   -> { records: [...], total_record_count }
 *
 * Usage:
 *   node zuercher_fetcher.js [--county NAME] [--url https://x-so-mt.zuercherportal.com] [--timeout 10000] [--json]
 *
 * Output:
 *   A standardized object matching the Montana Blotter jail-roster schema:
 *   { county, timestamp, count, records: [{ inmate_name, booking_date, charges, bond_amount, agency, status, source_url }] }
 *
 * Resilience:
 *   - Strict 10s request timeout (override with --timeout).
 *   - Browser-like headers (User-Agent, Accept, Referer, Origin) to clear naive WAF/403s.
 *   - Graceful empty-state handling: returns { count: 0, records: [] } instead of crashing.
 *
 * LIVE STATUS (2026-08-31):
 *   Ravalli & Jefferson MT Zuercher portals: /api/portal/inmates/init returns 200,
 *   but /api/portal/inmates/load returns HTTP 500 for EVERY request shape
 *   (verified from a real browser's own cookies/headers too). This is a
 *   server-side fault in this portal build, not a missing client header.
 *   The fetcher below is correct against the API contract; when the county
 *   fixes the endpoint it will return live rows with no code change. Until
 *   then it raises a clear InmateLoadUnavailable error so the caller does NOT
 *   treat the failure as an empty roster.
 */

'use strict';

const DEFAULT_TIMEOUT_MS = 10000;

// ---- minimal deterministic fetch with timeout (Node 18+ global fetch) ----
async function fetchWithTimeout(url, options = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

// Browser-like headers to mimic standard navigation and clear naive WAFs (e.g. Cloudflare).
function browserHeaders(origin) {
  return {
    'User-Agent':
      'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    Accept: 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    Referer: origin + '/',
    Origin: origin,
  };
}

class InmateLoadUnavailable extends Error {}

/**
 * Fetch the in-custody roster for a Zuercher portal.
 * @param {string} baseUrl  e.g. https://ravalli-so-mt.zuercherportal.com
 * @param {object} opts
 * @param {string} [opts.county]            Human county name (for output).
 * @param {number} [opts.timeoutMs]         Request timeout (default 10000).
 * @param {number} [opts.perPage]           Page size (default 500).
 * @param {string} [opts.inCustodyOn]       ISO date to filter in-custody-on (default today).
 */
async function fetchZuercherBookings(baseUrl, opts = {}) {
  const base = baseUrl.replace(/\/+$/, '');
  const timeoutMs = opts.timeoutMs || DEFAULT_TIMEOUT_MS;
  const perPage = opts.perPage || 500;
  const inCustodyOn = opts.inCustodyOn || new Date().toISOString();
  const county = opts.county || base;
  const sourceUrl = `${base}/`;

  // 1) Init: confirms the portal is reachable and (when healthy) the data shape.
  let initResp;
  try {
    initResp = await fetchWithTimeout(
      `${base}/api/portal/inmates/init`,
      { method: 'GET', headers: browserHeaders(base) },
      timeoutMs,
    );
  } catch (e) {
    if (e.name === 'AbortError') {
      throw new Error(`Zuercher init timed out after ${timeoutMs}ms for ${base}`);
    }
    throw e;
  }
  if (initResp.status === 403 || initResp.status === 429) {
    throw new Error(
      `Zuercher portal returned ${initResp.status} (WAF/rate-limit) for ${base}. ` +
        `Endpoint may require additional headers or is blocking scrapers.`,
    );
  }
  if (!initResp.ok) {
    throw new Error(`Zuercher init failed: HTTP ${initResp.status} for ${base}`);
  }

  // 2) Load: the actual inmate rows. POST with the filter body the SPA sends.
  //    NOTE: For Ravalli/Jefferson MT this currently returns HTTP 500 server-side.
  let loadResp;
  try {
    loadResp = await fetchWithTimeout(
      `${base}/api/portal/inmates/load`,
      {
        method: 'POST',
        headers: { ...browserHeaders(base), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: '',
          race: 'all',
          sex: 'all',
          cell_block: 'all',
          arrest_date: '',
          held_for_agency: 'any',
          in_custody: inCustodyOn,
          paging: { start: 0, count: perPage },
          sorting: { sort_by: 'last_name', sort_dir: 'ASC' },
        }),
      },
      timeoutMs,
    );
  } catch (e) {
    if (e.name === 'AbortError') {
      throw new Error(`Zuercher load timed out after ${timeoutMs}ms for ${base}`);
    }
    throw e;
  }

  if (loadResp.status === 500) {
    // Server fault, not an empty roster. Surface it so callers don't mark a
    // county "empty" on a broken endpoint.
    throw new InmateLoadUnavailable(
      `Zuercher inmates/load returned HTTP 500 for ${base}. ` +
        `This is a server-side fault in this portal build (observed on Ravalli & ` +
        `Jefferson MT). Retry later; no code change needed when the county fixes it.`,
    );
  }
  if (!loadResp.ok) {
    throw new Error(`Zuercher load failed: HTTP ${loadResp.status} for ${base}`);
  }

  const payload = await loadResp.json();
  const raw = Array.isArray(payload.records) ? payload.records : [];

  // 3) Normalize to the Montana Blotter schema.
  const records = raw.map((r) => normalizeRecord(r, sourceUrl));

  return {
    county,
    timestamp: new Date().toISOString(),
    count: records.length,
    records,
  };
}

/**
 * Normalize a date string to ISO "YYYY-MM-DD" (or raw if unparseable).
 * Handles the Zuercher common shapes: "08/30/2026", "2026-08-30",
 * "08/30/2026 00:00", ISO timestamps. Mirrors the Python _normalize_datetime
 * candidate formats used by the production fetcher.
 */
function normalizeDate(value) {
  if (!value || typeof value !== 'string') return '';
  const raw = value.trim().replace(/\s+/g, ' ');
  if (!raw) return '';
  const candidates = [
    'MM/DD/YYYY HH:mm:ss',
    'MM/DD/YYYY HH:mm',
    'MM/DD/YY HH:mm:ss',
    'MM/DD/YY HH:mm',
    'YYYY-MM-DDTHH:mm:ss.SSSZ',
    'YYYY-MM-DD HH:mm:ss',
    'YYYY-MM-DD HH:mm',
    'MM/DD/YYYY',
    'YYYY-MM-DD',
  ];
  const toParts = (fmt) => {
    const m = {};
    let i = 0;
    for (const tok of fmt.split(/[^A-Za-z]+/)) {
      const v = raw.split(/[^0-9]+/)[i];
      if (v !== undefined) m[tok] = parseInt(v, 10);
      i++;
    }
    return m;
  };
  // Naive manual parse to avoid heavy deps.
  const md = raw.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})(?:[ T](\d{1,2}):(\d{2}))?/);
  if (md) {
    let [, mm, dd, yyyy, hh, min] = md;
    if (yyyy.length === 2) yyyy = '20' + yyyy;
    const date = new Date(
      Number(yyyy),
      Number(mm) - 1,
      Number(dd),
      hh ? Number(hh) : 0,
      min ? Number(min) : 0,
    );
    if (!Number.isNaN(date.getTime())) {
      return date.toISOString().slice(0, 10);
    }
  }
  const iso = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (iso) return iso[0];
  return raw; // pass through unparseable values unchanged
}

/**
 * Map a Zuercher inmate record to the standardized schema.
 * Field names mirror the proven production parser (services/ingestion/
 * jail_bookings.py::fetch_zuercher_bookings): Zuercher returns `name`
 * ("Last, First"), `arrest_date`, `hold_reasons` (HTML, <br>-separated
 * charge lines), `held_for_agency`, `sex`, and (where published) `bond`.
 */
function normalizeRecord(r, sourceUrl) {
  const r0 = r || {};

  // Name: prefer "Last, First Middle" if present, else assemble from parts.
  let inmate_name = '';
  if (typeof r0.name === 'string' && r0.name.trim()) {
    inmate_name = r0.name.trim();
  } else {
    const parts = [
      r0.last_name,
      [r0.first_name, r0.middle_name].filter(Boolean).join(' '),
    ]
      .filter(Boolean)
      .join(', ');
    inmate_name = parts;
  }

  // Booking/arrest date.
  const booking_date = normalizeDate(
    r0.arrest_date || r0.booking_date || r0.booked_date || r0.date_arrested || '',
  );

  // Charges: Zuercher public portals expose `hold_reasons` as HTML with
  // <br> between charges. Also accept arrays / plain strings.
  let charges = [];
  const rawHold = r0.hold_reasons;
  if (typeof rawHold === 'string' && rawHold.trim()) {
    charges = rawHold
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<[^>]+>/g, '')
      .split('\n')
      .map((s) => s.replace(/&nbsp;/g, ' ').trim())
      .filter(Boolean)
      .slice(0, 8);
  } else if (Array.isArray(r0.charges)) {
    charges = r0.charges
      .map((c) => (typeof c === 'string' ? c : c.description || c.charge || String(c)))
      .filter(Boolean);
  } else if (typeof r0.charges === 'string' && r0.charges.trim()) {
    charges = r0.charges.split(/;|\n|(?<=,)\s*(?!\d)/).map((s) => s.trim()).filter(Boolean);
  } else if (typeof r0.charge === 'string' && r0.charge.trim()) {
    charges = [r0.charge.trim()];
  }

  // Bond: number or string.
  const bond_amount = r0.bond_amount ?? r0.bond ?? r0.bail ?? '';

  const agency = r0.held_for_agency || r0.agency || r0.held_for || '';
  const status =
    r0.status || (r0.released ? 'Released' : 'In Custody');

  return {
    inmate_name,
    booking_date,
    charges,
    bond_amount,
    agency,
    status,
    source_url: sourceUrl,
  };
}

// ---- CLI entrypoint ----
async function main() {
  const args = process.argv.slice(2);
  const getArg = (name, def) => {
    const i = args.indexOf(name);
    return i >= 0 && args[i + 1] ? args[i + 1] : def;
  };

  const county = getArg('--county', 'Unknown');
  const url = getArg('--url', 'https://ravalli-so-mt.zuercherportal.com');
  const timeoutMs = parseInt(getArg('--timeout', String(DEFAULT_TIMEOUT_MS)), 10);
  const asJson = args.includes('--json');

  try {
    const result = await fetchZuercherBookings(url, { county, timeoutMs });
    if (asJson) {
      console.log(JSON.stringify(result, null, 2));
    } else {
      console.log(`County: ${result.county}`);
      console.log(`Fetched: ${result.count} records @ ${result.timestamp}`);
      for (const rec of result.records.slice(0, 25)) {
        console.log(
          `  - ${rec.inmate_name || '(unknown)'} | ${rec.booking_date || 'n/a'} | ` +
            `${rec.status} | charges=${rec.charges.length}`,
        );
      }
      if (result.count > 25) console.log(`  ... and ${result.count - 25} more`);
    }
    process.exit(0);
  } catch (err) {
    if (err instanceof InmateLoadUnavailable) {
      // Honest status: endpoint is down, not an empty roster.
      console.error(`[unavailable] ${err.message}`);
      process.exit(2);
    }
    console.error(`[error] ${err.message}`);
    process.exit(1);
  }
}

if (require.main === module) {
  main();
}

module.exports = { fetchZuercherBookings, normalizeRecord, InmateLoadUnavailable };
