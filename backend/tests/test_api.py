"""End-to-end tests of every API endpoint: FastAPI + pipeline + the real Agents SDK runner with
scripted models + Postgres. Each test also checks what landed in the database."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from tests.conftest import (
    BASE_SETTINGS,
    BIZ,
    JPEG,
    OWNER_EMAIL,
    OWNER_PASSWORD,
    SUPPLIER,
    Harness,
    add_login,
    invoice_doc,
    invoice_line,
    receipt_doc,
)

pytestmark = pytest.mark.postgres
MISSING = "99999999-9999-9999-9999-999999999999"


@pytest.fixture
def h(make_harness) -> Harness:
    """A running API on a fresh database."""
    return make_harness()


def events(h: Harness, invoice_id: str) -> list[str]:
    """The audit-log event types of an invoice, oldest first."""
    return [event["event_type"] for event in h.client.get(f"/invoices/{invoice_id}/events").json()]


def typed_invoice(supplier_id: str, **overrides) -> dict:
    """A typed-in invoice body whose totals add up."""
    body = {
        "supplier_id": supplier_id, "invoice_number": "T-100", "invoice_date": "2026-10-21", "total": 6750.0,
        "lines": [{"item_name": "63mm uPVC pipe", "quantity": 10, "mro": 585.0, "line_total": 5850.0},
                  {"item_name": "PVC solvent cement 250ml", "quantity": 2, "mro": 450.0, "line_total": 900.0}],
    }
    return {**body, **overrides}


# ---- health and upload -------------------------------------------------------------------------
def test_health(h):
    """The API is up and reports what is configured."""
    assert h.client.get("/health", headers={"Authorization": ""}).json() == {
        "ok": True, "database": "postgres", "openai_configured": False, "image_storage": True, "login_required": True}


def test_upload_matches_saves_and_verifies(h):
    """Upload -> audited, matched draft with photo; verify sets the due date; all saved in Postgres."""
    supplier = h.supplier(credit_days=30)
    pipe, cement = h.product("63mm uPVC pipe"), h.product("PVC solvent cement 250ml")
    response = h.upload(invoice_doc())
    assert response.status_code == 201, response.text
    invoice = response.json()
    assert (invoice["status"], invoice["source"], invoice["math_ok"]) == ("draft", "agent", True)
    assert (invoice["supplier_id"], invoice["supplier_name"], invoice["supplier_name_raw"]) == (supplier["id"], SUPPLIER, SUPPLIER)
    assert invoice["missing_fields"] == [] and invoice["lines"][0]["mro"] == 585.0
    assert [(l["match_status"], l["product_id"], l["product_name"]) for l in invoice["lines"]] == [
        ("linked", pipe["id"], "63mm uPVC pipe"), ("linked", cement["id"], "PVC solvent cement 250ml")]
    assert invoice["image_url"] == f"memory://{invoice['image_path']}" and invoice["image_path"].endswith("/original.jpg")
    assert len(h.services.storage.objects) == 2

    verified = h.client.post(f"/invoices/{invoice['id']}/verify")
    assert verified.status_code == 200 and verified.json()["status"] == "verified"
    assert verified.json()["due_date"] == "2026-11-19"
    assert events(h, invoice["id"]) == ["invoice_extracted", "sku_matched", "invoice_verified"]
    assert h.sql("select status, due_date::text, total from invoices") == [("verified", "2026-11-19", 6750)]
    assert h.sql("select count(*) from invoice_items where product_id is not null") == [(2,)]
    assert h.sql("select agent, guardrail_passed, invoice_id::text from extraction_runs") == [("InvoiceVisionAgent", True, invoice["id"])]


def test_upload_refusals(h):
    """Unreadable photo, empty receipt, model outage and broken files: refused, nothing saved."""
    guardrail = h.upload(invoice_doc(date=None))
    assert guardrail.status_code == 422 and guardrail.json()["error_type"] == "reading_unsuccessful"
    assert guardrail.json()["missing_fields"][0] == "invoice.invoice_date"
    blank = h.upload(receipt_doc(number=None, payments=[(None, None)]))
    assert blank.status_code == 422 and blank.json()["missing_fields"] == ["receipt.payments", "receipt.receipt_number"]
    outage = h.upload(RuntimeError("openai down"))
    assert outage.status_code == 502 and outage.json()["error_type"] == "api_error"
    broken = h.client.post("/invoices/upload", files={"file": ("x.jpg", b"garbage", "image/jpeg")})
    assert broken.status_code == 422 and broken.json()["missing_fields"] == ["image"]
    assert h.client.post("/invoices/upload", files={"file": ("a.txt", b"hello", "text/plain")}).status_code == 415
    assert h.sql("select count(*) from invoices") == [(0,)] and h.sql("select count(*) from supplier_payments") == [(0,)]
    assert h.sql("select guardrail_passed from extraction_runs order by created_at") == [(False,), (False,), (None,)]


def test_upload_size_limit_and_no_storage(make_harness):
    """Photos over the limit are refused (413); without Storage the invoice is saved without a photo."""
    small = make_harness(settings=replace(BASE_SETTINGS, max_upload_mb=0.01))
    assert small.client.post("/invoices/upload", files={"file": JPEG}).status_code == 413
    bare = make_harness(with_storage=False)
    invoice = bare.upload(invoice_doc()).json()
    assert invoice["image_path"] is None and invoice["image_url"] is None
    assert bare.client.get("/health").json()["image_storage"] is False


def test_upload_duplicate_unknown_and_chosen_supplier(h):
    """Duplicates are refused; unknown suppliers wait for review; a chosen supplier wins."""
    h.supplier()
    first = h.upload(invoice_doc()).json()
    duplicate = h.upload(invoice_doc())
    assert duplicate.status_code == 409 and duplicate.json()["invoice_id"] == first["id"]
    unknown = h.upload(invoice_doc(supplier="Brand New Hardware Co", number="N-1")).json()
    assert unknown["supplier_id"] is None and unknown["supplier_name"] is None and "supplier_id" in unknown["missing_fields"]
    chosen = h.supplier("Chosen Supplier")
    picked = h.upload(invoice_doc(supplier="Something Else", number="N-2"), supplier_id=chosen["id"]).json()
    assert picked["supplier_id"] == chosen["id"]
    assert h.client.post("/invoices/upload", files={"file": JPEG}, data={"supplier_id": MISSING}).status_code == 404
    assert h.client.post("/invoices/upload", files={"file": JPEG}, data={"supplier_id": "nope"}).status_code == 422


# ---- typed-in invoices -------------------------------------------------------------------------
def test_typed_in_invoice(h):
    """POST /invoices audits and matches like an upload; mistakes are flagged, not fixed."""
    supplier = h.supplier()
    h.product("63mm uPVC pipe")
    h.product("PVC solvent cement 250ml")
    created = h.client.post("/invoices", json=typed_invoice(supplier["id"]))
    assert created.status_code == 201, created.text
    invoice = created.json()
    assert (invoice["source"], invoice["status"], invoice["math_ok"]) == ("manual", "draft", True)
    assert all(line["match_status"] == "linked" for line in invoice["lines"])
    assert events(h, invoice["id"]) == ["invoice_created", "sku_matched"]

    wrong = h.client.post("/invoices", json=typed_invoice(supplier["id"], invoice_number="T-101", total=7000.0)).json()
    assert wrong["math_ok"] is False and wrong["math_issues"][0]["code"] == "invoice_total_mismatch"
    assert all(line["match_status"] == "unmatched" for line in wrong["lines"])
    assert h.client.post("/invoices", json=typed_invoice(supplier["id"])).status_code == 409
    assert h.client.post("/invoices", json=typed_invoice(MISSING)).status_code == 404
    assert h.client.post("/invoices", json=typed_invoice(supplier["id"], lines=[])).status_code == 422
    assert h.sql("select source, count(*) from invoices group by source") == [("manual", 2)]


# ---- review: fix, confirm, verify, reject, payment ---------------------------------------------
def test_fix_confirm_verify_and_learning(h):
    """PendingFix -> HumanConfirm -> verify; the confirmed wording links the next invoice by itself."""
    h.supplier()
    bad = invoice_doc(lines=[invoice_line("63mm uPVC pipe", 10, 585.0, line_total=5580.0)], total=5580.0)
    invoice = h.upload(bad).json()
    assert invoice["math_ok"] is False and invoice["lines"][0]["match_status"] == "unmatched"
    refused = h.client.post(f"/invoices/{invoice['id']}/verify")
    assert refused.status_code == 409 and any("math audit" in r for r in refused.json()["reasons"])

    line = invoice["lines"][0]
    fixed = h.client.post(f"/invoices/{invoice['id']}/fix", json={"total": 5850.0, "lines": [
        {"id": line["id"], "item_name": line["item_name"], "quantity": 10, "mro": 585.0, "line_total": 5850.0}]}).json()
    assert fixed["math_ok"] is True and fixed["lines"][0]["match_status"] == "new_product"  # empty catalogue

    confirmed = h.client.post(f"/invoices/{invoice['id']}/confirm-sku",
                              json={"line_id": fixed["lines"][0]["id"], "new_product": {"name": "uPVC Pipe 63mm"}}).json()
    linked = confirmed["lines"][0]
    assert (linked["match_status"], linked["product_name"]) == ("linked", "uPVC Pipe 63mm")
    assert h.client.post(f"/invoices/{invoice['id']}/verify").status_code == 200
    assert events(h, invoice["id"]) == ["invoice_extracted", "invoice_corrected", "sku_matched", "sku_confirmed", "invoice_verified"]
    assert ("63mm uPVC pipe", "owner_confirmed") in h.sql("select alias_text, source from product_aliases")

    again = h.upload(invoice_doc(number="INV-1002", lines=[invoice_line("63MM  uPVC-pipe", 4, 585.0)])).json()
    assert (again["lines"][0]["product_id"], again["lines"][0]["match_similarity"]) == (linked["product_id"], 1.0)


def test_confirm_existing_product_below_threshold(make_harness):
    """Below the threshold a line suggests a product; the owner confirms it by id."""
    h = make_harness(settings=replace(BASE_SETTINGS, sku_match_threshold=0.99))
    h.supplier()
    pipe = h.product("uPVC Pipe 63mm")
    line = h.upload(invoice_doc(lines=[invoice_line("upvc pipe 63 mm", 1, 585.0)])).json()["lines"][0]
    assert (line["match_status"], line["suggested_product_id"]) == ("pending", pipe["id"])
    assert "below the 0.99 threshold" in line["match_reason"] and line["match_candidates"][0]["product_id"] == pipe["id"]
    done = h.client.post(f"/invoices/{line['invoice_id']}/confirm-sku", json={"line_id": line["id"], "product_id": pipe["id"]}).json()
    assert done["lines"][0]["product_id"] == pipe["id"] and done["lines"][0]["match_reason"] == "Confirmed by the owner."


def test_confirm_sku_validation(h):
    """Exactly one target; unknown lines/products are 404; duplicate new product names are 409."""
    h.supplier()
    invoice = h.upload(invoice_doc()).json()
    url, line_id = f"/invoices/{invoice['id']}/confirm-sku", invoice["lines"][0]["id"]
    assert h.client.post(url, json={"line_id": line_id}).status_code == 422
    assert h.client.post(url, json={"line_id": line_id, "product_id": line_id, "new_product": {"name": "x"}}).status_code == 422
    assert h.client.post(url, json={"line_id": MISSING, "new_product": {"name": "x"}}).status_code == 404
    assert h.client.post(url, json={"line_id": line_id, "product_id": MISSING}).status_code == 404
    h.product("Taken Name")
    duplicate = h.client.post(url, json={"line_id": line_id, "new_product": {"name": "taken name"}})
    assert duplicate.status_code == 409 and duplicate.json()["error_type"] == "duplicate_product"


def test_fix_rules(h):
    """Fixing keeps unchanged links, reverts verified to draft, refuses duplicates, checks dates, sets a supplier."""
    supplier = h.supplier()
    h.product("63mm uPVC pipe")
    h.product("PVC solvent cement 250ml")
    invoice = h.upload(invoice_doc()).json()
    assert h.client.post(f"/invoices/{invoice['id']}/verify").json()["status"] == "verified"
    first, second = invoice["lines"]
    fixed = h.client.post(f"/invoices/{invoice['id']}/fix", json={"total": 6450.0, "lines": [
        {"id": first["id"], "item_name": first["item_name"], "quantity": 10, "mro": 585.0, "line_total": 5850.0},
        {"id": second["id"], "item_name": "Gate valve 1in", "quantity": 1, "mro": 600.0, "line_total": 600.0}]}).json()
    assert (fixed["status"], fixed["verified_at"]) == ("draft", None)
    assert fixed["lines"][0]["product_id"] == first["product_id"] and fixed["lines"][1]["product_id"] != second["product_id"]

    other = h.upload(invoice_doc(number="INV-2")).json()
    assert h.client.post(f"/invoices/{other['id']}/fix", json={"invoice_number": "INV-1001"}).status_code == 409
    future = h.client.post(f"/invoices/{other['id']}/fix", json={"invoice_date": "2099-01-01"}).json()
    assert future["field_issues"][0]["code"] == "future_date"
    assert "Check the invoice dates." in h.client.post(f"/invoices/{other['id']}/verify").json()["reasons"]
    unknown = h.upload(invoice_doc(supplier="Nobody Ltd", number="INV-3")).json()
    assert h.client.post(f"/invoices/{unknown['id']}/fix", json={"supplier_id": supplier["id"]}).json()["supplier_id"] == supplier["id"]
    assert h.client.post(f"/invoices/{unknown['id']}/fix", json={"supplier_id": MISSING}).status_code == 404


def test_reject_and_payment(h):
    """Drafts can be rejected (logged, kept); only verified invoices can be marked paid."""
    h.supplier()
    draft = h.upload(invoice_doc(number="R-1")).json()
    assert h.client.post(f"/invoices/{draft['id']}/payment", json={"payment_status": "paid"}).status_code == 409
    rejected = h.client.post(f"/invoices/{draft['id']}/reject", json={"reason": "Duplicate photo"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert h.client.post(f"/invoices/{draft['id']}/verify").json()["reasons"][0].startswith("This invoice was rejected.")
    assert h.client.post(f"/invoices/{draft['id']}/reject", json={"reason": ""}).status_code == 422
    assert events(h, draft["id"])[-1] == "invoice_rejected"

    h.product("63mm uPVC pipe")
    h.product("PVC solvent cement 250ml")
    good = h.upload(invoice_doc(number="R-2")).json()
    h.client.post(f"/invoices/{good['id']}/verify")
    assert h.client.post(f"/invoices/{good['id']}/reject", json={"reason": "x"}).status_code == 409
    paid = h.client.post(f"/invoices/{good['id']}/payment", json={"payment_status": "paid"})
    assert paid.status_code == 200 and paid.json()["payment_status"] == "paid"
    assert h.client.post(f"/invoices/{good['id']}/payment", json={"payment_status": "maybe"}).status_code == 422
    assert h.client.post(f"/invoices/{good['id']}/payment", json={"payment_status": "unpaid"}).json()["payment_status"] == "unpaid"
    assert h.sql("select status, payment_status from invoices order by invoice_number") == [("rejected", "unpaid"), ("verified", "unpaid")]


def test_search_get_and_not_found(h):
    """Search filters, one invoice, and 404 / 422 for unknown or malformed ids."""
    supplier = h.supplier()
    h.product("63mm uPVC pipe")
    h.product("PVC solvent cement 250ml")
    a = h.upload(invoice_doc(number="S-1", date="2026-09-01")).json()
    b = h.upload(invoice_doc(number="S-2", date="2026-10-01")).json()
    h.client.post(f"/invoices/{b['id']}/verify")

    def numbers(**params):
        """Invoice numbers returned for these query parameters."""
        return [row["invoice_number"] for row in h.client.get("/invoices", params=params).json()]

    assert numbers() == ["S-2", "S-1"]
    assert numbers(status="verified") == ["S-2"] and numbers(payment_status="unpaid") == ["S-2", "S-1"]
    assert numbers(supplier_id=supplier["id"], date_from="2026-09-15") == ["S-2"]
    assert numbers(date_to="2026-09-15", invoice_number="s-") == ["S-1"] and numbers(limit=1) == ["S-2"]
    row = h.client.get("/invoices").json()[0]
    assert row["supplier_name"] == SUPPLIER and "lines" not in row
    assert h.client.get(f"/invoices/{a['id']}").json()["lines"][0]["item_name"] == "63mm uPVC pipe"
    for path in (f"/invoices/{MISSING}", f"/invoices/{MISSING}/events", f"/suppliers/{MISSING}", f"/suppliers/{MISSING}/profile"):
        assert h.client.get(path).status_code == 404
    for action in ("verify", "fix"):
        assert h.client.post(f"/invoices/{MISSING}/{action}", json={}).status_code == 404
    assert h.client.post(f"/invoices/{MISSING}/reject", json={"reason": "x"}).status_code == 404
    assert h.client.get("/invoices/not-a-uuid").status_code == 422
    assert h.client.get("/invoices", params={"limit": 0}).status_code == 422


# ---- suppliers, products, dashboard ------------------------------------------------------------
def test_suppliers_products_and_profile(h):
    """Supplier and product endpoints, credit-day updates, and the Supplier Profile totals."""
    supplier = h.supplier("Ceylon Paints", credit_days=None)
    updated = h.client.post(f"/suppliers/{supplier['id']}", json={"credit_days": 15, "phone1": "011 000 0000"}).json()
    assert (updated["credit_days"], updated["phone1"]) == (15, "011 000 0000")
    assert h.client.post(f"/suppliers/{supplier['id']}", json={"credit_days": -3}).status_code == 422
    assert h.client.post(f"/suppliers/{supplier['id']}", json={"name": None}).status_code == 422
    assert h.client.post(f"/suppliers/{supplier['id']}", json={}).json()["credit_days"] == 15
    assert h.client.post(f"/suppliers/{MISSING}", json={"credit_days": 1}).status_code == 404
    assert [s["name"] for s in h.client.get("/suppliers").json()] == ["Ceylon Paints"]
    assert h.client.get(f"/suppliers/{supplier['id']}").json()["name"] == "Ceylon Paints"
    assert h.client.post("/suppliers", json={"name": ""}).status_code == 422

    h.product("Wall Paint 4L", sku="WP-4")
    assert h.client.post("/products", json={"name": "wall paint 4l"}).status_code == 409
    assert [p["sku"] for p in h.client.get("/products").json()] == ["WP-4"]

    paint = {"item_name": "Wall Paint 4L", "quantity": 2, "mro": 450.0, "line_total": 900.0}
    verified = h.client.post("/invoices", json={"supplier_id": supplier["id"], "invoice_number": "P-1",
                                                "invoice_date": "2026-10-01", "total": 900.0, "lines": [paint]}).json()
    assert h.client.post(f"/invoices/{verified['id']}/verify").status_code == 200
    h.client.post("/invoices", json={"supplier_id": supplier["id"], "invoice_number": "P-2", "invoice_date": "2026-10-02",
                                     "total": 100.0, "lines": [{"item_name": "Brush", "quantity": 1, "mro": 100.0, "line_total": 100.0}]})
    profile = h.client.get(f"/suppliers/{supplier['id']}/profile").json()
    assert profile["supplier"]["name"] == "Ceylon Paints"
    assert profile["totals"] == {"invoice_count": 2, "draft_count": 1, "verified_total": 900.0, "outstanding": 900.0,
                                 "next_due_date": "2026-10-16", "paid_total": 0.0, "unconfirmed_total": 0.0,
                                 "unconfirmed_count": 0}
    assert profile["payments"] == []
    assert [i["invoice_number"] for i in profile["invoices"]] == ["P-2", "P-1"]
    assert [(l["invoice_number"], l["item_name"], l["mro"], l["product_name"]) for l in profile["lines"]] == [
        ("P-1", "Wall Paint 4L", 450.0, "Wall Paint 4L")]
    h.client.post(f"/invoices/{verified['id']}/payment", json={"payment_status": "paid"})
    assert h.client.get(f"/suppliers/{supplier['id']}/profile").json()["totals"]["outstanding"] == 0.0


def test_dashboard(h):
    """Review queue, monthly purchases, supplier spend and payments due, from verified invoices."""
    supplier = h.supplier("Ceylon Paints", credit_days=10)
    h.product("Wall Paint 4L")
    body = {"supplier_id": supplier["id"], "invoice_number": "D-1", "invoice_date": (date.today() - timedelta(days=5)).isoformat(),
            "total": 900.0, "lines": [{"item_name": "Wall Paint 4L", "quantity": 2, "mro": 450.0, "line_total": 900.0}]}
    verified = h.client.post("/invoices", json=body).json()
    h.client.post(f"/invoices/{verified['id']}/verify")
    h.client.post("/invoices", json={**body, "invoice_number": "D-2"})
    dash = h.client.get("/dashboard").json()
    assert (dash["review_queue"], dash["verified_invoices"]) == (1, 1)
    assert len(dash["monthly_purchases"]) == 12 and sum(m["total"] for m in dash["monthly_purchases"]) == 900.0
    assert dash["supplier_spend"] == [{"supplier_id": supplier["id"], "supplier_name": "Ceylon Paints", "total": 900.0}]
    [due] = dash["payments_due"]
    assert (due["invoice_number"], due["days_left"], due["total"]) == ("D-1", 5, 900.0)
    forecast = dash["forecast"]
    assert (forecast["days"], forecast["is_baseline"], forecast["method"], forecast["low"]) == (30, True, "rolling mean", None)
    assert "not been trained" in forecast["model_note"] and "30 days" in dash["forecast_note"]


def test_cors_and_methods(make_harness):
    """Only the configured websites may call the API, and only with GET and POST."""
    h = make_harness(settings=replace(BASE_SETTINGS, allowed_origins=("https://businesssense.vercel.app",),
                                      allowed_origin_regex=r"https://businesssense-[a-z0-9-]+\.vercel\.app"))

    def preflight(origin: str, method: str = "POST"):
        """Send a browser CORS preflight."""
        return h.client.options("/invoices", headers={"Origin": origin, "Access-Control-Request-Method": method})

    assert preflight("https://businesssense.vercel.app").headers["access-control-allow-origin"] == "https://businesssense.vercel.app"
    assert preflight("https://businesssense-git-feature-x.vercel.app").status_code == 200
    assert "access-control-allow-origin" not in preflight("https://evil.example").headers
    assert preflight("https://businesssense.vercel.app", "DELETE").status_code == 400


# ---- fixes found by the end-to-end test run -------------------------------------------------------
def test_confirm_updates_timestamp_and_only_drafts_change_links(h):
    """confirm-sku changes updated_at; verified invoices cannot change links or be verified twice."""
    h.supplier()
    invoice = h.upload(invoice_doc(lines=[invoice_line("63mm uPVC pipe", 10, 585.0)], total=5850.0)).json()
    line = invoice["lines"][0]
    confirmed = h.client.post(f"/invoices/{invoice['id']}/confirm-sku",
                              json={"line_id": line["id"], "new_product": {"name": "uPVC Pipe 63mm"}}).json()
    assert confirmed["updated_at"] != invoice["updated_at"]
    assert h.client.post(f"/invoices/{invoice['id']}/verify").status_code == 200
    again = h.client.post(f"/invoices/{invoice['id']}/verify")
    assert again.status_code == 409 and again.json()["error_type"] == "already_verified"
    other = h.product("GI Pipe 1 inch")
    locked = h.client.post(f"/invoices/{invoice['id']}/confirm-sku", json={"line_id": line["id"], "product_id": other["id"]})
    assert locked.status_code == 409 and locked.json()["error_type"] == "not_draft"


def test_reconfirming_text_replaces_the_old_link(h):
    """The owner's latest confirmation wins: the next invoice links to the new product, not the old one."""
    h.supplier()
    wrong, right = h.product("GI Pipe 1 inch"), h.product("uPVC Pipe 63mm")
    first = h.upload(invoice_doc(number="A-1", lines=[invoice_line("63mm uPVC pipe", 1, 585.0)], total=585.0)).json()
    h.client.post(f"/invoices/{first['id']}/confirm-sku", json={"line_id": first["lines"][0]["id"], "product_id": wrong["id"]})
    second = h.upload(invoice_doc(number="A-2", lines=[invoice_line("63mm uPVC pipe", 1, 585.0)], total=585.0)).json()
    assert second["lines"][0]["product_id"] == wrong["id"]  # learned from the first confirmation
    h.client.post(f"/invoices/{second['id']}/fix", json={})  # still a draft
    h.client.post(f"/invoices/{second['id']}/confirm-sku", json={"line_id": second["lines"][0]["id"], "product_id": right["id"]})
    third = h.upload(invoice_doc(number="A-3", lines=[invoice_line("63mm uPVC pipe", 1, 585.0)], total=585.0)).json()
    assert third["lines"][0]["product_id"] == right["id"]


