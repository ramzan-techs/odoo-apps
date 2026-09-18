# Copyright 2026 Muhammad Ramzan
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).

# Business models tracked out of the box, when their module is installed.
DEFAULT_TRACKED_MODELS = [
    'res.partner',
    'product.template',
    'product.product',
    'sale.order',
    'purchase.order',
    'account.move',
    'account.payment',
    'stock.picking',
    'mrp.production',
    'crm.lead',
    'project.project',
    'project.task',
    'hr.employee',
    'hr.expense',
    'helpdesk.ticket',
]


def post_init_hook(env):
    models = env['ir.model'].search([('model', 'in', DEFAULT_TRACKED_MODELS)])
    env['mrz.deletion.audit.rule'].create([{'model_id': model.id} for model in models])
