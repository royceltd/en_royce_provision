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

    bench --site [client-site] execute royce_provision.royce_provision.onboarding.onboard_client \\
      --kwargs '{"company": "Acme Ltd", "abbr": "ACM", "apps": ["payroll"]}'

`apps` is a list drawn from PRODUCTS below. Right now that's just
`["payroll"]` — `royce_etims` has no provision()-style entry point yet, and
its own architecture doc is explicit that KRA registration needs a human
entering a real TIN and Apigee credentials, so it can never be a no-input
call the way payroll is. `PRODUCTS["etims"]` is filled in with what exists
today (just an install step) precisely so this stays the one place that
knows what "onboard a client" means — ready to wire in a real etims
provision() call the moment one exists, without restructuring anything here.
"""

import frappe
from frappe import _
from frappe.installer import install_app

PRODUCTS = {
	"payroll": {
		"install": ["royce_payroll_ke"],
		"provision": "royce_payroll_ke.royce_payroll_ke.setup.provision",
		"verify": "royce_payroll_ke.royce_payroll_ke.setup.verify",
	},
	"etims": {
		"install": ["royce_etims"],
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
	what makes that assumption actually hold for a brand new client."""
	if frappe.db.exists("Company", company):
		return company

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
	return doc.name


@frappe.whitelist()
def onboard_client(company, abbr, apps, country="Kenya", currency="KES", rates=None):
	"""The single place "onboard a client" happens from. Runs the company-level
	setup no individual compliance app owns, then calls each purchased
	product's own provisioning and verification — in that order, so a
	provisioning failure is caught before it's declared done, not after."""
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

		provision_fn = frappe.get_attr(spec["provision"])
		result["steps"][f"{product}_provision"] = provision_fn(company, rates=rates)

		if spec["verify"]:
			verify_fn = frappe.get_attr(spec["verify"])
			result["steps"][f"{product}_verify"] = verify_fn(company)

	result["status"] = "live"
	return result
