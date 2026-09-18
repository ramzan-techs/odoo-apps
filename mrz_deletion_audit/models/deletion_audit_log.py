# Copyright 2026 Muhammad Ramzan
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import json
import logging
import re
from contextlib import contextmanager
from datetime import timedelta
from weakref import WeakKeyDictionary

import psycopg2
from markupsafe import Markup

from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError
from odoo.http import request
from odoo.tools import SQL, html2plaintext, split_every

_logger = logging.getLogger(__name__)

SNAPSHOT_CHUNK_SIZE = 500
MAX_CASCADE_DEPTH = 5
DEFAULT_MAX_CASCADE_RECORDS = 10000
MAX_RELATED_NAMES = 50          # x2many ids resolved to names in the snapshot
MAX_TEXT_LENGTH = 10000         # longer text / html values are truncated
PURGE_BATCH_SIZE = 10000
SENSITIVE_FIELD_KEYWORDS = ('password', 'secret', 'token', 'api_key')
TEXT_FIELD_TYPES = ('char', 'text', 'html')
X2MANY_FIELD_TYPES = ('one2many', 'many2many')

# cursor -> stack of {(model, id): log} of the deletions in progress
_ACTIVE_PARENTS_BY_CURSOR = WeakKeyDictionary()

# User agents name several browsers for compatibility ("Mozilla/5.0 ...
# (KHTML, like Gecko) Chrome/147 Safari/537.36" is Chrome): the most specific
# token must be tested first.
CLIENT_PATTERNS = [
    ('Python XML-RPC', r'Python-xmlrpc/(\d+)'),
    ('Python Requests', r'python-requests/(\d+)'),
    ('Python', r'Python-urllib/(\d+)'),
    ('curl', r'curl/(\d+)'),
    ('Postman', r'PostmanRuntime/(\d+)'),
    ('Edge', r'Edg(?:e|A|iOS)?/(\d+)'),
    ('Opera', r'(?:OPR|Opera)/(\d+)'),
    ('Samsung Internet', r'SamsungBrowser/(\d+)'),
    ('Firefox', r'(?:Firefox|FxiOS)/(\d+)'),
    ('Chrome', r'(?:Chrome|CriOS)/(\d+)'),
    ('Safari', r'Version/(\d+)[\d.]* .*Safari/'),
]
PLATFORM_PATTERNS = [
    ('iOS', r'iPhone|iPad|iPod'),
    ('Android', r'Android'),
    ('ChromeOS', r'CrOS'),
    ('Windows', r'Windows'),
    ('macOS', r'Macintosh|Mac OS X'),
    ('Linux', r'Linux|X11'),
]


def describe_user_agent(user_agent):
    """Readable client name, e.g. 'Chrome 147 on Linux', from a User-Agent header."""
    if not user_agent:
        return False
    client = next(
        (f'{name} {match.group(1)}' for name, pattern in CLIENT_PATTERNS
         if (match := re.search(pattern, user_agent))),
        None,
    )
    platform = next((name for name, pattern in PLATFORM_PATTERNS if re.search(pattern, user_agent)), None)
    if client and platform:
        return f'{client} on {platform}'
    return client or platform or user_agent[:64]


ORIGINS = [
    ('ui', 'User Interface'),
    ('api', 'External API'),
    ('http', 'Website / Portal'),
    ('cron', 'Scheduled Action'),
    ('cascade', 'Cascade'),
    ('system', 'System / Script'),
]


