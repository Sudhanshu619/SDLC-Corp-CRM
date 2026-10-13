# -*- coding: utf-8 -*-
"""Drop the views that referenced the removed per-campaign stage fields.

Odoo removes records that disappeared from a module's XML, but it does that at
the END of the update - after it has validated the views it just loaded. The
obsolete inherited view is still in the database at that point, so it gets
applied on top of its parent and the parent fails to validate with

    Field "campaign_ids" does not exist in model "crm.stage"

Deleting it up front, before the module's XML is parsed, avoids that.
"""
import logging

_logger = logging.getLogger(__name__)

OBSOLETE_VIEWS = [
    'crm_stage_form_campaigns_sdlc',   # added "Campaigns" to the stage form
]


def migrate(cr, version):
    if not version:
        return
    for name in OBSOLETE_VIEWS:
        cr.execute("""
            DELETE FROM ir_ui_view
             WHERE id IN (SELECT res_id FROM ir_model_data
                           WHERE module = 'sdlc_CRM'
                             AND model = 'ir.ui.view'
                             AND name = %s)
        """, (name,))
        removed = cr.rowcount
        cr.execute("""
            DELETE FROM ir_model_data
             WHERE module = 'sdlc_CRM' AND model = 'ir.ui.view' AND name = %s
        """, (name,))
        _logger.info("sdlc_CRM: removed obsolete view %s (%d row(s))", name, removed)