def test_exact_names_link_even_when_embeddings_are_blocked(make_harness):
    """A blocked embedding model must not stop exact catalogue names from linking; others wait politely."""
    from tests.conftest import BrokenEmbedder

    h = make_harness(embedder=BrokenEmbedder())
    h.supplier()
    h.client.post("/products", json={"name": "uPVC Pipe 63mm"})  # saved without an embedding
    invoice = h.client.post("/invoices", json=typed_invoice(h.supplier("Second Supplier")["id"], lines=[
        {"item_name": "uPVC Pipe 63mm", "quantity": 1, "mro": 585.0, "line_total": 585.0},
        {"item_name": "Wall paint 4L", "quantity": 1, "mro": 900.0, "line_total": 900.0}], total=1485.0)).json()
    assert invoice["lines"][0]["match_status"] == "linked"
    assert invoice["lines"][1]["match_status"] == "pending"
    assert "unavailable" in invoice["lines"][1]["match_reason"] and "403" not in invoice["lines"][1]["match_reason"]


def test_out_of_range_values_and_names_are_refused_cleanly(h):
    """Huge numbers, NaN-like values and blank or duplicate names give 4xx JSON, never a 500."""
    supplier = h.supplier()
    assert h.client.post("/suppliers", json={"name": "X", "credit_days": 99999999999}).status_code == 422
    assert h.client.post("/suppliers", json={"name": "   "}).status_code == 422
    duplicate = h.client.post("/suppliers", json={"name": "  lanka pipe traders "})
    assert duplicate.status_code == 409 and duplicate.json()["error_type"] == "duplicate_supplier"
    assert h.client.post("/invoices", json=typed_invoice(supplier["id"], total=1e13)).status_code == 422
    big_line = typed_invoice(supplier["id"], invoice_number="T-2",
                             lines=[{"item_name": "x", "quantity": 1e12, "mro": 1, "line_total": 1}])
    assert h.client.post("/invoices", json=big_line).status_code == 422
    product = h.client.post("/products", json={"name": "  Wall Paint 4L  "}).json()
    assert product["name"] == "Wall Paint 4L"
    assert h.client.post("/products", json={"name": "wall paint 4l"}).status_code == 409


