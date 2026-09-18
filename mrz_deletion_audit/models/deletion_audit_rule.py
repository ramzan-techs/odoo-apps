# Copyright 2026 Muhammad Ramzan
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import api, fields, models, tools

# Our own models are never tracked, whatever the configuration.
NEVER_TRACKED_MODELS = frozenset({'mrz.deletion.audit.log', 'mrz.deletion.audit.rule'})

# Technical models skipped in "track all" mode and when removed by cascade,
# unless a rule explicitly tracks them.
TECHNICAL_MODEL_PREFIXES = (
    'ir.',
    'base.',
    'base_import.',
    'bus.',
    'mail.',
    'discuss.',
    'res.users.',
    'res.device',
    'web_tour.',
    'web_editor.',
    'html_editor.',
    'iap.',
    'auth_totp.',
    'spreadsheet.',
    'digest.',
    'sms.',
    'snailmail.',
    'onboarding.',
    'website.visitor',
    'website.track',
    'account.partial.reconcile',
    'account.full.reconcile',
)


class DeletionAuditRule(models.Model):
    _name = 'mrz.deletion.audit.rule'
    _description = 'Deletion Audit Rule'
    _order = 'model_name'
    _rec_name = 'model_id'

    model_id = fields.Many2one(
        'ir.model', string='Model', required=True, index=True, ondelete='cascade',
        domain=[('transient', '=', False), ('model', 'not in', list(NEVER_TRACKED_MODELS))],
    )
    model_name = fields.Char(related='model_id.model', string='Technical Name', store=True)
    active = fields.Boolean(default=True)
    mode = fields.Selection(
        [('track', 'Track'), ('ignore', 'Ignore')], default='track', required=True,
        help="Track: log every deletion of this model.\n"
             "Ignore: never log them, even when 'Track All Business Models' is enabled "
             "or when they are removed by cascade.",
    )
    capture_children = fields.Boolean(
        string='Capture Cascaded Records', default=True,
        help="Also snapshot the child records the database deletes automatically together "
             "with this record, e.g. the lines of an order.",
    )
    excluded_field_ids = fields.Many2many(
        'ir.model.fields', string='Excluded Fields',
        domain="[('model_id', '=', model_id)]",
        help="Fields never stored in the snapshot, e.g. sensitive data.",
    )
    log_count = fields.Integer(compute='_compute_log_count', string='Deletions')

    _sql_constraints = [
        ('model_uniq', 'unique(model_id)', 'There is already a deletion audit rule for this model.'),
    ]

    def _compute_log_count(self):
        counts = dict(self.env['mrz.deletion.audit.log']._read_group(
            [('model_name', 'in', self.mapped('model_name'))], ['model_name'], ['__count'],
        ))
        for rule in self:
            rule.log_count = counts.get(rule.model_name, 0)

    @api.model_create_multi
    def create(self, vals_list):
        rules = super().create(vals_list)
        self.env.registry.clear_cache()
        return rules

    def write(self, vals):
        res = super().write(vals)
        self.env.registry.clear_cache()
        return res

    def unlink(self):
        res = super().unlink()
        self.env.registry.clear_cache()
        return res

    def action_view_logs(self):
        self.ensure_one()
        return self.env['mrz.deletion.audit.log']._action_open_logs(
            [('model_name', '=', self.model_name)], self.model_id.name,
        )

    # ------------------------------------------------------------------
    # Tracking policy (called on every unlink, so it must stay cheap)
    # ------------------------------------------------------------------

    @api.model
    @tools.ormcache()
    def _get_policies(self):
        return tools.frozendict({
            rule.model_name: tools.frozendict(
                mode=rule.mode,
                capture_children=rule.capture_children,
                excluded_fields=frozenset(rule.excluded_field_ids.mapped('name')),
            )
            for rule in self.sudo().search([])
        })

    @api.model
    def _get_policy(self, model_name):
        return self._get_policies().get(model_name)

    @api.model
    def _is_technical_model(self, model_name):
        return model_name.startswith(TECHNICAL_MODEL_PREFIXES)

    @api.model
    def _is_tracked(self, model_name):
        """Whether a direct deletion of ``model_name`` records must be logged."""
        if model_name in NEVER_TRACKED_MODELS:
            return False
        policy = self._get_policy(model_name)
        if policy:
            return policy['mode'] == 'track'
        track_all = self.env['ir.config_parameter'].sudo().get_param('mrz_deletion_audit.track_all')
        return tools.str2bool(track_all or '0', False) and not self._is_technical_model(model_name)

    @api.model
    def _is_tracked_as_child(self, model_name):
        """Whether ``model_name`` records removed by a database cascade must be logged."""
        if model_name in NEVER_TRACKED_MODELS:
            return False
        policy = self._get_policy(model_name)
        if policy:
            return policy['mode'] == 'track'
        return not self._is_technical_model(model_name)