class DeletionAuditLog(models.Model):
    _name = 'mrz.deletion.audit.log'
    _description = 'Deletion Audit Log'
    _order = 'deletion_date desc, id desc'

    name = fields.Char(string='Deleted Record', readonly=True)
    model_id = fields.Many2one('ir.model', string='Model', index=True, ondelete='set null', readonly=True)
    model_name = fields.Char(string='Technical Model', required=True, index=True, readonly=True)
    model_description = fields.Char(string='Document Type', readonly=True)
    res_id = fields.Integer(string='Record ID', index=True, readonly=True, aggregator=None)
    company_id = fields.Many2one('res.company', string='Company', index=True, ondelete='set null', readonly=True)
    user_id = fields.Many2one('res.users', string='Deleted By', index=True, ondelete='set null', readonly=True)
    session_user_id = fields.Many2one(
        'res.users', string='Logged-in User', ondelete='set null', readonly=True,
        help="The user logged in to the browser session, when it differs from the user "
             "who performed the deletion (e.g. an action running as another user).",
    )
    deletion_date = fields.Datetime(string='Deleted On', required=True, index=True, readonly=True,
                                    default=fields.Datetime.now)
    origin = fields.Selection(ORIGINS, required=True, default='system', readonly=True)
    origin_detail = fields.Char(string='Origin Details', readonly=True)
    ip_address = fields.Char(string='IP Address', readonly=True)
    user_agent = fields.Char(string='User Agent', readonly=True,
                             help="Identification sent by the browser or program, as received.")
    client_name = fields.Char(string='Browser', compute='_compute_client_name', store=True,
                              help="Browser or program used, read from the user agent.")
    request_path = fields.Char(string='Request Path', readonly=True)
    transaction_ref = fields.Char(
        string='Transaction', index=True, readonly=True,
        help="Deletions made in the same database transaction share this reference.",
    )
    parent_id = fields.Many2one('mrz.deletion.audit.log', string='Deleted Together With',
                                index=True, ondelete='cascade', readonly=True)
    child_ids = fields.One2many('mrz.deletion.audit.log', 'parent_id', string='Cascaded Records', readonly=True)
    child_count = fields.Integer(compute='_compute_child_count', string='Cascaded')
    cascade_field = fields.Char(string='Linked Through', readonly=True,
                                help="Field of this record that pointed to the deleted parent record.")
    cascade_truncated = fields.Boolean(
        string='Cascade Truncated', readonly=True,
        help="More records were removed by cascade than the configured maximum; "
             "only part of them was captured.",
    )
    snapshot = fields.Json(string='Data', readonly=True)
    attachment_info = fields.Json(string='Attachment Data', readonly=True)
    attachment_count = fields.Integer(string='Attachments', readonly=True)
    snapshot_html = fields.Html(string='Deleted Data', compute='_compute_snapshot_display', sanitize=False)
    snapshot_text = fields.Text(string='Raw Data (JSON)', compute='_compute_snapshot_display')
    attachment_html = fields.Html(string='Deleted Attachments', compute='_compute_snapshot_display',
                                  sanitize=False)
    snapshot_search = fields.Char(string='Deleted Data Contains', compute='_compute_snapshot_search',
                                  search='_search_snapshot_search')
    related_count = fields.Integer(compute='_compute_related_count', string='Same Transaction')

    @api.depends('name', 'model_description')
    def _compute_display_name(self):
        for log in self:
            log.display_name = f"{log.model_description}: {log.name}" if log.model_description else log.name

    @api.depends('user_agent')
    def _compute_client_name(self):
        for log in self:
            log.client_name = describe_user_agent(log.user_agent)

    def _compute_child_count(self):
        counts = dict(self._read_group([('parent_id', 'in', self.ids)], ['parent_id'], ['__count']))
        for log in self:
            log.child_count = counts.get(log, 0)

    def _compute_related_count(self):
        refs = [ref for ref in self.mapped('transaction_ref') if ref]
        counts = dict(self._read_group([('transaction_ref', 'in', refs)], ['transaction_ref'], ['__count']))
        for log in self:
            log.related_count = max(counts.get(log.transaction_ref, 0) - 1, 0)

    @api.depends('snapshot', 'attachment_info')
    def _compute_snapshot_display(self):
        for log in self:
            log.snapshot_html = log._render_snapshot_html()
            log.attachment_html = log._render_attachment_html()
            log.snapshot_text = (
                json.dumps(log.snapshot, indent=2, ensure_ascii=False) if log.snapshot else False
            )

    def _compute_snapshot_search(self):
        self.snapshot_search = False

    def _search_snapshot_search(self, operator, value):
        if operator != 'ilike' or not isinstance(value, str):
            raise UserError(_("Deleted data can only be searched with 'contains'."))
        escaped = value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        # Match field values and display values only, not the field names / labels.
        query = SQL(
            """
            SELECT audit_log.id
              FROM %s audit_log, jsonb_each(audit_log.snapshot) snapshot_item
             WHERE snapshot_item.value->>'value' ILIKE %s
                OR snapshot_item.value->>'display' ILIKE %s
            """,
            SQL.identifier(self._table), f'%{escaped}%', f'%{escaped}%',
        )
        return [('id', 'in', list({row[0] for row in self.env.execute_query(query)}))]

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    @api.model
    def _get_captured(self):
        """(model, id) pairs already logged in the current transaction, so that
        a record is logged once even when several unlink() calls see it. The
        cursor clears this data on commit and rollback."""
        return self.env.cr.postrollback.data.setdefault('mrz_deletion_audit.captured', set())

    @api.model
    def _get_active_parents(self):
        """{(model, id): log} of the records whose deletion is in progress."""
        active = {}
        for entry in _ACTIVE_PARENTS_BY_CURSOR.get(self.env.cr, ()):
            active.update(entry)
        return active

    @api.model
    def _has_active_parents(self):
        return bool(_ACTIVE_PARENTS_BY_CURSOR.get(self.env.cr))

    @contextmanager
    def _active_parents(self):
        """While the original unlink() of the logged records runs, records it
        deletes itself (e.g. the moves of a picking) are logged under them."""
        Rule = self.env['mrz.deletion.audit.rule']
        entry = {
            (log.model_name, log.res_id): log
            for log in self
            if (Rule._get_policy(log.model_name) or {}).get('capture_children', True)
        }
        if not entry:
            yield
            return
        stack = _ACTIVE_PARENTS_BY_CURSOR.setdefault(self.env.cr, [])
        stack.append(entry)
        try:
            yield
        finally:
            stack.pop()

    @api.model
    def _capture(self, records):
        """Log ``records`` and the records deleted together with them, before
        they are deleted, in the same transaction as the deletion."""
        captured = self._get_captured()
        records = records.browse([
            id_ for id_ in records._ids
            if isinstance(id_, int) and (records._name, id_) not in captured
        ])
        records = records.sudo().with_context(active_test=False).exists()
        if not records:
            return self.browse()
        links = self._find_parent_links(records)
        if not self.env['mrz.deletion.audit.rule']._is_tracked(records._name):
            # not tracked by itself: only logged as part of a parent's deletion
            records = records.browse(list(links))
            if not records:
                return self.browse()
        budget = {'remaining': self._get_max_cascade_records()}
        captured.update((records._name, id_) for id_ in records.ids)
        return self._capture_level(records, self._get_common_values(), budget, captured, 0, links)

    @api.model
    def _find_parent_links(self, records):
        """{res_id: (field name, parent log)} for the records pointing to a
        record whose deletion is in progress."""
        active = self._get_active_parents()
        if not active:
            return {}
        active_models = {model_name for model_name, _res_id in active}
        link_fields = [
            field for field in records._fields.values()
            if field.type == 'many2one' and field.store and field.comodel_name in active_models
        ]
        links = {}
        for record in records:
            for field in link_fields:
                parent_log = active.get((field.comodel_name, record[field.name].id))
                if parent_log:
                    links[record.id] = (field.name, parent_log)
                    break
        return links

    def _discard(self):
        """Drop the logs (and cascaded logs) of records finally not deleted."""
        captured = self._get_captured()
        try:
            logs = self
            while logs:
                captured.difference_update((log.model_name, log.res_id) for log in logs)
                logs = logs.child_ids
            self.unlink()
        except psycopg2.Error:
            pass  # the transaction is aborted anyway: the logs will be rolled back

    def _discard_not_deleted(self):
        survivors = self.browse()
        for model_name, logs in self.grouped('model_name').items():
            remaining = self.env[model_name].sudo().with_context(active_test=False).browse(
                logs.mapped('res_id')).exists()
            if remaining:
                survivors |= logs.filtered(lambda log: log.res_id in remaining._ids)
        if survivors:
            survivors._discard()

    def _capture_level(self, records, common_vals, budget, seen, depth, parent_links):
        Rule = self.env['mrz.deletion.audit.rule']
        policy = Rule._get_policy(records._name) or {}
        snapshot_fields = self._get_snapshot_fields(records, policy.get('excluded_fields', ()))

        logs = self.browse()
        for chunk_ids in split_every(SNAPSHOT_CHUNK_SIZE, records.ids):
            chunk = records.browse(chunk_ids)
            logs |= self.create(self._prepare_log_vals(chunk, snapshot_fields, common_vals, parent_links))

        if not policy.get('capture_children', True) or depth >= MAX_CASCADE_DEPTH:
            return logs

        logs_by_res_id = {log.res_id: log for log in logs}
        for child_model, field_name in self._get_cascade_fields(records._name):
            if not Rule._is_tracked_as_child(child_model):
                continue
            if budget['remaining'] <= 0:
                logs.cascade_truncated = True
                break
            children = self.env[child_model].sudo().with_context(active_test=False).search(
                [(field_name, 'in', records.ids)], limit=budget['remaining'] + 1,
            )
            children = children.filtered(lambda child: (child_model, child.id) not in seen)
            if len(children) > budget['remaining']:
                logs.cascade_truncated = True
                _logger.warning(
                    "Deletion audit: more than %s %s records cascaded, the rest is not captured.",
                    budget['remaining'], child_model,
                )
                children = children[:budget['remaining']]
            if not children:
                continue
            budget['remaining'] -= len(children)
            seen.update((child_model, id_) for id_ in children.ids)
            child_links = {child.id: (field_name, logs_by_res_id.get(child[field_name].id)) for child in children}
            logs |= self._capture_level(children, common_vals, budget, seen, depth + 1, child_links)
        return logs

    @api.model
    def _get_max_cascade_records(self):
        value = self.env['ir.config_parameter'].sudo().get_param('mrz_deletion_audit.max_cascade_records')
        try:
            return max(int(value), 0) if value else DEFAULT_MAX_CASCADE_RECORDS
        except ValueError:
            return DEFAULT_MAX_CASCADE_RECORDS

    @api.model
    @tools.ormcache('model_name')
    def _get_cascade_fields(self, model_name):
        """Many2one fields of other models deleted by the database (ON DELETE
        CASCADE) when a ``model_name`` record is deleted, as (model, field)."""
        result = []
        for child_name, child_model in self.env.registry.items():
            if child_model._abstract or child_model._transient or not child_model._auto:
                continue
            for field in child_model._fields.values():
                if (
                    field.type == 'many2one'
                    and field.comodel_name == model_name
                    and field.ondelete == 'cascade'
                    and field.store
                    and not field.inherited
                    and not field.company_dependent
                ):
                    result.append((child_name, field.name))
        return tuple(result)

    @api.model
    def _get_common_values(self):
        """Values shared by every log of one deletion: who, when, from where."""
        vals = {
            'user_id': self.env.uid,
            'deletion_date': fields.Datetime.now(),
            'origin': 'system',
            'transaction_ref': str(self.env.execute_query(SQL("SELECT txid_current()"))[0][0]),
        }
        cron_id = self.env.context.get('cron_id')
        if cron_id:
            vals['origin'] = 'cron'
            vals['origin_detail'] = self.env['ir.cron'].sudo().browse(cron_id).exists().cron_name or False
        httprequest = getattr(request, 'httprequest', None) if request else None
        if httprequest is not None:
            path = httprequest.path or ''
            vals.update({
                'ip_address': httprequest.remote_addr,
                'user_agent': (httprequest.headers.get('User-Agent') or '')[:512] or False,
                'request_path': path[:1024],
            })
            if not cron_id:
                vals['origin'] = self._get_request_origin(path)
            session = getattr(request, 'session', None)
            session_uid = session and session.uid
            if session_uid and session_uid != self.env.uid:
                vals['session_user_id'] = session_uid
        return vals

    @api.model
    def _get_request_origin(self, path):
        if path.startswith(('/xmlrpc', '/jsonrpc', '/json/')):
            return 'api'
        if path.startswith('/web/'):
            return 'ui'
        return 'http'

    @api.model
    def _get_snapshot_fields(self, records, excluded_fields):
        return [
            field for name, field in records._fields.items()
            if field.store
            and name != 'id'
            and name not in excluded_fields
            and not any(keyword in name for keyword in SENSITIVE_FIELD_KEYWORDS)
        ]

    @api.model
    def _prepare_log_vals(self, records, snapshot_fields, common_vals, parent_links):
        model_name = records._name
        ir_model = self.env['ir.model']._get(model_name)
        snapshots, names = self._read_snapshots(records, snapshot_fields)
        attachments = self._read_attachments(records)
        company_field = records._fields.get('company_id')
        has_company = company_field and company_field.type == 'many2one' and company_field.comodel_name == 'res.company'

        vals_list = []
        for record in records:
            record_attachments = attachments.get(record.id, [])
            vals = dict(
                common_vals,
                name=names.get(record.id) or f'{model_name},{record.id}',
                model_id=ir_model.id,
                model_name=model_name,
                model_description=ir_model.name,
                res_id=record.id,
                company_id=record.company_id.id if has_company else False,
                snapshot=snapshots.get(record.id) or False,
                attachment_info=record_attachments or False,
                attachment_count=len(record_attachments),
            )
            if record.id in parent_links:
                parent_field, parent_log = parent_links[record.id]
                vals.update({
                    'parent_id': parent_log.id if parent_log else False,
                    'cascade_field': parent_field,
                    'origin': 'cascade',
                    'origin_detail': parent_log.display_name if parent_log else False,
                })
            vals_list.append(vals)
        return vals_list

    @api.model
    def _read_snapshots(self, records, snapshot_fields):
        """Return ({res_id: snapshot}, {res_id: display_name}). Never raises:
        a snapshot failure must not block the deletion itself."""
        try:
            with self.env.cr.savepoint():
                rows = records.with_context(bin_size=True).read([field.name for field in snapshot_fields])
                names = {record.id: record.display_name for record in records}
                related_names = self._read_related_names(rows, snapshot_fields)
        except Exception as error:  # noqa: BLE001
            _logger.warning("Deletion audit: could not snapshot %s%s", records._name, records.ids, exc_info=True)
            error_snapshot = {'__error__': str(error)}
            return {id_: error_snapshot for id_ in records.ids}, {}

        selections = {
            field.name: dict(field._description_selection(self.env))
            for field in snapshot_fields if field.type == 'selection'
        }
        snapshots = {}
        for row in rows:
            snapshot = {}
            for field in snapshot_fields:
                item = self._snapshot_item(field, row.get(field.name), selections, related_names)
                if item:
                    snapshot[field.name] = item
            snapshots[row['id']] = snapshot
        return snapshots, names

    @api.model
    def _read_related_names(self, rows, snapshot_fields):
        """Display names of x2many values, read in one batch per comodel."""
        ids_by_model = {}
        for field in snapshot_fields:
            if field.type in X2MANY_FIELD_TYPES:
                ids = ids_by_model.setdefault(field.comodel_name, set())
                for row in rows:
                    ids.update((row.get(field.name) or [])[:MAX_RELATED_NAMES])
        return {
            model_name: {
                record.id: record.display_name
                for record in self.env[model_name].sudo().with_context(active_test=False).browse(ids).exists()
            }
            for model_name, ids in ids_by_model.items() if ids
        }

    @api.model
    def _snapshot_item(self, field, value, selections, related_names):
        """Serialize one field value; return None for empty values."""
        # 0 / 0.0 are meaningful values: compare by identity, not equality
        if field.type != 'boolean' and (value is None or value is False or value in ('', [])):
            return None
        item = {'label': field.string, 'type': field.type}
        if field.type == 'many2one':
            item.update(value=value[0], display=value[1], model=field.comodel_name)
        elif field.type in X2MANY_FIELD_TYPES:
            names = related_names.get(field.comodel_name, {})
            item.update(
                value=list(value),
                display=[names.get(id_) or f'#{id_}' for id_ in value[:MAX_RELATED_NAMES]],
                model=field.comodel_name,
            )
        elif field.type == 'selection':
            item.update(value=value, display=selections[field.name].get(value, value))
        elif field.type == 'date':
            item['value'] = fields.Date.to_string(value)
        elif field.type == 'datetime':
            item['value'] = fields.Datetime.to_string(value)
        elif field.type in TEXT_FIELD_TYPES:
            value = str(value)
            if len(value) > MAX_TEXT_LENGTH:
                value = value[:MAX_TEXT_LENGTH]
                item['truncated'] = True
            item['value'] = value
        elif field.type == 'binary':
            item['value'] = value if isinstance(value, str) else _('(binary data)')
        else:
            # numbers, json, properties, references...: keep whatever is JSON-serializable
            item['value'] = json.loads(json.dumps(value, default=str))
        return item

    @api.model
    def _read_attachments(self, records):
        rows = self.env.execute_query_dict(SQL(
            """
            SELECT id, res_id, name, mimetype, file_size
              FROM ir_attachment
             WHERE res_model = %s AND res_id IN %s AND res_field IS NULL
          ORDER BY id
            """,
            records._name, tuple(records.ids),
        ))
        attachments = {}
        for row in rows:
            attachments.setdefault(row.pop('res_id'), []).append(row)
        return attachments

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_snapshot_html(self):
        self.ensure_one()
        snapshot = self.snapshot or {}
        if snapshot.get('__error__'):
            return Markup('<div class="alert alert-warning mb-0">%s<br/><code>%s</code></div>') % (
                _("The data of this record could not be captured."), snapshot['__error__'],
            )
        if not snapshot:
            return Markup('<p class="text-muted">%s</p>') % _("No data captured.")
        rows = Markup().join(
            Markup('<tr><td class="fw-bold text-nowrap" title="%s">%s</td>'
                   '<td style="white-space: pre-wrap;">%s</td></tr>') % (
                name, item.get('label') or name, self._format_snapshot_value(item),
            )
            for name, item in snapshot.items()
        )
        return Markup(
            '<table class="table table-sm table-striped mb-0">'
            '<thead><tr><th class="w-25">%s</th><th>%s</th></tr></thead><tbody>%s</tbody></table>'
        ) % (_("Field"), _("Value"), rows)

    def _format_snapshot_value(self, item):
        field_type, value, display = item.get('type'), item.get('value'), item.get('display')
        if field_type == 'boolean':
            return _("Yes") if value else _("No")
        if field_type == 'many2one':
            return str(display or f'#{value}')
        if field_type in X2MANY_FIELD_TYPES:
            text = ', '.join(str(name) for name in display or [])
            if len(value) > len(display or []):
                text += ' ' + _("(and %(count)s more)", count=len(value) - len(display or []))
            return text
        if field_type == 'selection':
            return str(display or value)
        if field_type == 'date':
            return tools.format_date(self.env, value)
        if field_type == 'datetime':
            return tools.format_datetime(self.env, value)
        if field_type == 'html':
            value = html2plaintext(value)
        elif not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        return value + (' …' if item.get('truncated') else '')

    def _render_attachment_html(self):
        self.ensure_one()
        if not self.attachment_info:
            return False
        rows = Markup().join(
            Markup('<tr><td>%s</td><td>%s</td><td class="text-end">%s</td></tr>') % (
                attachment.get('name') or '', attachment.get('mimetype') or '',
                tools.human_size(attachment.get('file_size') or 0) or '',
            )
            for attachment in self.attachment_info
        )
        return Markup(
            '<table class="table table-sm table-striped mb-0"><thead><tr><th>%s</th><th>%s</th>'
            '<th class="text-end">%s</th></tr></thead><tbody>%s</tbody></table>'
        ) % (_("File"), _("Type"), _("Size"), rows)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    @api.model
    def _action_open_logs(self, domain, name):
        action = self.env['ir.actions.actions']._for_xml_id('mrz_deletion_audit.action_mrz_deletion_audit_log')
        action.update(name=name, domain=domain, context={'create': False})
        return action

    def action_view_children(self):
        self.ensure_one()
        return self._action_open_logs([('parent_id', '=', self.id)], _("Cascaded Records"))

    def action_view_same_transaction(self):
        self.ensure_one()
        return self._action_open_logs(
            [('transaction_ref', '=', self.transaction_ref), ('id', '!=', self.id)],
            _("Deleted in the Same Transaction"),
        )

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    @api.model
    def _cron_purge_old_logs(self):
        value = self.env['ir.config_parameter'].sudo().get_param('mrz_deletion_audit.retention_days')
        days = int(value) if value and value.isdigit() else 0
        if days <= 0:
            return
        domain = [
            ('deletion_date', '<', fields.Datetime.now() - timedelta(days=days)),
            ('parent_id', '=', False),
        ]
        logs = self.sudo().search(domain, limit=PURGE_BATCH_SIZE)
        remaining = self.sudo().search_count(domain) - len(logs)
        logs.unlink()  # cascaded children are removed by the database
        self.env['ir.cron']._notify_progress(done=len(logs), remaining=remaining)