def test_invoice_number_search_is_literal(h):
    """% and _ in the search box are matched as text, not as wildcards."""
    supplier = h.supplier()
    h.client.post("/invoices", json=typed_invoice(supplier["id"], invoice_number="T_100"))
    h.client.post("/invoices", json=typed_invoice(supplier["id"], invoice_number="TX100"))
    assert h.client.get("/invoices", params={"invoice_number": "%"}).json() == []
    assert [i["invoice_number"] for i in h.client.get("/invoices", params={"invoice_number": "T_"}).json()] == ["T_100"]


# ---- supplier payments and receipts ---------------------------------------------------------
def verified_invoice(h: Harness, supplier_id: str, number: str, total: float = 6750.0, day: str = "2026-10-21") -> dict:
    """A typed-in invoice that is verified (its two products exist, so both lines link)."""
    for name in ("63mm uPVC pipe", "PVC solvent cement 250ml"):
        if not any(p["name"] == name for p in h.client.get("/products").json()):
            h.product(name)
    lines = [{"item_name": "63mm uPVC pipe", "quantity": 1, "mro": total - 100, "line_total": total - 100},
             {"item_name": "PVC solvent cement 250ml", "quantity": 1, "mro": 100.0, "line_total": 100.0}]
    invoice = h.client.post("/invoices", json={"supplier_id": supplier_id, "invoice_number": number,
                                               "invoice_date": day, "total": total, "lines": lines}).json()
    verified = h.client.post(f"/invoices/{invoice['id']}/verify")
    assert verified.status_code == 200, verified.text
    return verified.json()


