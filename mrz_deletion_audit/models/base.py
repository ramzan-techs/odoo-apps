# Copyright 2026 Muhammad Ramzan
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import logging

from odoo import api, models
from odoo.addons.base.models.ir_model import MODULE_UNINSTALL_FLAG

from .deletion_audit_rule import NEVER_TRACKED_MODELS

_logger = logging.getLogger(__name__)


def _make_unlink_wrapper():
    """Build the ``unlink`` installed on top of each model class.

    Model overrides often modify or delete child records before calling
    ``super().unlink()`` (e.g. an invoice deletes its lines, a picking cancels
    its moves), so the snapshot must be taken before any of them runs.
    """
    def unlink(self, **kwargs):
        if not (self and self._mrz_deletion_audit_enabled()):
            return unlink.origin(self, **kwargs)
        return self._mrz_deletion_audit_unlink(unlink.origin, **kwargs)

    unlink.mrz_deletion_audit = True
    return unlink


class Base(models.AbstractModel):
    _inherit = 'base'

    def unlink(self):
        # Fallback, right before the SQL DELETE: captures the records the
        # top-level wrapper did not (e.g. when another module removed it).
        if self and self._mrz_deletion_audit_enabled():
            logs = self.env['mrz.deletion.audit.log'].sudo()._capture(self)
            if logs and not getattr(type(self).unlink, 'mrz_deletion_audit', False):
                self._mrz_deletion_audit_patch()
        return super().unlink()

    def _mrz_deletion_audit_unlink(self, origin, **kwargs):
        Log = self.env['mrz.deletion.audit.log'].sudo()
        logs = Log._capture(self)
        try:
            with logs._active_parents():
                result = origin(self, **kwargs)
        except Exception:
            logs._discard()
            raise
        # some models override unlink to archive instead of deleting
        logs._discard_not_deleted()
        return result

    def _mrz_deletion_audit_enabled(self):
        context = self.env.context
        if (
            self._transient
            or context.get(MODULE_UNINSTALL_FLAG)
            or context.get('mrz_deletion_audit_skip')
            or not self.env.registry.ready
        ):
            return False
        Rule = self.env['mrz.deletion.audit.rule']
        return Rule._is_tracked(self._name) or (
            # deleted while a tracked parent is being deleted
            self.env['mrz.deletion.audit.log']._has_active_parents()
            and Rule._is_tracked_as_child(self._name)
        )

    # ------------------------------------------------------------------
    # Patching, same mechanism as base_automation: the registry calls
    # _register_hook() on every model once it is loaded, and
    # _unregister_hook() before rebuilding the models.
    # ------------------------------------------------------------------

    def _register_hook(self):
        super()._register_hook()
        self._mrz_deletion_audit_patch()

    def _unregister_hook(self):
        super()._unregister_hook()
        model_class = self.env.registry.get(self._name)
        if model_class is not None and 'unlink' in vars(model_class):
            delattr(model_class, 'unlink')

    @api.model
    def _mrz_deletion_audit_patch(self):
        if self._abstract or self._transient or not self._auto or self._name in NEVER_TRACKED_MODELS:
            return
        model_class = self.env.registry[self._name]
        if getattr(model_class.unlink, 'mrz_deletion_audit', False):
            return
        wrapper = _make_unlink_wrapper()
        wrapper.origin = model_class.unlink
        model_class.unlink = wrapper
