# Copyright 2026 Muhammad Ramzan
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import models


class IrCron(models.Model):
    _inherit = 'ir.cron'

    def _callback(self, cron_name, server_action_id, job_id):
        # Odoo 17 does not tell the job which scheduled action runs it:
        # pass it on so that deletions are logged with origin "Scheduled Action".
        return super(IrCron, self.with_context(mrz_deletion_audit_cron_id=job_id))._callback(
            cron_name, server_action_id, job_id)