def invoice_state(h: Harness, invoice_id: str) -> tuple[str, float]:
    """An invoice's payment status and the confirmed amount paid against it."""
    invoice = h.client.get(f"/invoices/{invoice_id}").json()
    return invoice["payment_status"], invoice["amount_paid"]


def test_receipt_upload_is_saved_and_confirming_marks_the_invoice_paid(h):
    """A receipt photo becomes an unconfirmed payment linked to its invoice; confirming pays it."""
    supplier = h.supplier()
    invoice = verified_invoice(h, supplier["id"], "INV-1001")
    response = h.upload(receipt_doc(low_confidence=["receipt.payments[0].amount"]))
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert (receipt["document_type"], receipt["receipt_number"], receipt["supplier_id"]) == ("receipt", "R-77", supplier["id"])
    [payment] = receipt["payments"]
    assert (payment["status"], payment["source"], payment["invoice_id"], payment["invoice_number"]) == (
        "unconfirmed", "agent", invoice["id"], "INV-1001")
    assert (payment["amount"], payment["paid_on"], payment["method"]) == (6750.0, "2026-10-22", "cash")
    assert payment["low_confidence_fields"] == ["receipt.payments[0].amount"]
    assert payment["image_url"] == receipt["image_url"] == f"memory://{payment['image_path']}"
    assert payment["image_path"].endswith(f"receipts/{payment['id']}/original.jpg")
    assert invoice_state(h, invoice["id"]) == ("unconfirmed", 0.0)
    assert h.sql("select guardrail_passed, invoice_id from extraction_runs") == [(True, None)]

    again = h.upload(receipt_doc())
    assert again.status_code == 409 and again.json()["payment_id"] == payment["id"]

    confirmed = h.client.post(f"/payments/{payment['id']}/confirm")
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed" and confirmed.json()["confirmed_at"]
    assert invoice_state(h, invoice["id"]) == ("paid", 6750.0)
    assert h.client.post(f"/payments/{payment['id']}/confirm").json()["error_type"] == "already_confirmed"
    assert events(h, invoice["id"])[-4:] == ["receipt_extracted", "payment_status_updated", "payment_confirmed",
                                             "payment_status_updated"]
    profile = h.client.get(f"/suppliers/{supplier['id']}/profile").json()
    assert (profile["totals"]["outstanding"], profile["totals"]["paid_total"], profile["totals"]["unconfirmed_count"]) == (0.0, 6750.0, 0)
    assert [p["id"] for p in profile["payments"]] == [payment["id"]] and profile["payments"][0]["image_url"]
    assert h.client.get(f"/payments/{payment['id']}").json()["image_url"] == payment["image_url"]
    assert h.client.get("/invoices", params={"payment_status": "paid"}).json()[0]["amount_paid"] == 6750.0


def test_receipt_settling_several_invoices_part_payment_and_unknown_number(h):
    """One row per invoice; a part payment leaves the rest owed; an unsaved invoice number blocks confirming."""
    supplier = h.supplier()
    first = verified_invoice(h, supplier["id"], "T-1")
    second = verified_invoice(h, supplier["id"], "T-2")
    cheque = {"cheque_number": "004512", "bank_name": "Example Bank", "cheque_date": "2026-10-23"}
    receipt = h.upload(receipt_doc(method=None, payments=[("T-1", 6750.0), ("T-2", 3000.0), ("T-9", 500.0)],
                                   cheques=[cheque])).json()
    rows = {p["invoice_number"] or p["invoice_number_raw"]: p for p in receipt["payments"]}
    assert set(rows) == {"T-1", "T-2", "T-9"} and rows["T-9"]["invoice_id"] is None
    assert {(p["method"], p["cheque_number"], p["bank_name"], p["cheque_date"]) for p in rows.values()} == {
        ("cheque", "004512", "Example Bank", "2026-10-23")}
    assert len({p["image_path"] for p in rows.values()}) == 1

    assert h.client.post(f"/payments/{rows['T-2']['id']}/confirm").status_code == 200
    assert invoice_state(h, second["id"]) == ("unpaid", 3000.0)
    blocked = h.client.post(f"/payments/{rows['T-9']['id']}/confirm")
    assert blocked.status_code == 409 and "T-9" in blocked.json()["reasons"][0]
    assert h.client.post(f"/payments/{rows['T-9']['id']}/reject", json={"reason": ""}).status_code == 422
    rejected = h.client.post(f"/payments/{rows['T-9']['id']}/reject", json={"reason": "No such invoice"})
    assert rejected.json()["status"] == "rejected"
    assert h.client.post(f"/payments/{rows['T-9']['id']}/confirm").status_code == 409

    totals = h.client.get(f"/suppliers/{supplier['id']}/profile").json()["totals"]
    assert (totals["outstanding"], totals["paid_total"], totals["unconfirmed_total"], totals["unconfirmed_count"]) == (
        10500.0, 3000.0, 6750.0, 1)
    assert h.client.post(f"/payments/{rows['T-1']['id']}/confirm").status_code == 200
    assert invoice_state(h, first["id"]) == ("paid", 6750.0)
    assert h.client.get(f"/suppliers/{supplier['id']}/profile").json()["totals"]["outstanding"] == 3750.0
    assert [p["status"] for p in h.client.get("/payments", params={"supplier_id": supplier["id"]}).json()].count("confirmed") == 2


