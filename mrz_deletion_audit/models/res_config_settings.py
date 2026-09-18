# Copyright 2026 Muhammad Ramzan
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    mrz_deletion_audit_track_all = fields.Boolean(
        string='Track All Business Models',
        config_parameter='mrz_deletion_audit.track_all',
    )
    mrz_deletion_audit_retention_days = fields.Integer(
        string='Keep Deletion Logs For (days)',
        config_parameter='mrz_deletion_audit.retention_days',
        default=0,
    )
    mrz_deletion_audit_max_cascade = fields.Integer(
        string='Max. Cascaded Records per Deletion',
        config_parameter='mrz_deletion_audit.max_cascade_records',
        default=10000,
    )
