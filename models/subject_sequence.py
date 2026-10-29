import hashlib
import json
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class OutlookSubjectDepartment(models.Model):
    _name = "outlook.subject.department"
    _description = "Outlook Subject Department"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True)
    active = fields.Boolean(default=True)
    sequence_config_ids = fields.One2many("outlook.subject.sequence.config", "department_id")

    _sql_constraints = [("code_unique", "unique(code)", "Department code must be unique.")]

    def write(self, vals):
        if "code" in vals and any(self.mapped("sequence_config_ids")):
            raise UserError(_("Code cannot change after sequence configuration exists."))
        return super().write(vals)


class OutlookSubjectDocumentType(models.Model):
    _name = "outlook.subject.document.type"
    _description = "Outlook Subject Document Type"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True)
    active = fields.Boolean(default=True)
    sequence_config_ids = fields.One2many("outlook.subject.sequence.config", "document_type_id")

    _sql_constraints = [("code_unique", "unique(code)", "Document type code must be unique.")]

    def write(self, vals):
        if "code" in vals and any(self.mapped("sequence_config_ids")):
            raise UserError(_("Code cannot change after sequence configuration exists."))
        return super().write(vals)


class OutlookSubjectSequenceConfig(models.Model):
    _name = "outlook.subject.sequence.config"
    _description = "Outlook Subject Sequence Configuration"
    _order = "department_id, document_type_id"

    name = fields.Char(compute="_compute_name", store=True)
    department_id = fields.Many2one("outlook.subject.department", required=True, ondelete="restrict")
    document_type_id = fields.Many2one("outlook.subject.document.type", required=True, ondelete="restrict")
    sequence_id = fields.Many2one("ir.sequence", required=True, ondelete="restrict")
    prefix = fields.Char(default="%(year)s/%(department)s/%(doctype)s/", required=True)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("pair_unique", "unique(department_id, document_type_id)", "Only one config per department/document type."),
    ]

    @api.depends("department_id.name", "document_type_id.name")
    def _compute_name(self):
        for rec in self:
            rec.name = "%s / %s" % (rec.department_id.name or "", rec.document_type_id.name or "")

    def generate_number(self):
        self.ensure_one()

        if (
            not self.active
            or not self.department_id.active
            or not self.document_type_id.active
        ):
            raise UserError(_("Sequence configuration is inactive."))

        number = self.sequence_id.next_by_id()
        year = fields.Date.context_today(self).strftime("%Y")

        prefix = (self.prefix or "").replace("%(year)s", year)
        prefix = prefix.replace(
            "%(department)s",
            self.department_id.code
        )
        prefix = prefix.replace(
            "%(doctype)s",
            self.document_type_id.name
        )

        return prefix + number


class OutlookSubjectRequest(models.Model):
    _name = "outlook.subject.request"
    _description = "Outlook Subject Sequence Request"
    _order = "create_date desc"

    idempotency_key = fields.Char(required=True, index=True, copy=False)
    payload_hash = fields.Char(required=True, copy=False)
    department_id = fields.Many2one("outlook.subject.department", required=True, ondelete="restrict")
    document_type_id = fields.Many2one("outlook.subject.document.type", required=True, ondelete="restrict")
    external_reference = fields.Char(index=True)
    subject_number = fields.Char(required=True, index=True, copy=False)
    requested_by = fields.Char()
    result_json = fields.Text(required=True, copy=False)

    _sql_constraints = [
        ("idempotency_unique", "unique(idempotency_key)", "Idempotency key must be unique."),
        ("subject_unique", "unique(subject_number)", "Subject number must be unique."),
    ]


class OutlookSubjectAudit(models.Model):
    _name = "outlook.subject.audit"
    _description = "Outlook Subject API Audit"
    _order = "create_date desc"

    event = fields.Selection([("issued", "Issued"), ("replayed", "Replay"), ("rejected", "Rejected")], required=True)
    idempotency_key_hash = fields.Char(index=True)
    department_code = fields.Char()
    document_type_code = fields.Char()
    external_reference = fields.Char()
    subject_number = fields.Char()
    client_ip = fields.Char()
    details = fields.Text()

    def write(self, vals):
        raise AccessError(_("Audit records are append-only."))

    def unlink(self):
        raise AccessError(_("Audit records cannot be deleted through the application."))


class OutlookSubjectApiSettings(models.AbstractModel):
    _name = "outlook.subject.api.settings"
    _description = "Outlook Subject API Helpers"

    @api.model
    def get_token(self):
        return self.env["ir.config_parameter"].sudo().get_param("outlook_subject_sequence.api_token", "")

    @api.model
    def audit(self, event, key="", dept="", doc_type="", external="", number="", ip="", details=""):
        self.env["outlook.subject.audit"].sudo().create({
            "event": event,
            "idempotency_key_hash": hashlib.sha256(key.encode()).hexdigest() if key else "",
            "department_code": dept,
            "document_type_code": doc_type,
            "external_reference": external,
            "subject_number": number,
            "client_ip": (ip or "")[:64],
            "details": (details or "")[:2000],
        })