def test_receipt_supplier_found_from_invoice_number_chosen_or_picked_later(h):
    """No supplier name: the invoice number finds it. Unknown: it waits; the owner picks it and confirms."""
    supplier = h.supplier()
    other = h.supplier("Ceylon Paints")
    invoice = verified_invoice(h, supplier["id"], "INV-5")
    found = h.upload(receipt_doc(supplier=None, number="R-1", payments=[("INV-5", 1000.0)])).json()
    assert found["supplier_id"] == supplier["id"] and found["payments"][0]["invoice_id"] == invoice["id"]

    chosen = h.upload(receipt_doc(supplier="Lanka Pipe Traders", number="R-2", payments=[("P-1", 50.0)]),
                      supplier_id=other["id"]).json()
    assert chosen["supplier_id"] == other["id"] and chosen["payments"][0]["invoice_number_raw"] == "P-1"

    unknown = h.upload(receipt_doc(supplier="Brand New Hardware Co", number="R-3", payments=[("Z-1", 250.0)])).json()
    [waiting] = unknown["payments"]
    assert unknown["supplier_id"] is None and waiting["supplier_name_raw"] == "Brand New Hardware Co"
    reasons = h.client.post(f"/payments/{waiting['id']}/confirm").json()["reasons"]
    assert reasons[0] == "Pick the supplier."
    mismatch = h.client.post(f"/payments/{waiting['id']}/fix", json={"supplier_id": other["id"], "invoice_id": invoice["id"]})
    assert mismatch.status_code == 409 and mismatch.json()["error_type"] == "supplier_mismatch"
    fixed = h.client.post(f"/payments/{waiting['id']}/fix", json={"supplier_id": None, "invoice_id": invoice["id"]}).json()
    assert (fixed["supplier_id"], fixed["invoice_id"], fixed["invoice_number_raw"]) == (supplier["id"], invoice["id"], None)
    assert h.client.post(f"/payments/{waiting['id']}/confirm").status_code == 200
    assert invoice_state(h, invoice["id"]) == ("unconfirmed", 250.0)  # the R-1 payment still waits

    nameless = h.upload(receipt_doc(supplier=None, number="R-4", payments=[("NOPE", 10.0)])).json()
    assert nameless["supplier_id"] is None and nameless["payments"][0]["invoice_number_raw"] == "NOPE"
    assert h.sql("select count(*) from supplier_payments") == [(4,)]


def test_typed_in_payment_record_fix_confirm_and_reject(h):
    """A payment typed in by hand follows the same rules as one read from a receipt."""
    supplier = h.supplier()
    other = h.supplier("Ceylon Paints")
    invoice = verified_invoice(h, supplier["id"], "M-1", total=1000.0)
    draft = h.client.post("/invoices", json=typed_invoice(supplier["id"], invoice_number="M-2")).json()
    base = {"supplier_id": supplier["id"], "invoice_id": invoice["id"], "amount": 400.0, "paid_on": "2026-10-25",
            "method": "cash", "receipt_number": " R-10 "}

    for bad, status in (({"amount": 0}, 422), ({"amount": -5}, 422), ({"paid_on": None}, 422),
                        ({"method": "barter"}, 422), ({"supplier_id": MISSING, "invoice_id": None}, 404), ({"invoice_id": MISSING}, 404),
                        ({"supplier_id": other["id"]}, 409)):
        assert h.client.post("/payments", json={**base, **bad}).status_code == status, bad
    rejected_invoice = h.client.post("/invoices", json=typed_invoice(supplier["id"], invoice_number="M-3")).json()
    h.client.post(f"/invoices/{rejected_invoice['id']}/reject", json={"reason": "duplicate"})
    assert h.client.post("/payments", json={**base, "invoice_id": rejected_invoice["id"]}).json()["error_type"] == "invoice_rejected"

    saved = h.client.post("/payments", json=base)
    assert saved.status_code == 201, saved.text
    payment = saved.json()
    assert (payment["status"], payment["source"], payment["receipt_number"]) == ("unconfirmed", "manual", "R-10")
    assert invoice_state(h, invoice["id"]) == ("unconfirmed", 0.0)
    assert h.client.post("/payments", json=base).json()["error_type"] == "duplicate_payment"

    on_draft = h.client.post("/payments", json={**base, "invoice_id": draft["id"], "receipt_number": None, "confirm": True})
    assert on_draft.status_code == 409 and on_draft.json()["reasons"] == ["Verify invoice M-2 first."]
    too_much = h.client.post("/payments", json={**base, "receipt_number": "R-11", "amount": 5000.0, "confirm": True})
    assert "more than" in too_much.json()["reasons"][0]
    future = h.client.post("/payments", json={**base, "receipt_number": "R-12", "paid_on": "2030-01-01", "confirm": True})
    assert "future" in future.json()["reasons"][0]
    assert h.sql("select count(*) from supplier_payments") == [(1,)]  # refused ones saved nothing

    now = h.client.post("/payments", json={**base, "receipt_number": "R-13", "amount": 600.0, "confirm": True})
    assert now.status_code == 201 and now.json()["status"] == "confirmed"
    assert invoice_state(h, invoice["id"]) == ("unconfirmed", 600.0)
    assert h.client.post(f"/payments/{payment['id']}/confirm").status_code == 200
    assert invoice_state(h, invoice["id"]) == ("paid", 1000.0)

    assert h.client.post(f"/payments/{payment['id']}/reject", json={"reason": "x"}).status_code == 409
    fixed = h.client.post(f"/payments/{payment['id']}/fix", json={"amount": 300.0, "bank_name": "  "}).json()
    assert (fixed["status"], fixed["amount"], fixed["confirmed_at"], fixed["bank_name"]) == ("unconfirmed", 300.0, None, None)
    assert invoice_state(h, invoice["id"]) == ("unconfirmed", 600.0)
    assert h.client.post(f"/payments/{payment['id']}/fix", json={"amount": 0}).status_code == 422
    assert h.client.post(f"/payments/{payment['id']}/reject", json={"reason": "Typed twice"}).json()["status"] == "rejected"
    assert invoice_state(h, invoice["id"]) == ("unpaid", 600.0)
    back = h.client.post(f"/payments/{payment['id']}/fix", json={"amount": 400.0}).json()
    assert back["status"] == "unconfirmed"
    assert h.client.post(f"/payments/{MISSING}/confirm").status_code == 404
    assert h.client.post(f"/payments/{MISSING}/fix", json={}).status_code == 404
    assert h.client.get("/payments/not-a-uuid").status_code == 422
    assert [e for e in events(h, invoice["id"]) if e.startswith("payment_")][:3] == [
        "payment_recorded", "payment_status_updated", "payment_recorded"]


