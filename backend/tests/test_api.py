"""End-to-end API tests: FastAPI + pipeline + real Agents SDK runner with scripted models."""

from __future__ import annotations

import base64
from dataclasses import replace

import pytest

from tests.harness import BASE_SETTINGS, JPEG, Harness
from tests.helpers import invoice_doc, invoice_line, receipt_doc, vision_step


@pytest.fixture
def h(make_harness) -> Harness:
    return make_harness()


def event_types(h: Harness, invoice_id: str) -> list[str]:
    return [event["event_type"] for event in h.client.get(f"/invoices/{invoice_id}/events").json()]


# /health and /extract (legacy contract used by the current frontend) ---------------------------
def test_health(h):
    assert h.client.get("/health").json() == {
        "ok": True, "repository": "memory", "openai_configured": False, "image_storage": True,
    }


def test_extract_keeps_the_prototype_response_shape(h):
    h.vision.enqueue(vision_step(invoice_doc()))
    response = h.client.post("/extract", files={"file": JPEG})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"document", "missing_fields", "partial", "message", "preview_png_base64", "region_previews"}
    assert body["partial"] is False and body["missing_fields"] == [] and body["message"] is None
    line = body["document"]["invoice"]["lines"][0]
    assert line["mro"] == 585.0 and line["item_name"] == "63mm uPVC pipe"
    assert base64.b64decode(body["preview_png_base64"]).startswith(b"\x89PNG")
    assert set(body["region_previews"]) == {"supplier_panel", "header_right", "body", "footer"}
    # /extract saves no invoice but keeps the raw agent output
    assert h.services.repo.invoices == {}
    assert h.services.repo.extraction_runs[0]["guardrail_passed"] is True


def test_extract_partial_read(h):
    h.vision.enqueue(vision_step(invoice_doc(supplier=None)))
    body = h.client.post("/extract", files={"file": JPEG}).json()
    assert body["partial"] is True and body["missing_fields"] == ["supplier.name"]
    assert body["message"].startswith("Partial read")


def test_extract_guardrail_is_422(h):
    h.vision.enqueue(vision_step(invoice_doc(lines=[], total=0)))
    response = h.client.post("/extract", files={"file": JPEG})
    assert response.status_code == 422
    body = response.json()
    assert body["error_type"] == "reading_unsuccessful"
    assert "invoice.lines" in body["missing_fields"]
    assert "preview_png_base64" in body
    assert h.services.repo.extraction_runs[0]["guardrail_passed"] is False


def test_extract_model_failure_is_502(h):
    h.vision.enqueue(RuntimeError("upstream timeout"))
    response = h.client.post("/extract", files={"file": JPEG})
    assert response.status_code == 502
    assert response.json()["error_type"] == "api_error"
    assert "upstream timeout" in response.json()["message"]


def test_extract_undecodable_image_never_reaches_the_model(h):
    response = h.client.post("/extract", files={"file": ("x.jpg", b"garbage", "image/jpeg")})
    assert response.status_code == 422
    assert response.json()["missing_fields"] == ["image"]
    assert h.vision.calls == ()


def test_upload_type_and_size_limits(make_harness):
    h = make_harness(settings=replace(BASE_SETTINGS, max_upload_mb=0.01))
    assert h.client.post("/extract", files={"file": ("a.txt", b"hello", "text/plain")}).status_code == 415
    assert h.client.post("/invoices/upload", files={"file": JPEG}).status_code == 413
    assert h.vision.calls == ()


