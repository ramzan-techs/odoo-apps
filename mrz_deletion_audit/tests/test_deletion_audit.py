# Copyright 2026 Muhammad Ramzan
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, RedirectWarning
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.mrz_deletion_audit.models.deletion_audit_log import describe_user_agent


@tagged('post_install', '-at_install')
class TestDeletionAudit(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Log = cls.env['mrz.deletion.audit.log']
        cls.Rule = cls.env['mrz.deletion.audit.rule']
        cls.env['ir.config_parameter'].set_param('mrz_deletion_audit.track_all', False)
        cls.partner_rule = cls._set_rule('res.partner')

    @classmethod
    def _set_rule(cls, model_name, **vals):
        rule = cls.Rule.with_context(active_test=False).search([('model_name', '=', model_name)])
        vals = {'mode': 'track', 'active': True, 'capture_children': True, **vals}
        if rule:
            rule.write(vals)
        else:
            rule = cls.Rule.create({'model_id': cls.env['ir.model']._get_id(model_name), **vals})
        return rule

    def _get_log(self, model_name, res_id):
        return self.Log.search([('model_name', '=', model_name), ('res_id', '=', res_id)])

    def test_deletion_logs_who_when_and_data(self):
        tag = self.env['res.partner.category'].create({'name': 'VIP Customer'})
        country = self.env.ref('base.be')
        partner = self.env['res.partner'].create({
            'name': 'Deleted Partner',
            'email': 'deleted.partner@example.com',
            'country_id': country.id,
            'category_id': [Command.set(tag.ids)],
        })
        partner_id = partner.id

        partner.unlink()

        log = self._get_log('res.partner', partner_id)
        self.assertEqual(len(log), 1)
        self.assertEqual(log.name, 'Deleted Partner')
        self.assertEqual(log.user_id, self.env.user)
        self.assertEqual(log.origin, 'system')
        self.assertEqual(log.model_id.model, 'res.partner')
        self.assertTrue(log.transaction_ref)
        snapshot = log.snapshot
        self.assertEqual(snapshot['email']['value'], 'deleted.partner@example.com')
        self.assertEqual(snapshot['country_id']['value'], country.id)
        self.assertEqual(snapshot['country_id']['display'], country.display_name)
        self.assertEqual(snapshot['category_id']['display'], ['VIP Customer'])
        self.assertEqual(snapshot['type']['display'], 'Contact')
        self.assertTrue(snapshot['active']['value'])
        self.assertIn('deleted.partner@example.com', log.snapshot_html)
        self.assertIn('VIP Customer', log.snapshot_html)

    def test_describe_user_agent(self):
        cases = {
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/147.0.0.0 Safari/537.36': 'Chrome 147 on Linux',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0': 'Edge 147 on Windows',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0':
                'Firefox 140 on Windows',
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 '
            '(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1': 'Safari 17 on iOS',
            'Mozilla/5.0 (Linux; Android 14; K) AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/147.0.0.0 Mobile Safari/537.36': 'Chrome 147 on Android',
            'Python-xmlrpc/3.10': 'Python XML-RPC 3',
            '': False,
        }
        for user_agent, expected in cases.items():
            self.assertEqual(describe_user_agent(user_agent), expected, user_agent)

    def test_untracked_model_is_not_logged(self):
        tag = self.env['res.partner.category'].create({'name': 'Untracked'})
        tag_id = tag.id
        tag.unlink()
        self.assertFalse(self._get_log('res.partner.category', tag_id))

    def test_cascaded_records_are_logged_under_their_parent(self):
        partner = self.env['res.partner'].create({'name': 'Company With Bank', 'is_company': True})
        bank = self.env['res.partner.bank'].create({'acc_number': 'BE71096123456769', 'partner_id': partner.id})
        partner_id, bank_id = partner.id, bank.id

        partner.unlink()

        parent_log = self._get_log('res.partner', partner_id)
        child_log = self._get_log('res.partner.bank', bank_id)
        self.assertEqual(child_log.parent_id, parent_log)
        self.assertEqual(child_log.origin, 'cascade')
        self.assertEqual(child_log.cascade_field, 'partner_id')
        self.assertEqual(child_log.transaction_ref, parent_log.transaction_ref)
        self.assertEqual(child_log.snapshot['acc_number']['value'], 'BE71096123456769')
        self.assertEqual(parent_log.child_count, 1)

    def test_cascade_capture_can_be_disabled(self):
        self.partner_rule.capture_children = False
        partner = self.env['res.partner'].create({'name': 'No Cascade', 'is_company': True})
        bank = self.env['res.partner.bank'].create({'acc_number': 'NL91ABNA0417164300', 'partner_id': partner.id})
        bank_id = bank.id
        partner.unlink()
        self.assertFalse(self._get_log('res.partner.bank', bank_id))

    @mute_logger('odoo.addons.mrz_deletion_audit.models.deletion_audit_log')
    def test_cascade_is_capped(self):
        self.env['ir.config_parameter'].set_param('mrz_deletion_audit.max_cascade_records', 1)
        partner = self.env['res.partner'].create({'name': 'Many Banks', 'is_company': True})
        self.env['res.partner.bank'].create([
            {'acc_number': number, 'partner_id': partner.id}
            for number in ('DE89370400440532013000', 'FR1420041010050500013M02606')
        ])
        partner_id = partner.id
        partner.unlink()
        parent_log = self._get_log('res.partner', partner_id)
        self.assertTrue(parent_log.cascade_truncated)
        self.assertEqual(parent_log.child_count, 1)

    def test_unlink_is_wrapped_and_self_heals(self):
        partner_class = type(self.env['res.partner'])
        self.assertTrue(getattr(partner_class.unlink, 'mrz_deletion_audit', False))

        # another module (e.g. base_automation) may remove class-level patches
        wrapper = partner_class.unlink

        def restore():
            if not getattr(partner_class.unlink, 'mrz_deletion_audit', False):
                partner_class.unlink = wrapper

        self.addCleanup(restore)
        delattr(partner_class, 'unlink')
        partner = self.env['res.partner'].create({'name': 'Unpatched'})
        partner_id = partner.id
        partner.unlink()
        self.assertTrue(self._get_log('res.partner', partner_id), "the fallback hook still logs")
        self.assertTrue(getattr(partner_class.unlink, 'mrz_deletion_audit', False), "the wrapper is restored")

    def test_archive_instead_of_delete_is_not_logged(self):
        # res.partner.bank overrides unlink() to archive the account
        self._set_rule('res.partner.bank')
        partner = self.env['res.partner'].create({'name': 'Bank Holder', 'is_company': True})
        bank = self.env['res.partner.bank'].create({'acc_number': 'GB29NWBK60161331926819', 'partner_id': partner.id})
        bank.unlink()
        self.assertTrue(bank.exists())
        self.assertFalse(bank.active)
        self.assertFalse(self._get_log('res.partner.bank', bank.id))

    def test_records_deleted_during_parent_deletion_are_linked(self):
        # simulate a parent unlink() deleting related records itself, like a
        # picking deleting its moves before being deleted
        company = self.env['res.partner'].create({'name': 'Parent Co', 'is_company': True})
        contact = self.env['res.partner'].create({'name': 'Child Contact', 'parent_id': company.id})
        tag = self.env['res.partner.category'].create({'name': 'Unrelated Tag'})
        contact_id, tag_id = contact.id, tag.id

        company_log = self.Log._capture(company)
        with company_log._active_parents():
            contact.unlink()
            tag.unlink()

        contact_log = self._get_log('res.partner', contact_id)
        self.assertEqual(contact_log.parent_id, company_log)
        self.assertEqual(contact_log.cascade_field, 'parent_id')
        self.assertEqual(contact_log.origin, 'cascade')
        # untracked and unrelated to the parent: not logged
        self.assertFalse(self._get_log('res.partner.category', tag_id))

    def test_failed_deletion_leaves_no_log(self):
        user = new_test_user(self.env, login='mrz_linked_user')
        partner = user.partner_id
        # the admin can manage users, so deleting a user's contact raises a RedirectWarning
        admin = self.env.ref('base.user_admin')
        with self.assertRaises(RedirectWarning), self.env.cr.savepoint():
            partner.with_user(admin).unlink()
        self.assertTrue(partner.exists())
        self.assertFalse(self._get_log('res.partner', partner.id))

    def test_track_all_and_ignore_rules(self):
        self.env['ir.config_parameter'].set_param('mrz_deletion_audit.track_all', True)
        tag = self.env['res.partner.category'].create({'name': 'Tracked Tag'})
        tag_id = tag.id
        tag.unlink()
        self.assertTrue(self._get_log('res.partner.category', tag_id))

        # technical models stay excluded in "track all" mode
        param = self.env['ir.config_parameter'].create({'key': 'mrz.test.param', 'value': '1'})
        param_id = param.id
        param.unlink()
        self.assertFalse(self._get_log('ir.config_parameter', param_id))

        self._set_rule('res.partner.category', mode='ignore')
        tag = self.env['res.partner.category'].create({'name': 'Ignored Tag'})
        tag_id = tag.id
        tag.unlink()
        self.assertFalse(self._get_log('res.partner.category', tag_id))

    def test_bulk_deletion(self):
        partners = self.env['res.partner'].create([{'name': f'Bulk Partner {i}'} for i in range(600)])
        partner_ids = partners.ids
        partners.unlink()
        logs = self.Log.search([('model_name', '=', 'res.partner'), ('res_id', 'in', partner_ids)])
        self.assertEqual(len(logs), 600)
        self.assertEqual(len(set(logs.mapped('transaction_ref'))), 1)

    def test_skip_context(self):
        partner = self.env['res.partner'].create({'name': 'Skipped'})
        partner_id = partner.id
        partner.with_context(mrz_deletion_audit_skip=True).unlink()
        self.assertFalse(self._get_log('res.partner', partner_id))

    def test_search_in_deleted_data(self):
        partner = self.env['res.partner'].create({'name': 'Searchable', 'email': 'needle.4521@example.com'})
        partner_id = partner.id
        partner.unlink()
        logs = self.Log.search([('snapshot_search', 'ilike', 'NEEDLE.4521')])
        self.assertEqual(logs.res_id, partner_id)
        # field names and labels are not searched, only values
        self.assertFalse(self.Log.search([('snapshot_search', 'ilike', 'country_id')]))

    def test_access_rights(self):
        partner_log = self.Log.create({
            'name': 'Partner', 'model_name': 'res.partner', 'model_id': self.env['ir.model']._get_id('res.partner'),
        })
        cron_log = self.Log.create({
            'name': 'Cron', 'model_name': 'ir.cron', 'model_id': self.env['ir.model']._get_id('ir.cron'),
        })
        logs = partner_log | cron_log
        auditor = new_test_user(
            self.env, login='mrz_auditor',
            groups='base.group_user,mrz_deletion_audit.group_mrz_deletion_audit_user',
        )
        manager = new_test_user(
            self.env, login='mrz_audit_manager',
            groups='base.group_user,mrz_deletion_audit.group_mrz_deletion_audit_manager',
        )
        # auditors only see the logs of models they can read
        self.assertEqual(self.Log.with_user(auditor).search([('id', 'in', logs.ids)]), partner_log)
        self.assertEqual(self.Log.with_user(manager).search([('id', 'in', logs.ids)]), logs)
        # nobody can alter or delete a log
        with self.assertRaises(AccessError):
            partner_log.with_user(manager).write({'name': 'Tampered'})
        with self.assertRaises(AccessError):
            partner_log.with_user(manager).unlink()

    def test_purge_old_logs(self):
        partners = self.env['res.partner'].create([{'name': 'Old'}, {'name': 'Recent'}])
        old_id, recent_id = partners.ids
        partners.unlink()
        old_log = self._get_log('res.partner', old_id)
        recent_log = self._get_log('res.partner', recent_id)
        old_log.deletion_date = fields.Datetime.now() - timedelta(days=40)

        self.env['ir.config_parameter'].set_param('mrz_deletion_audit.retention_days', 30)
        self.Log._cron_purge_old_logs()

        self.assertFalse(old_log.exists())
        self.assertTrue(recent_log.exists())
