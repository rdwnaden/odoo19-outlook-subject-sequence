# Outlook Subject Sequence — Odoo 19 Community

## Install
Copy this folder into the host directory mounted at `/mnt/extra-addons` (example: `~/odoo19/addons/outlook_subject_sequence`). Update the Apps list and install **Outlook Subject Sequence**, or run:

```bash
docker exec -it odoo19 odoo -d YOUR_DATABASE -i outlook_subject_sequence --stop-after-init
docker restart odoo19
```

If Odoo does not find the module, specify the container's actual `--addons-path`, including `/mnt/extra-addons`. For updates use `-u outlook_subject_sequence`.

## Configure
As administrator, open **Outlook Subject Sequence**. Create departments (e.g. Operations / `OPS`) and document types (e.g. Outgoing Letter / `OUT`). Create one Sequence Configuration per pair and select **Outlook Subject Central Sequence**. Leave the linked `ir.sequence` prefix empty. Example generated value: `2026/OPS/OUT/00001`. All pairs share a central counter; create separate `ir.sequence` records if independent counters are required.

## API token
Generate a secret with `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`. In Settings → Technical → System Parameters set key `outlook_subject_sequence.api_token` to that value. Do not put the token in source control or browser-side code. Expose the endpoint only over HTTPS; use a reverse-proxy IP allowlist/rate limit where possible.

## API
`POST /api/outlook/subject-sequence`

Headers:
```http
Authorization: Bearer YOUR_LONG_RANDOM_TOKEN
Content-Type: application/json
```

Body:
```json
{
  "department_code": "OPS",
  "document_type_code": "OUT",
  "idempotency_key": "outlook-item-12345",
  "external_reference": "mailbox/message-id",
  "requested_by": "user@company.example"
}
```

A successful first request returns HTTP 201 and `subject_number`, e.g. `2026/OPS/OUT/00001`. Retry with the same idempotency key and same payload to receive the same number (HTTP 200). Reusing the key with a different payload returns HTTP 409. Persist the key on the Outlook integration side before calling, and reuse it on retries.

Health endpoint: `GET /api/outlook/subject-sequence/health`.

## Security notes
The controller validates bearer token using constant-time comparison, requires JSON POST, limits body size, validates input, and only uses sudo after authentication. Idempotency and subject numbers have database uniqueness constraints. Audit records store a hash of the key and are append-only through the Odoo ORM. Database administrators can still alter database records; forward logs externally for stronger tamper evidence. This module allocates numbers only: a separate Outlook add-in, Power Automate flow, or integration service must call the API and place the returned number in the subject.