# /invoices/upload -> verify -------------------------------------------------------------------
def test_happy_path_upload_then_verify(h):
    supplier = h.supplier(credit_days=30)
    pipe = h.product("63mm uPVC pipe")
    cement = h.product("PVC solvent cement 250ml")

    response = h.upload(invoice_doc())
    assert response.status_code == 201, response.text
    invoice = response.json()
    assert (invoice["status"], invoice["source"], invoice["math_ok"]) == ("draft", "agent", True)
    assert invoice["supplier_id"] == supplier["id"]
    assert invoice["missing_fields"] == []
    assert [(l["match_status"], l["product_id"]) for l in invoice["lines"]] == [
        ("linked", pipe["id"]), ("linked", cement["id"])
    ]
    assert invoice["lines"][0]["mro"] == 585.0
    assert invoice["image_signed_url"] == f"memory://{invoice['image_path']}"
    assert invoice["image_path"].endswith("/original.jpg")
    assert len(h.services.images.objects) == 2  # original + CLAHE copy
    assert h.services.repo.extraction_runs[-1]["invoice_id"] == invoice["id"]

    verified = h.client.post(f"/invoices/{invoice['id']}/verify")
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "verified"
    assert verified.json()["due_date"] == "2026-11-19"  # 2026-10-20 + 30 credit days
    assert event_types(h, invoice["id"]) == ["invoice_extracted", "sku_matched", "invoice_verified"]

    listed = h.client.get("/invoices", params={"status": "verified"}).json()
    assert [row["id"] for row in listed] == [invoice["id"]]
    assert h.client.get("/invoices", params={"supplier_id": supplier["id"], "status": "draft"}).json() == []


def test_math_failure_flow_fix_confirm_verify_and_learning(h):
    h.supplier()
    h.product("Elbow 63mm")
    bad = invoice_doc(lines=[invoice_line("63mm uPVC pipe", 10, 585.0, line_total=5580.0)], total=5580.0)
    invoice = h.upload(bad).json()
    assert invoice["math_ok"] is False
    assert invoice["math_issues"][0]["code"] == "line_total_mismatch"
    assert invoice["lines"][0]["match_status"] == "unmatched"  # matching waits for the fix

    refused = h.client.post(f"/invoices/{invoice['id']}/verify")
    assert refused.status_code == 409
    assert refused.json()["error_type"] == "not_verifiable"
    assert any("math audit" in reason for reason in refused.json()["reasons"])

    line = invoice["lines"][0]
    fixed = h.client.post(
        f"/invoices/{invoice['id']}/fix",
        json={"total": 5850.0, "lines": [{"id": line["id"], "item_name": line["item_name"], "quantity": 10,
                                          "mro": 585.0, "line_total": 5850.0}]},
    ).json()
    assert fixed["math_ok"] is True and fixed["math_issues"] == []
    assert fixed["lines"][0]["match_status"] in {"pending", "new_product", "linked"}

    created = h.client.post(
        f"/invoices/{invoice['id']}/confirm-sku",
        json={"line_id": fixed["lines"][0]["id"], "new_product": {"name": "uPVC Pipe 63mm", "unit": "length"}},
    )
    assert created.status_code == 200, created.text
    linked = created.json()["lines"][0]
    assert linked["match_status"] == "linked" and linked["match_reason"] == "Confirmed by the owner."
    assert h.client.post(f"/invoices/{invoice['id']}/verify").status_code == 200
    assert event_types(h, invoice["id"]) == [
        "invoice_extracted", "invoice_corrected", "sku_matched", "sku_confirmed", "invoice_verified"
    ]

    # The confirmed printed text is remembered: the next invoice links it with no agent decision.
    again = h.upload(invoice_doc(number="INV-1002", lines=[invoice_line("63MM  uPVC-pipe", 4, 585.0)])).json()
    assert again["lines"][0]["match_status"] == "linked"
    assert again["lines"][0]["product_id"] == linked["product_id"]
    assert again["lines"][0]["match_similarity"] == 1.0


def test_below_threshold_suggests_and_waits(make_harness):
    h = make_harness(settings=replace(BASE_SETTINGS, sku_match_threshold=0.99))
    h.supplier()
    pipe = h.product("uPVC Pipe 63mm")
    line = h.upload(invoice_doc(lines=[invoice_line("upvc pipe 63 mm", 1, 585.0)])).json()["lines"][0]
    assert line["match_status"] == "pending"
    assert line["product_id"] is None and line["suggested_product_id"] == pipe["id"]
    assert "below the 0.99 threshold" in line["match_reason"]
    assert line["match_candidates"][0]["product_id"] == pipe["id"]


