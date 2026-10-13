# -*- coding: utf-8 -*-
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# Menus belonging to modules we do NOT depend on. They may or may not be
# present; hiding them from XML is not an option, because a <record> aimed at a
# missing external id makes Odoo CREATE the record instead of updating it - and
# a menu with no name violates ir_ui_menu.name NOT NULL, which aborts the whole
# install. That is exactly what broke a fresh install of this module.
OPTIONAL_MENUS = [
    'crm_iap_mine.crm_menu_lead_generation',   # "Lead Generation" (IAP credits)
]


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _sdlc_hide_optional_menus(self):
        """Switch off menus from optional modules, when they exist.

        Called from XML on install and on every upgrade, so the menus stay
        hidden if their module is installed later.
        """
        for xmlid in OPTIONAL_MENUS:
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            if menu:
                menu.sudo().active = False
                _logger.info("sdlc_CRM: hid optional menu %s", xmlid)
            else:
                _logger.info("sdlc_CRM: optional menu %s not installed, nothing "
                             "to hide", xmlid)
        return True
