# Royce Provision — User Guide

This guide is for whoever is actually running client onboarding — Royce staff bringing a new
client onto Royce Cloud. For *why* it's built this way, see `architecture.md` instead; this doc
only covers *how to use what exists today*.

**What this app is not:** a UI, a "Client Onboarding" doctype, or a site-creation tool. There's one
command. It assumes a site already exists and picks up from there.

---

## 1. Prerequisites

| Item | Where to check | Notes |
|---|---|---|
| A site for this client | — | Create it yourself first: `bench new-site [client-site]` — this app doesn't create sites |
| `royce_provision` installed on that site | `bench --site [site] list-apps` | `bench --site [site] install-app royce_provision` if not |
| A submitted Payroll Rates record | `royce_payroll_ke`'s own Payroll Rates list | Needed if onboarding the `payroll` product — see `royce_payroll_ke`'s user guide section 2 if none exists yet |
| Company name, abbreviation, and which products were bought | — | Decided before you run anything |

---

## 2. Onboarding a client

One command, run once per client, from a terminal:

```
bench --site [client-site] execute royce_provision.royce_provision.onboarding.onboard_client --kwargs '{"company": "Acme Ltd", "abbr": "ACM", "apps": ["payroll"]}'
```

Bought both products? List both:

```
bench --site [client-site] execute royce_provision.royce_provision.onboarding.onboard_client --kwargs '{"company": "Acme Ltd", "abbr": "ACM", "apps": ["payroll", "etims"]}'
```

### Parameters

| Parameter | Required | Default | What it means |
|---|---|---|---|
| `company` | Yes | — | The Company's full display name, e.g. `"Acme Ltd"` |
| `abbr` | Yes | — | Short company abbreviation. Shows up in every account name this creates (`PAYE Payable - ACM`) — pick it deliberately, it's not easily changed later |
| `apps` | Yes | — | List of products the client bought: `"payroll"`, `"etims"`, or both |
| `country` | No | `"Kenya"` | Only change this if you're genuinely onboarding a non-Kenya company — everything downstream assumes Kenya |
| `currency` | No | `"KES"` | Same caveat as `country` |
| `rates` | No | whichever Payroll Rates is currently effective | Pass a specific version name (e.g. `"2026-01-01"`) to pin onboarding to a particular rate set instead of "whatever's active today" |

---

## 3. What actually happens, in order

1. **Installs whichever apps the purchased products need**, skipping any already installed.
   `hrms` comes along automatically with `payroll` — it's declared as a dependency in
   `royce_payroll_ke`'s own `hooks.py`, not something this app tracks separately.
2. **Creates the Company**, if it doesn't already exist — with the `country` given, which is what
   makes ERPNext apply the standard Chart of Accounts template. This is the step that makes
   `royce_payroll_ke`'s own assumption (that the right parent accounts already exist) actually
   true for a brand new client.
3. **For `payroll`**: runs `royce_payroll_ke.setup.provision(company)`, then
   `royce_payroll_ke.setup.verify(company)`. If `verify()` finds anything wrong, the whole call
   raises — the client is **not** marked onboarded on a provisioning step that didn't actually work.
4. **For `etims`**: installs `royce_etims` only. Nothing is provisioned automatically — KRA
   registration needs a real TIN and Apigee credentials that only a human can enter. See
   `royce_etims`'s own architecture doc for that checklist.
5. Returns a structured summary of every step taken.

---

## 4. Reading the result

A successful `payroll`-only run looks like this:

```json
{
  "company": "Acme Ltd",
  "apps": ["payroll"],
  "steps": {
    "install_apps": {"royce_payroll_ke": "installed"},
    "company": "Acme Ltd",
    "payroll_provision": {
      "rates": "2026-01-01",
      "income_tax_slab": "Kenya PAYE Placeholder 2026",
      "payroll_period": "Acme Ltd Payroll 2026",
      "salary_structure": "ACM Payroll Structure 2026-01-01"
    },
    "payroll_verify": {
      "company": "Acme Ltd",
      "rates": "2026-01-01",
      "components_checked": 22,
      "structure": "ACM Payroll Structure 2026-01-01",
      "status": "PASS"
    }
  },
  "status": "live"
}
```

`status: "live"` only appears if every step actually completed — if anything failed, you'll have an
exception and a partial `steps` dict instead, not a false "live".

---

## 5. Onboarding a second product for an existing client

`ensure_company()` only creates the Company if it's missing — if you're adding `etims` for a client
who already has `payroll` (or vice versa), run the same command with the same `company`/`abbr` and
the new product in `apps`. The existing Company is left untouched; onboarding just proceeds
straight to installing and provisioning the new product.

---

## 6. Finishing eTIMS setup

`onboard_client()` installs `royce_etims` but does not configure it — that's not a gap, it's a
limit of what can be automated without a human present. Once the app is installed, whoever's
handling this client needs to work through `royce_etims`'s own manual checklist (TIN, Apigee
credentials, branch/device registration) — see `royce_etims/docs/architecture.md`'s onboarding flow.

---

## 7. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `"At least one product is required — got none."` | `apps` was empty or not passed |
| `"Unknown product 'X'. Known products: ['payroll', 'etims']"` | A typo in `apps` — values must be exactly `payroll` or `etims` |
| `"Expected parent account ... not found"` | Bubbled up from `royce_payroll_ke.setup.provision()` — the Company's Chart of Accounts doesn't have the standard Kenya groups. Shouldn't happen if `country` was left as `"Kenya"`; check that field on the Company if it does |
| `"No effective, submitted Payroll Rates record found."` | No Payroll Rates exists yet, or none is both submitted and dated on/before today. Create one first — see `royce_payroll_ke`'s user guide section 2 |
| `"Payroll provisioning verification failed for ..."` with a list of problems | `verify()` caught real drift after provisioning — read the specific list, it names exactly what's wrong. The client is not marked `"live"` when this happens; fix what's listed and re-run |
| `AttributeError: module 'frappe' has no attribute 'installer'` | Shouldn't happen — this was a real bug caught and fixed during development (see `architecture.md` section 3). If it resurfaces, something in the environment changed; it's not something to work around in this app |