def test_duplicate_invoice_is_refused(h):
    h.supplier()
    first = h.upload(invoice_doc()).json()
    second = h.upload(invoice_doc())
    assert second.status_code == 409
    assert second.json() == {
        "error_type": "duplicate_invoice",
        "message": "That invoice number is already saved for this supplier.",
        "invoice_id": first["id"],
    }
    assert len(h.services.repo.invoices) == 1


def test_unknown_supplier_is_saved_for_review(h):
    invoice = h.upload(invoice_doc(supplier="Brand New Hardware Co")).json()
    assert invoice["supplier_id"] is None
    assert invoice["supplier_name_raw"] == "Brand New Hardware Co"
    assert "supplier_id" in invoice["missing_fields"]
    reasons = h.client.post(f"/invoices/{invoice['id']}/verify").json()["reasons"]
    assert "Pick or create the supplier." in reasons

    supplier = h.supplier("Brand New Hardware Co", credit_days=None)
    fixed = h.client.post(f"/invoices/{invoice['id']}/fix", json={"supplier_id": supplier["id"]}).json()
    assert fixed["supplier_id"] == supplier["id"] and "supplier_id" not in fixed["missing_fields"]


def test_upload_with_chosen_supplier(h):
    chosen = h.supplier("Chosen Supplier")
    invoice = h.upload(invoice_doc(supplier="Something Else"), supplier_id=chosen["id"]).json()
    assert invoice["supplier_id"] == chosen["id"]
    missing = h.client.post("/invoices/upload", files={"file": JPEG},
                            data={"supplier_id": "99999999-9999-9999-9999-999999999999"})
    assert missing.status_code == 404
    assert h.client.post("/invoices/upload", files={"file": JPEG}, data={"supplier_id": "nope"}).status_code == 422


def test_guardrail_on_upload_saves_nothing(h):
    response = h.upload(invoice_doc(date=None))
    assert response.status_code == 422
    assert response.json()["missing_fields"][0] == "invoice.invoice_date"
    assert h.services.repo.invoices == {}
    assert h.services.repo.extraction_runs[-1]["guardrail_passed"] is False


def test_receipt_upload_is_not_supported_yet(h):
    response = h.upload(receipt_doc())
    assert response.status_code == 422
    assert response.json()["error_type"] == "unsupported_document"
    assert h.services.repo.invoices == {}


def test_model_outage_on_upload_is_502(h):
    response = h.upload(RuntimeError("openai down"))
    assert response.status_code == 502 and response.json()["error_type"] == "api_error"


def test_future_date_blocks_verification(h):
    h.supplier()
    h.product("63mm uPVC pipe")
    invoice = h.upload(invoice_doc(date="2099-01-01", lines=[invoice_line("63mm uPVC pipe", 1, 585.0)])).json()
    assert invoice["field_issues"][0]["code"] == "future_date"
    assert "Check the invoice dates." in h.client.post(f"/invoices/{invoice['id']}/verify").json()["reasons"]
    fixed = h.client.post(f"/invoices/{invoice['id']}/fix", json={"invoice_date": "2026-10-21"}).json()
    assert fixed["field_issues"] == []
    assert h.client.post(f"/invoices/{invoice['id']}/verify").status_code == 200


