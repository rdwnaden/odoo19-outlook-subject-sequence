import hashlib
import hmac
import json
import re

from psycopg2 import IntegrityError
from odoo import http
from odoo.http import request

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
MAX_BODY = 16384


class OutlookSubjectSequenceController(http.Controller):
    def _response(self, data, status=200):
        return request.make_json_response(data, status=status)

    def _audit(self, event, key="", dept="", doc_type="", external="", number="", details=""):
        try:
            request.env["outlook.subject.api.settings"].sudo().audit(
                event, key, dept, doc_type, external, number,
                request.httprequest.remote_addr or "", details
            )
        except Exception:
            pass

    def _authorized(self):
        auth = request.httprequest.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        supplied = auth[7:].strip()
        expected = request.env["outlook.subject.api.settings"].sudo().get_token()
        return bool(expected and supplied and hmac.compare_digest(supplied.encode(), expected.encode()))

    @http.route("/api/outlook/subject-sequence", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def generate(self, **kwargs):
        if request.httprequest.mimetype != "application/json":
            return self._response({"ok": False, "error": "content_type_must_be_application_json"}, 415)
        raw = request.httprequest.get_data(cache=True) or b""
        if len(raw) > MAX_BODY:
            return self._response({"ok": False, "error": "request_too_large"}, 413)
        if not self._authorized():
            self._audit("rejected", details="authentication_failed")
            return self._response({"ok": False, "error": "unauthorized"}, 401)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._response({"ok": False, "error": "invalid_json"}, 400)
        if not isinstance(data, dict):
            return self._response({"ok": False, "error": "json_object_required"}, 400)

        dept = str(data.get("department_code", "")).strip()
        doc_type = str(data.get("document_type_code", "")).strip()
        key = str(data.get("idempotency_key", "")).strip()
        external = str(data.get("external_reference", "")).strip()[:128]
        requested_by = str(data.get("requested_by", "")).strip()[:128]
        if not CODE_RE.fullmatch(dept) or not CODE_RE.fullmatch(doc_type):
            return self._response({"ok": False, "error": "invalid_department_or_document_type_code"}, 400)
        if not KEY_RE.fullmatch(key):
            return self._response({"ok": False, "error": "invalid_idempotency_key"}, 400)

        semantic = {"department_code": dept, "document_type_code": doc_type,
                    "external_reference": external, "requested_by": requested_by}
        payload_hash = hashlib.sha256(json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        Request = request.env["outlook.subject.request"].sudo()
        existing = Request.search([("idempotency_key", "=", key)], limit=1)
        if existing:
            if existing.payload_hash != payload_hash:
                self._audit("rejected", key, dept, doc_type, external, details="key_payload_mismatch")
                return self._response({"ok": False, "error": "idempotency_key_reused_with_different_payload"}, 409)
            result = json.loads(existing.result_json)
            result["idempotent_replay"] = True
            self._audit("replayed", key, dept, doc_type, external, existing.subject_number, "idempotent_replay")
            return self._response(result)

        Department = request.env["outlook.subject.department"].sudo()
        DocType = request.env["outlook.subject.document.type"].sudo()
        Config = request.env["outlook.subject.sequence.config"].sudo()
        department = Department.search([("code", "=", dept), ("active", "=", True)], limit=1)
        document_type = DocType.search([("code", "=", doc_type), ("active", "=", True)], limit=1)
        if not department or not document_type:
            self._audit("rejected", key, dept, doc_type, external, details="unknown_or_inactive_code")
            return self._response({"ok": False, "error": "unknown_or_inactive_department_or_document_type"}, 404)
        config = Config.search([("department_id", "=", department.id),
                                ("document_type_id", "=", document_type.id),
                                ("active", "=", True)], limit=1)
        if not config:
            self._audit("rejected", key, dept, doc_type, external, details="configuration_missing")
            return self._response({"ok": False, "error": "sequence_configuration_not_found"}, 404)

        try:
            with request.env.cr.savepoint():
                number = config.generate_number()
                result = {"ok": True, "subject_number": number, "department_code": dept,
                          "document_type_code": doc_type, "external_reference": external or None,
                          "idempotent_replay": False}
                Request.create({"idempotency_key": key, "payload_hash": payload_hash,
                                "department_id": department.id, "document_type_id": document_type.id,
                                "external_reference": external, "subject_number": number,
                                "requested_by": requested_by, "result_json": json.dumps(result)})
        except IntegrityError:
            existing = Request.search([("idempotency_key", "=", key)], limit=1)
            if existing and existing.payload_hash == payload_hash:
                result = json.loads(existing.result_json)
                result["idempotent_replay"] = True
                self._audit("replayed", key, dept, doc_type, external, existing.subject_number, "concurrent_replay")
                return self._response(result)
            return self._response({"ok": False, "error": "idempotency_conflict_retry_same_key"}, 409)

        self._audit("issued", key, dept, doc_type, external, number, "number_issued")
        return self._response(result, 201)

    @http.route("/api/outlook/subject-sequence/health", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    def health(self, **kwargs):
        return self._response({"ok": True, "service": "outlook-subject-sequence"})