def test_payment_reminder_after_two_days_and_dashboard(h):
    """A payment waiting more than two days is a reminder on the dashboard; it stays unconfirmed."""
    supplier = h.supplier(credit_days=10)
    invoice = verified_invoice(h, supplier["id"], "D-1", total=1000.0, day=(date.today() - timedelta(days=5)).isoformat())
    payment = h.client.post("/payments", json={"supplier_id": supplier["id"], "invoice_id": invoice["id"], "amount": 400.0,
                                               "paid_on": date.today().isoformat(), "confirm": True}).json()
    waiting = h.client.post("/payments", json={"supplier_id": supplier["id"], "amount": 50.0,
                                               "paid_on": date.today().isoformat()}).json()
    dash = h.client.get("/dashboard").json()
    assert (dash["unconfirmed_payments"], dash["payment_reminders"]) == (1, [])
    [due] = dash["payments_due"]
    assert (due["total"], due["amount_paid"], due["still_owed"]) == (1000.0, 400.0, 600.0)

    h.sql("update supplier_payments set created_at = now() - interval '3 days' where id = %s", (waiting["id"],))
    [reminder] = h.client.get("/dashboard").json()["payment_reminders"]
    assert reminder["id"] == waiting["id"] and reminder["reminder_due"] is True
    assert h.sql("select status, reminder_sent_at is not null from supplier_payments where id = %s", (waiting["id"],)) == [
        ("unconfirmed", True)]
    assert [p["id"] for p in h.client.get("/payments", params={"status": "unconfirmed"}).json()] == [waiting["id"]]
    assert h.client.get("/payments", params={"status": "maybe"}).status_code == 422
    assert payment["status"] == "confirmed"


def test_reverifying_an_invoice_re_works_out_its_payment_status(h):
    """A paid invoice corrected to a larger total is no longer paid once verified again."""
    supplier = h.supplier()
    invoice = verified_invoice(h, supplier["id"], "V-1", total=1000.0)
    h.client.post("/payments", json={"supplier_id": supplier["id"], "invoice_id": invoice["id"], "amount": 1000.0,
                                     "paid_on": "2026-10-25", "confirm": True})
    assert invoice_state(h, invoice["id"]) == ("paid", 1000.0)
    lines = [{"item_name": "63mm uPVC pipe", "quantity": 1, "mro": 1100.0, "line_total": 1100.0},
             {"item_name": "PVC solvent cement 250ml", "quantity": 1, "mro": 100.0, "line_total": 100.0}]
    h.client.post(f"/invoices/{invoice['id']}/fix", json={"total": 1200.0, "lines": lines})
    verified = h.client.post(f"/invoices/{invoice['id']}/verify").json()
    assert (verified["status"], verified["payment_status"], verified["amount_paid"]) == ("verified", "unpaid", 1000.0)
    # Marking paid by hand still works (e.g. the rest was paid without a receipt).
    assert h.client.post(f"/invoices/{invoice['id']}/payment", json={"payment_status": "paid"}).json()["payment_status"] == "paid"


# ---- owner login ------------------------------------------------------------------------------
DATA_ROUTES = ["/dashboard", "/invoices", "/suppliers", "/products", "/payments",
               f"/suppliers/{MISSING}/profile", f"/invoices/{MISSING}"]


def test_every_data_route_needs_a_login(h):
    """No token or a bad token gets nothing; /health and the login itself stay open."""
    h.supplier()
    for path in DATA_ROUTES:
        anonymous = h.client.get(path, headers={"Authorization": ""})
        assert anonymous.status_code == 401 and anonymous.json()["error_type"] == "not_logged_in", path
        forged = h.client.get(path, headers={"Authorization": "Bearer made-up-token"})
        assert forged.status_code == 401 and forged.json()["error_type"] == "session_expired", path
    for method, path in (("post", "/suppliers"), ("post", "/payments"), ("post", "/invoices/upload")):
        assert getattr(h.client, method)(path, headers={"Authorization": ""}).status_code == 401, path
    assert h.client.get("/suppliers", headers={"Authorization": "Basic abc"}).status_code == 401
    assert h.client.get("/health", headers={"Authorization": ""}).status_code == 200
    assert len(h.client.get("/suppliers").json()) == 1  # the logged-in owner sees the data


def test_login_refresh_and_logout(h):
    """The owner logs in with email and password, renews the session, and logs out."""
    no_header = {"Authorization": ""}
    wrong = h.client.post("/auth/login", json={"email": OWNER_EMAIL, "password": "nope"}, headers=no_header)
    assert wrong.status_code == 401 and wrong.json() == {"error_type": "login_failed", "message": "Email or password is incorrect."}
    assert h.client.post("/auth/login", json={"email": "someone@example.com", "password": OWNER_PASSWORD}, headers=no_header).status_code == 401
    assert h.client.post("/auth/login", json={"email": OWNER_EMAIL}, headers=no_header).status_code == 422

    login = h.client.post("/auth/login", json={"email": f"  {OWNER_EMAIL} ", "password": OWNER_PASSWORD}, headers=no_header)
    assert login.status_code == 200, login.text
    session = login.json()
    assert (session["business_id"], session["business_name"], session["email"], session["expires_in"]) == (
        BIZ, "Hardware shop", OWNER_EMAIL, 3600)
    as_owner = {"Authorization": f"Bearer {session['access_token']}"}
    assert h.client.get("/invoices", headers=as_owner).status_code == 200

    renewed = h.client.post("/auth/refresh", json={"refresh_token": session["refresh_token"]}, headers=no_header).json()
    assert renewed["access_token"] != session["access_token"] and renewed["business_id"] == BIZ
    reused = h.client.post("/auth/refresh", json={"refresh_token": session["refresh_token"]}, headers=no_header)
    assert reused.status_code == 401 and reused.json()["error_type"] == "session_expired"

    new_owner = {"Authorization": f"Bearer {renewed['access_token']}"}
    assert h.client.post("/auth/logout", headers=new_owner).json() == {"ok": True}
    assert h.client.get("/invoices", headers=new_owner).status_code == 401
    assert h.client.post("/auth/logout", headers=no_header).status_code == 200  # nothing to end


def test_a_login_not_linked_to_a_shop_sees_nothing(h):
    """A Supabase account that is not in business_users cannot log in or read anything."""
    stranger = "bbbbbbbb-0000-0000-0000-00000000000b"
    h.auth.add_account("stranger@example.com", "pw-123456", stranger)
    add_login(h.pg_url, stranger, "stranger@example.com", business_id=None)
    refused = h.client.post("/auth/login", json={"email": "stranger@example.com", "password": "pw-123456"},
                            headers={"Authorization": ""})
    assert refused.status_code == 403 and refused.json()["error_type"] == "no_business"
    h.auth.tokens["stranger-token"] = stranger
    assert h.client.get("/invoices", headers={"Authorization": "Bearer stranger-token"}).status_code == 403


def test_each_login_reads_only_its_own_shop(h):
    """Rows are read and written for the logged-in owner's business only."""
    other_shop, other_owner = "00000000-0000-0000-0000-000000000002", "cccccccc-0000-0000-0000-00000000000c"
    h.sql("insert into businesses (id, name) values (%s, 'Other test shop')", (other_shop,))
    add_login(h.pg_url, other_owner, "other@example.com", business_id=other_shop)
    h.auth.tokens["other-token"] = other_owner
    other = {"Authorization": "Bearer other-token"}
    mine = h.supplier()
    assert h.client.get("/suppliers", headers=other).json() == []
    assert h.client.get(f"/suppliers/{mine['id']}", headers=other).status_code == 404
    theirs = h.client.post("/suppliers", json={"name": "Their Supplier"}, headers=other).json()
    assert theirs["business_id"] == other_shop
    assert [s["name"] for s in h.client.get("/suppliers").json()] == [SUPPLIER]


def test_login_switched_off_uses_the_default_shop(make_harness):
    """AUTH_REQUIRED=false (local tests only): no token needed, the default business is used."""
    h = make_harness(settings=replace(BASE_SETTINGS, auth_required=False))
    assert h.client.get("/suppliers", headers={"Authorization": ""}).status_code == 200
    assert h.client.get("/health").json()["login_required"] is False


def test_startup_refuses_login_without_supabase(pg_url):
    """With login required but no Supabase project configured, the API does not start."""
    from fastapi.testclient import TestClient

    from database.client import Database
    from main import build_services, create_app

    async def factory(settings):
        database = Database(pg_url, max_size=1)
        await database.open()
        try:
            return await build_services(settings, db=database, storage=None, vision_model="x", sku_model="y")
        except RuntimeError:
            await database.close()
            raise

    with pytest.raises(RuntimeError, match="Owner login needs SUPABASE_URL"):
        with TestClient(create_app(BASE_SETTINGS, factory)):
            pass