def test_fix_keeps_links_for_unchanged_lines_and_reverts_verified(h):
    h.supplier()
    h.product("63mm uPVC pipe")
    h.product("PVC solvent cement 250ml")
    invoice = h.upload(invoice_doc()).json()
    assert h.client.post(f"/invoices/{invoice['id']}/verify").json()["status"] == "verified"
    first, second = invoice["lines"]
    fixed = h.client.post(
        f"/invoices/{invoice['id']}/fix",
        json={
            "total": 5850.0 + 600.0,
            "lines": [
                {"id": first["id"], "item_name": first["item_name"], "quantity": 10, "mro": 585.0, "line_total": 5850.0},
                {"id": second["id"], "item_name": "Gate valve 1in", "quantity": 1, "mro": 600.0, "line_total": 600.0},
            ],
        },
    ).json()
    assert fixed["status"] == "draft" and fixed["verified_at"] is None
    assert fixed["lines"][0]["match_status"] == "linked"
    assert fixed["lines"][0]["product_id"] == first["product_id"]
    assert fixed["lines"][1]["product_id"] != second["product_id"]


def test_fix_refuses_a_duplicate_number(h):
    h.supplier()
    h.upload(invoice_doc(number="INV-1"))
    other = h.upload(invoice_doc(number="INV-2")).json()
    response = h.client.post(f"/invoices/{other['id']}/fix", json={"invoice_number": "INV-1"})
    assert response.status_code == 409


def test_confirm_sku_validation(h):
    h.supplier()
    invoice = h.upload(invoice_doc()).json()
    line_id = invoice["lines"][0]["id"]
    url = f"/invoices/{invoice['id']}/confirm-sku"
    assert h.client.post(url, json={"line_id": line_id}).status_code == 422
    both = {"line_id": line_id, "product_id": line_id, "new_product": {"name": "x"}}
    assert h.client.post(url, json=both).status_code == 422
    unknown = "99999999-9999-9999-9999-999999999999"
    assert h.client.post(url, json={"line_id": unknown, "new_product": {"name": "x"}}).status_code == 404
    assert h.client.post(url, json={"line_id": line_id, "product_id": unknown}).status_code == 404
    h.product("Taken Name")
    dup = h.client.post(url, json={"line_id": line_id, "new_product": {"name": "taken name"}})
    assert dup.status_code == 409 and dup.json()["error_type"] == "duplicate_product"


def test_not_found_and_bad_ids(h):
    missing = "99999999-9999-9999-9999-999999999999"
    assert h.client.get(f"/invoices/{missing}").status_code == 404
    assert h.client.get(f"/invoices/{missing}/events").status_code == 404
    assert h.client.post(f"/invoices/{missing}/verify").status_code == 404
    assert h.client.post(f"/invoices/{missing}/fix", json={}).status_code == 404
    assert h.client.get("/invoices/not-a-uuid").status_code == 422
    assert h.client.get(f"/suppliers/{missing}").status_code == 404


def test_suppliers_and_products_endpoints(h):
    supplier = h.supplier("Ceylon Paints", credit_days=None)
    patched = h.client.patch(f"/suppliers/{supplier['id']}", json={"credit_days": 45}).json()
    assert patched["credit_days"] == 45
    assert h.client.patch(f"/suppliers/{supplier['id']}", json={"credit_days": -3}).status_code == 422
    assert [s["name"] for s in h.client.get("/suppliers").json()] == ["Ceylon Paints"]
    h.product("Wall Paint 4L", sku="WP-4")
    assert h.client.post("/products", json={"name": "wall paint 4l"}).status_code == 409
    assert [p["sku"] for p in h.client.get("/products").json()] == ["WP-4"]


def test_cors(make_harness):
    h = make_harness(settings=replace(
        BASE_SETTINGS,
        allowed_origins=("https://businesssense.vercel.app",),
        allowed_origin_regex=r"https://businesssense-[a-z0-9-]+\.vercel\.app",
    ))
    def preflight(origin: str):
        return h.client.options("/invoices", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    assert preflight("https://businesssense.vercel.app").headers["access-control-allow-origin"] == "https://businesssense.vercel.app"
    assert preflight("https://businesssense-git-feature-x.vercel.app").status_code == 200
    assert "access-control-allow-origin" not in preflight("https://evil.example").headers
