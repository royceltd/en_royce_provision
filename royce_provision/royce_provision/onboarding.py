# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Client onboarding orchestration for Royce Cloud — the thin bundler on top
of compliance apps that each provision themselves standalone. This app owns
nothing about payroll or eTIMS; it only knows how to call each one's own
provisioning routine in the right order, plus the company-level setup that
belongs to neither of them individually.

Deliberately not a UI, not a "Client Onboarding" doctype, not a site-creation
tool — matches the decision recorded early in royce_payroll_ke's own
architecture doc: v1 onboarding is Royce staff, by hand, via a bench command.
Site creation (`bench new-site`) stays a separate manual step; this module
picks up from there — an existing site, a company that may or may not exist
yet, and a list of what this client actually bought.

    bench --site [client-site] execute royce_provision.royce_provision.onboarding.onboard_client_cli \\
      --kwargs '{"company": "Acme Ltd", "abbr": "ACM", "apps": ["payroll", "etims", "talk"]}'

`apps` is a list drawn from PRODUCTS below. Only `"payroll"` has a real
provision() — both `royce_etims` and `royce_talk` install-only, honestly
reporting nothing further is automated rather than silently doing nothing
while claiming success. Neither can ever be a no-input call the way payroll
is: etims needs a human entering a real KRA TIN and Apigee credentials
(royce_etims's own architecture doc is explicit about that), and talk needs
a human entering a real RoyceTalk API key and Sender ID (there's no
provision()-worthy setup beyond what royce_talk's own after_install hook
already does automatically on install). Both PRODUCTS entries are filled in
with what exists today precisely so this stays the one place that knows
what "onboard a client" means — ready to wire in a real provision() call
for either the moment one exists, without restructuring anything here.

PRODUCTS["payroll"]["bootstrap"] handles the one real environment-level
prerequisite payroll has: a submitted Payroll Rates record. That's per-SITE,
not per-bench or per-company — Payroll Rates has no company filter, but each
client here gets their own site (own database), so a fresh site has no
record yet regardless of what other sites have. onboard_client() calls
seed_default_rates() itself before provision(), every time, safely (it
no-ops if this site already has one) — so this stays what it says on the
tin: one call, not one call plus a prerequisite you have to remember per
site.
"""

import frappe
from frappe import _
from frappe.installer import install_app

PRODUCTS = {
	"payroll": {
		"install": ["royce_payroll_ke"],
		# Payroll Rates is per-SITE, not per-bench: each client here gets their
		# own site (own database), so a fresh site has no rates record yet even
		# if others do. bootstrap runs first, every time - idempotent (no-ops if
		# this site already has an effective record), so this stays a true
		# one-call onboarding instead of relying on a separate step someone has
		# to remember per site.
		"bootstrap": "royce_payroll_ke.royce_payroll_ke.setup.seed_default_rates",
		"provision": "royce_payroll_ke.royce_payroll_ke.setup.provision",
		"verify": "royce_payroll_ke.royce_payroll_ke.setup.verify",
	},
	"etims": {
		"install": ["royce_etims"],
		"bootstrap": None,
		"provision": None,
		"verify": None,
	},
	"talk": {
		"install": ["royce_talk"],
		"bootstrap": None,
		"provision": None,
		"verify": None,
	},
}


def ensure_apps_installed(products):
	"""Install whichever Frappe apps each purchased product needs, if not
	already present. Cascades through each app's own required_apps
	automatically — frappe.installer.install_app is the same mechanism
	`bench install-app` itself uses, not a reimplementation of it."""
	installed = set(frappe.get_installed_apps())
	results = {}

	for product in products:
		if product not in PRODUCTS:
			frappe.throw(_("Unknown product '{0}'. Known products: {1}").format(product, list(PRODUCTS)))
		for app in PRODUCTS[product]["install"]:
			if app in installed:
				results[app] = "already installed"
				continue
			install_app(app)
			installed.add(app)
			results[app] = "installed"

	return results


def ensure_company(company, abbr, country="Kenya", currency="KES"):
	"""Create the Company if it doesn't exist yet. This matters more than it
	looks: creating a Company the normal way (through this API, same as the
	desk UI) is what makes ERPNext apply the standard Chart of Accounts
	template for the given country — the exact parent accounts
	(`Duties and Taxes`, `Indirect Expenses`, `Accounts Payable`)
	royce_payroll_ke's own `provision()` assumes already exist and throws a
	clear error about if they don't. Creating the Company properly here is
	what makes that assumption actually hold for a brand new client.

	Also seeds ERPNext's own setup-wizard fixtures before creating a genuinely
	new Company (Warehouse Types, default UOMs, Address Templates, ...) —
	found the hard way: `bench new-site --install-app erpnext` does NOT run
	these. They're only ever installed by the interactive Setup Wizard, which
	nothing in this programmatic flow goes through. Company creation's own
	hooks assume they already exist (e.g. the default "Goods In Transit"
	warehouse needs a "Transit" Warehouse Type record) and fail with a
	confusing LinkValidationError if they don't. `install()` is the same
	function the wizard itself calls for this step (not setup_complete() —
	that one also creates the Company, which would duplicate what this
	function already does its own simpler way).

	Only called in the branch that's actually about to insert a new Company,
	not unconditionally: `install()` IS idempotent (ignore_if_duplicate=True
	internally), but re-running it against a site that already has these
	fixtures logs a wall of harmless-but-noisy "NestedSetRecursionError"
	tracebacks for already-rooted nested-set doctypes (Item Group, Territory,
	...) — caught internally, never propagates, but there's no reason to
	pay that cost on every single onboard_client() call against an
	already-onboarded company (section 5 of the user guide: adding a second
	product later calls this again with the same company on purpose).

	Also marks the site as "setup complete" — another thing only the
	interactive Setup Wizard normally does, found the same way as the
	fixtures gap: every site this pipeline creates redirected every login to
	/desk/setup-wizard, even though the Company/CoA/fixtures were already
	genuinely in place. First attempt only marked "frappe" and "erpnext"
	complete, matching frappe.is_setup_complete()'s own check — but that
	wasn't the whole story: something client-side treats ANY installed app
	with is_setup_complete=0 as reason to keep re-mounting the wizard,
	which showed up as a real, reproducible infinite reload loop on /desk
	(confirmed in frontend access logs: setup_wizard.load_languages called
	once a second, forever) on a site whose OTHER apps — hrms, royce_talk,
	royce_etims, royce_payroll_ke, royce_provision itself — were all still
	flagged incomplete. Fix: mark every currently installed app, not a
	hardcoded pair, using the wizard's own one-line
	enable_setup_wizard_complete() rather than duplicating its body.

	Even with every app flagged complete, sites kept looping — traced to a
	THIRD, separate mechanism: frappe.boot.home_page (what the desk shell
	navigates to on load) comes from the "desktop:home_page" default,
	which frappe/utils/install.py sets to "setup-wizard" for every fresh
	site and only the interactive wizard's own disable_future_access()
	ever clears — to "workspace", plus System Settings.setup_complete,
	which turned out to be a distinct field from frappe.is_setup_complete()
	and was still 0 despite every Installed Application flag being 1.
	A site stuck with home_page="setup-wizard" boots straight into the
	wizard page every time, which (since bootinfo.setup_complete IS true)
	immediately tries to bounce away via a full-page navigate to "/apps" —
	not a real server route, so it 301s straight back to /desk, which
	boots into home_page="setup-wizard" again: a genuine infinite
	full-page-reload loop, confirmed in real time in access logs, not a
	browser-side artifact (survived a private/incognito window)."""
	if frappe.db.exists("Company", company):
		return company

	from erpnext.setup.setup_wizard.operations.install_fixtures import install as install_erpnext_fixtures
	from frappe.desk.page.setup_wizard.setup_wizard import disable_future_access, enable_setup_wizard_complete

	install_erpnext_fixtures(country)

	doc = frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": company,
			"abbr": abbr,
			"country": country,
			"default_currency": currency,
		}
	)
	doc.insert(ignore_permissions=True)

	for app_name in frappe.get_installed_apps():
		enable_setup_wizard_complete(app_name)

	disable_future_access()

	return doc.name


@frappe.whitelist(methods=["POST"])
def onboard_client(company, abbr, apps, country="Kenya", currency="KES", rates=None):
	"""The single place "onboard a client" happens from. Runs the company-level
	setup no individual compliance app owns, then calls each purchased
	product's own provisioning and verification — in that order, so a
	provisioning failure is caught before it's declared done, not after.

	Restricted to System Manager: architecture.md and the user guide are both
	explicit that v1 onboarding is Royce staff, by hand, not a self-serve or
	general API call. @frappe.whitelist() alone doesn't enforce that — it just
	requires *some* authenticated session — so this checks the role directly
	rather than leaving the restriction as prose only."""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted — onboard_client is restricted to System Managers."), frappe.PermissionError)

	if isinstance(apps, str):
		apps = frappe.parse_json(apps)
	if not apps:
		frappe.throw(_("At least one product is required — got none."))

	result = {"company": company, "apps": apps, "steps": {}}

	result["steps"]["install_apps"] = ensure_apps_installed(apps)
	result["steps"]["company"] = ensure_company(company, abbr, country, currency)

	for product in apps:
		spec = PRODUCTS[product]
		if not spec["provision"]:
			result["steps"][product] = (
				f"{spec['install'][0]} installed. No automated provisioning yet for '{product}' — "
				"see that app's own onboarding checklist."
			)
			continue

		if spec.get("bootstrap"):
			bootstrap_fn = frappe.get_attr(spec["bootstrap"])
			result["steps"][f"{product}_bootstrap"] = bootstrap_fn()

		provision_fn = frappe.get_attr(spec["provision"])
		result["steps"][f"{product}_provision"] = provision_fn(company, rates=rates)

		if spec["verify"]:
			verify_fn = frappe.get_attr(spec["verify"])
			result["steps"][f"{product}_verify"] = verify_fn(company)

	result["status"] = "live"
	return result


@frappe.whitelist(methods=["POST"])
def onboard_client_cli(company, abbr, apps, country="Kenya", currency="KES", rates=None):
	"""CLI-safe wrapper around onboard_client(), meant for `bench execute`.

	bench's own execute() has a real gap: when the target callable raises,
	it doesn't surface that exception — it falls through to a confusing
	fallback (compiling the method path itself as a bare expression) that
	fails with an unrelated NameError, hiding whatever actually went wrong.
	Confirmed against this bench version, not assumed.

	This wrapper never raises. It catches everything onboard_client() throws
	and returns a plain dict describing the failure instead, so bench execute
	always takes its normal success path — the real error message ends up in
	the printed result, not swallowed. Same role/method restriction as
	onboard_client() itself; kept separate rather than folded into it so
	onboard_client()'s "let it raise" behavior stays intact for direct,
	interactive use (e.g. bench console, where exceptions surface fine)."""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted — onboard_client_cli is restricted to System Managers."), frappe.PermissionError)

	try:
		return onboard_client(company, abbr, apps, country=country, currency=currency, rates=rates)
	except Exception as e:
		frappe.db.rollback()
		return {
			"status": "error",
			"error_type": type(e).__name__,
			"message": str(e),
		}


def reassert_setup_complete():
	"""after_migrate hook. Not a fix by itself — a defense against
	`bench migrate` silently undoing ensure_company()'s fix.

	`bench migrate` runs frappe's own Installed Applications.update_versions(),
	which unconditionally recomputes Installed Application.is_setup_complete
	for every app on every migrate — including resetting it back to 0 for
	every app except frappe/erpnext, with no awareness that onboard_client()
	already asserted otherwise. Found live: a site that had been fully
	onboarded and working started looping on
	/desk -> setup_wizard.load_languages -> /desk again, once a second,
	forever, after nothing but a routine `bench --site all migrate` — no code
	change, no new onboarding call, just the migrate itself.

	Re-asserts every currently installed app as setup-complete, every time
	this site migrates. Skips sites with no Company yet — one mid-onboarding,
	or one that's never been onboarded, should still see the real wizard.

	Uses frappe.db.count(), not frappe.db.exists("Company") with no second
	argument — caught live, the latter is falsy regardless of how many
	Company records actually exist (needs a name or filter dict to mean
	anything), which silently no-op'd this entire function on every call,
	including from the after_migrate hook itself, until this was found by
	testing the function directly rather than trusting it worked.

	Also re-runs disable_future_access() — the Installed Application flags
	turned out not to be the whole story either (see ensure_company()'s own
	docstring for the "desktop:home_page" / System Settings.setup_complete
	saga); re-asserting both here too, defensively, in case a future migrate
	ever resets either the way it already reset the per-app flags."""
	if not frappe.db.count("Company"):
		return

	from frappe.desk.page.setup_wizard.setup_wizard import disable_future_access, enable_setup_wizard_complete

	for app_name in frappe.get_installed_apps():
		enable_setup_wizard_complete(app_name)

	disable_future_access()