def test_invoice_list_pages(h):
    """The list comes a page at a time; X-Total-Count gives the total for the page buttons."""
    supplier = h.supplier()
    for n in range(7):
        h.client.post("/invoices", json=typed_invoice(supplier["id"], invoice_number=f"P-{n}",
                                                      invoice_date=f"2026-10-{10 + n:02d}"))
    first = h.client.get("/invoices", params={"limit": 3})
    assert first.headers["x-total-count"] == "7"
    assert [i["invoice_number"] for i in first.json()] == ["P-6", "P-5", "P-4"]
    second = h.client.get("/invoices", params={"limit": 3, "offset": 3}).json()
    last = h.client.get("/invoices", params={"limit": 3, "offset": 6}).json()
    assert [i["invoice_number"] for i in second] == ["P-3", "P-2", "P-1"] and [i["invoice_number"] for i in last] == ["P-0"]
    assert h.client.get("/invoices", params={"limit": 3, "offset": 9}).json() == []
    filtered = h.client.get("/invoices", params={"invoice_number": "P-1", "limit": 3})
    assert filtered.headers["x-total-count"] == "1"
    assert h.client.get("/invoices", params={"offset": -1}).status_code == 422
    preflight = h.client.options("/invoices", headers={"Origin": "http://localhost:3001", "Access-Control-Request-Method": "GET"})
    assert preflight.status_code == 200
    cors = h.client.get("/invoices", headers={"Origin": "http://localhost:3001"})
    assert "x-total-count" in cors.headers["access-control-expose-headers"].lower()


# ---- Purchase Analytics (Model 1) ----------------------------------------------------------------
def buy(h: Harness, supplier_id: str, number: str, day: date, rate: float, qty: int = 10) -> dict:
    """A verified invoice buying one product at ``rate`` per unit (the product exists, so the line links)."""
    body = {"supplier_id": supplier_id, "invoice_number": number, "invoice_date": day.isoformat(), "total": qty * rate,
            "lines": [{"item_name": "63mm uPVC pipe", "quantity": qty, "mro": rate, "line_total": qty * rate}]}
    invoice = h.client.post("/invoices", json=body).json()
    verified = h.client.post(f"/invoices/{invoice['id']}/verify")
    assert verified.status_code == 200, verified.text
    return verified.json()


def install_model(h: Harness, *, low: float = 0.98, high: float = 1.10, version: str = "item_price-test") -> None:
    """Put a trained Model 1 (latest-price baseline, -2% / +10% range) in the shop's models bucket."""
    import asyncio
    import math

    from ml.train import to_bytes

    data = to_bytes({"format": 1, "model": "item_price", "version": version, "method": "latest price", "learned": False,
                     "estimator": None, "range_log": (math.log(low), math.log(high)), "min_history": 3,
                     "report": {"trained_at": "2026-11-01T00:00:00+00:00", "range_pct": {"low": -2.0, "high": 10.0}}})
    asyncio.run(h.services.models_storage.put(f"{BIZ}/item_price.joblib", data, "application/octet-stream"))


def pipe_history(h: Harness) -> tuple[dict, dict, list[dict]]:
    """Six purchases of the pipe at Rs 585 and one at Rs 800, over the last months."""
    supplier = h.supplier()
    pipe = h.product("63mm uPVC pipe")
    start = date.today() - timedelta(days=200)
    rates = [585, 585, 585, 585, 800, 590, 590]
    invoices = [buy(h, supplier["id"], f"H-{n}", start + timedelta(days=25 * n), rate) for n, rate in enumerate(rates)]
    return supplier, pipe, invoices


def test_analytics_without_a_trained_model_shows_baselines(h):
    """Before ml/train.py has run: medians labelled as baselines, nothing flagged."""
    _, pipe, _ = pipe_history(h)
    overview = h.client.get("/analytics/overview").json()
    assert overview["model"]["trained"] is False and "not been trained" in overview["model"]["note"]
    [product] = overview["products"]
    assert (product["product_name"], product["purchases"], product["last_price"]) == ("63mm uPVC pipe", 7, 590.0)
    assert product["days_between"] == 25.0 and product["total_spend"] == 43200.0
    assert (product["next"]["expected"], product["next"]["is_baseline"], product["next"]["low"]) == (590.0, True, None)
    assert overview["flagged"] == [] and len(overview["monthly_purchases"]) == 12
    detail = h.client.get(f"/analytics/products/{pipe['id']}").json()
    assert {p["status"] for p in detail["points"]} == {"no_range"}
    assert h.sql("select count(*) from model_predictions") == [(0,)]


def test_analytics_with_model_1_flags_prices_and_keeps_predictions(h):
    """With the shop's Model 1: each price judged from earlier purchases, the outlier flagged."""
    install_model(h)
    _, pipe, invoices = pipe_history(h)
    overview = h.client.get("/analytics/overview").json()
    assert overview["model"] | {"note": ""} == {
        "trained": True, "version": "item_price-test", "method": "latest price", "learner_kept": False,
        "trained_at": "2026-11-01T00:00:00+00:00", "range_low_pct": -2.0, "range_high_pct": 10.0, "note": ""}
    assert "baseline" in overview["model"]["note"]
    flagged = overview["flagged"]
    assert [(f["invoice_number"], f["status"], f["unit_price"], f["expected"]) for f in flagged] == [
        ("H-5", "below", 590.0, 800.0), ("H-4", "above", 800.0, 585.0)]
    assert flagged[1]["difference_pct"] == 36.8 and "pack size" in flagged[1]["message"]
    assert flagged[1]["invoice_id"] == invoices[4]["id"] and flagged[1]["supplier_name"] == SUPPLIER

    detail = h.client.get(f"/analytics/products/{pipe['id']}").json()
    assert [p["status"] for p in detail["points"]] == ["no_range", "no_range", "no_range", "inside", "above", "below", "inside"]
    assert detail["points"][3] | {"day": None, "invoice_id": None} == {
        "day": None, "invoice_id": None, "invoice_number": "H-3", "supplier_name": SUPPLIER, "quantity": 10.0,
        "unit_price": 585.0, "expected": 585.0, "low": 573.3, "high": 643.5, "status": "inside", "difference_pct": 0.0}
    next_price = detail["product"]["next"]
    assert (next_price["expected"], next_price["low"], next_price["high"], next_price["is_baseline"]) == (590.0, 578.2, 649.0, True)
    assert sum(m["total"] for m in detail["spend_by_month"]) == 43200.0
    h.client.get(f"/analytics/products/{pipe['id']}")
    assert h.sql("select model_name, model_version, horizon, predicted_value::float, lower_bound::float, baseline_name "
                 "from model_predictions") == [("item_price", "item_price-test", "next purchase", 590.0, 578.2, "rolling median")]
    assert h.client.get(f"/analytics/products/{MISSING}").status_code == 404


def test_invoice_price_check_before_verifying(h):
    """A new draft's lines are checked against earlier purchases (the draft itself is left out)."""
    install_model(h)
    supplier, _, _ = pipe_history(h)
    h.product("Elbow 63mm")
    draft = h.client.post("/invoices", json={
        "supplier_id": supplier["id"], "invoice_number": "NEW-1", "invoice_date": date.today().isoformat(), "total": 9620.0,
        "lines": [{"item_name": "63mm uPVC pipe", "quantity": 10, "mro": 950.0, "line_total": 9500.0},
                  {"item_name": "Elbow 63mm", "quantity": 1, "mro": 120.0, "line_total": 120.0}]}).json()
    pipe_line, elbow_line = h.client.get(f"/analytics/invoices/{draft['id']}/price-check").json()
    assert (pipe_line["status"], pipe_line["expected"], pipe_line["unit_price"], pipe_line["difference_pct"]) == ("above", 590.0, 950.0, 61.0)
    assert "Check the unit price and pack size" in pipe_line["message"]
    assert (elbow_line["status"], elbow_line["expected"]) == ("no_range", None) and "No earlier purchases" in elbow_line["message"]
    assert h.client.get(f"/analytics/invoices/{MISSING}/price-check").status_code == 404


