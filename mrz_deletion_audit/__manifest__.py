# Copyright 2026 Muhammad Ramzan
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
{
    'name': 'Deletion Audit Trail',
    'summary': 'Know who deleted which record, when, and what it contained '
               '- including records removed by cascade',
    'description': """
Deletion Audit Trail
====================
Keeps a permanent, read-only trace of every tracked record deletion:

* who deleted it, when, from where (UI, API, scheduled action, cascade), IP and browser
* a full snapshot of the deleted record's data, readable field by field
* child records removed by database cascade (e.g. order lines), linked to their parent
* attachments that were attached to the deleted record
* per-model tracking rules, or track every business model at once
* retention policy with automatic purge
""",
    'version': '18.0.1.0.0',
    'category': 'Extra Tools',
    'author': 'Muhammad Ramzan',
    'maintainer': 'Muhammad Ramzan',
    'support': 'techsramzan@gmail.com',
    'license': 'LGPL-3',
    'depends': ['base_setup'],
    'data': [
        'security/deletion_audit_security.xml',
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/deletion_audit_log_views.xml',
        'views/deletion_audit_rule_views.xml',
        'views/res_config_settings_views.xml',
        'views/deletion_audit_menus.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
}
