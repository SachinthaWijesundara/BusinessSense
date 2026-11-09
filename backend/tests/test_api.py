"""End-to-end tests of every API endpoint: FastAPI + pipeline + the real Agents SDK runner with
scripted models + Postgres. Each test also checks what landed in the database."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from tests.conftest import BASE_SETTINGS, JPEG, SUPPLIER, Harness, invoice_doc, invoice_line, receipt_doc

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
    assert h.client.get("/health").json() == {"ok": True, "database": "postgres", "openai_configured": False, "image_storage": True}


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
    """Unreadable photo, receipt, model outage and broken files: refused, nothing saved."""
    guardrail = h.upload(invoice_doc(date=None))
    assert guardrail.status_code == 422 and guardrail.json()["error_type"] == "reading_unsuccessful"
    assert guardrail.json()["missing_fields"][0] == "invoice.invoice_date"
    receipt = h.upload(receipt_doc())
    assert receipt.status_code == 422 and receipt.json()["error_type"] == "unsupported_document"
    outage = h.upload(RuntimeError("openai down"))
    assert outage.status_code == 502 and outage.json()["error_type"] == "api_error"
    broken = h.client.post("/invoices/upload", files={"file": ("x.jpg", b"garbage", "image/jpeg")})
    assert broken.status_code == 422 and broken.json()["missing_fields"] == ["image"]
    assert h.client.post("/invoices/upload", files={"file": ("a.txt", b"hello", "text/plain")}).status_code == 415
    assert h.sql("select count(*) from invoices") == [(0,)]
    assert h.sql("select guardrail_passed from extraction_runs order by created_at") == [(False,), (True,), (None,)]


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
                                 "next_due_date": "2026-10-16"}
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
    assert dash["forecast"] is None and "Model 2" in dash["forecast_note"]


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