def test_analytics_needs_a_login(h):
    """Purchase Analytics is shop data: no login, no data."""
    for path in (
        "/analytics/overview",
        "/analytics/cash-overview",
        f"/analytics/products/{MISSING}",
        f"/analytics/invoices/{MISSING}/price-check",
    ):
        assert h.client.get(path, headers={"Authorization": ""}).status_code == 401


def test_cash_overview_series_length_and_monthly(h):
    """GET /analytics/cash-overview returns a full payable window and monthly purchases+paid."""
    supplier = h.supplier(credit_days=10)
    inv = h.client.post("/invoices", json={
        "supplier_id": supplier["id"],
        "invoice_number": "CASH-1",
        "invoice_date": date.today().isoformat(),
        "total": 1000.0,
        "lines": [{"item_name": "63mm uPVC pipe", "quantity": 10, "mro": 100.0, "line_total": 1000.0}],
    }).json()
    # Confirm SKU so item pie can group by product when present; verification still works without it.
    h.client.post(f"/invoices/{inv['id']}/verify")
    overview = h.client.get("/analytics/cash-overview?payable_days=30&months=3").json()
    assert overview["payable_days"] == 30 and overview["months"] == 3
    assert len(overview["payable_series"]) == 30
    assert overview["payable_series"][0]["day"] == date.today().isoformat()
    assert len(overview["monthly"]) == 3
    assert {"month", "purchases", "paid"} <= set(overview["monthly"][-1])
    assert isinstance(overview["spend_by_supplier"], list)
    assert isinstance(overview["spend_by_item"], list)
    assert "overdue" in overview
    assert {"total_owed", "invoice_count", "invoices"} <= set(overview["overdue"])


# ---- Purchasing Cash Planner (Model 2) ----------------------------------------------------------
def test_cash_plan_endpoint_with_and_without_model_2(h):
    """The planner works before Model 2 is trained (baseline) and with it (range, kept predictions)."""
    import asyncio

    from ml.spend import add_months, to_bytes, train_purchase_spend
    from tests.test_planner import seasonal_invoices

    supplier = h.supplier(credit_days=10)
    verified_invoice(h, supplier["id"], "T-100", day=(date.today() - timedelta(days=5)).isoformat())
    plan = h.client.get("/analytics/cash-plan", params={"days": 30}).json()
    assert plan["model"]["trained"] is False and plan["bills_due"] == 6750.0
    assert [o["invoice_number"] for o in plan["obligations"]] == ["T-100"]
    assert plan["reserve"] == pytest.approx(plan["bills_due"] + plan["new_purchases_due"]["expected"])
    assert h.client.get("/analytics/cash-plan", params={"days": 45}).status_code == 422
    assert h.client.get("/analytics/cash-plan", headers={"Authorization": ""}).status_code == 401

    this_month = date.today().replace(day=1)
    for k in range(1, 15):  # 14 months of purchases, so there is a history to forecast from
        verified_invoice(h, supplier["id"], f"M-{k}", total=2000.0 + 50 * k, day=(add_months(this_month, -k) + timedelta(days=4)).isoformat())
    artifact, _ = train_purchase_spend(seasonal_invoices(40, start=add_months(date.today().replace(day=1), -40)),
                                       today=date.today(), business_id=BIZ)
    asyncio.run(h.services.models_storage.put(f"{BIZ}/purchase_spend.joblib", to_bytes(artifact), "application/octet-stream"))
    h.services._spend_models.clear()
    plan = h.client.get("/analytics/cash-plan", params={"days": 30}).json()
    assert plan["model"]["trained"] is True and plan["model"]["version"] == artifact["version"]
    h.client.get("/analytics/cash-plan", params={"days": 30})
    saved = h.sql("select model_name, count(*) from model_predictions group by 1")
    assert len(plan["months"]) == 4 and all(m["low"] is not None for m in plan["months"])
    assert saved == [("purchase_spend", 4)]
    dash = h.client.get("/dashboard").json()["forecast"]
    assert dash["bills_due"] == plan["bills_due"] == 40000.0 and dash["model_note"] == plan["model"]["note"]


# ---- unlink a product match -----------------------------------------------------------------------
def test_unlink_a_wrong_product_and_rebuild_the_history(h):
    """Proposal: owner confirmation, an audit log of every link, and the ability to unlink and
    rebuild a history. A verified invoice goes back to draft until the line is linked again."""
    supplier = h.supplier()
    wrong = h.product("Wall Paint 4L")
    right = h.product("63mm uPVC pipe Type 1000")
    draft = h.client.post("/invoices", json={
        "supplier_id": supplier["id"], "invoice_number": "U-1", "invoice_date": "2026-10-21", "total": 5850.0,
        "lines": [{"item_name": "PIPE 63 T1000 5.8M", "quantity": 10, "mro": 585.0, "line_total": 5850.0}]}).json()
    line_id = draft["lines"][0]["id"]
    linked = h.client.post(f"/invoices/{draft['id']}/confirm-sku", json={"line_id": line_id, "product_id": wrong["id"]})
    assert linked.status_code == 200 and h.client.post(f"/invoices/{draft['id']}/verify").json()["status"] == "verified"
    assert h.sql("select confirmed from product_aliases where source = 'owner_confirmed'") == [(True,)]

    unlinked = h.client.post(f"/invoices/{draft['id']}/unlink-sku", json={"line_id": line_id})
    assert unlinked.status_code == 200, unlinked.text
    body = unlinked.json()
    line = body["lines"][0]
    assert (body["status"], body["verified_at"], line["product_id"], line["match_status"]) == ("draft", None, None, "pending")
    assert "Unlinked by the owner" in line["match_reason"]
    assert events(h, draft["id"])[-1] == "sku_unlinked"
    logged = h.client.get(f"/invoices/{draft['id']}/events").json()[-1]["payload"]
    assert (logged["product_id"], logged["product_name"], logged["previous_status"], logged["alias_forgotten"]) == (
        wrong["id"], "Wall Paint 4L", "verified", True)
    # The wrong product's history no longer has this line, and the printed text no longer points at it.
    assert h.sql("select count(*) from invoice_items l join invoices i on i.id = l.invoice_id "
                 "where i.status = 'verified' and l.product_id is not null") == [(0,)]
    assert h.sql("select confirmed from product_aliases where source = 'owner_confirmed'") == [(False,)]
    assert h.sql("select count(*) from product_aliases where source = 'catalogue' and confirmed") == [(2,)]
    assert h.client.post(f"/invoices/{draft['id']}/verify").status_code == 409

    relinked = h.client.post(f"/invoices/{draft['id']}/confirm-sku", json={"line_id": line_id, "product_id": right["id"]})
    assert relinked.json()["lines"][0]["product_name"] == "63mm uPVC pipe Type 1000"
    assert h.client.post(f"/invoices/{draft['id']}/verify").json()["status"] == "verified"
    assert h.sql("select p.name from invoice_items l join products p on p.id = l.product_id") == [("63mm uPVC pipe Type 1000",)]


def test_unlink_refusals(h):
    """Only a linked line of a draft or verified invoice can be unlinked."""
    supplier = h.supplier()
    invoice = verified_invoice(h, supplier["id"], "U-2")
    line_id = invoice["lines"][0]["id"]
    assert h.client.post(f"/invoices/{invoice['id']}/unlink-sku", json={"line_id": MISSING}).status_code == 404
    assert h.client.post(f"/invoices/{MISSING}/unlink-sku", json={"line_id": line_id}).status_code == 404
    assert h.client.post(f"/invoices/{invoice['id']}/unlink-sku", json={"line_id": line_id}).status_code == 200
    again = h.client.post(f"/invoices/{invoice['id']}/unlink-sku", json={"line_id": line_id})
    assert again.status_code == 409 and again.json()["error_type"] == "not_linked"
    h.client.post(f"/invoices/{invoice['id']}/reject", json={"reason": "duplicate"})
    other_line = invoice["lines"][1]["id"]
    refused = h.client.post(f"/invoices/{invoice['id']}/unlink-sku", json={"line_id": other_line})
    assert refused.status_code == 409 and refused.json()["error_type"] == "rejected"
    assert h.client.post(f"/invoices/{invoice['id']}/unlink-sku", json={"line_id": line_id},
                         headers={"Authorization": ""}).status_code == 401
