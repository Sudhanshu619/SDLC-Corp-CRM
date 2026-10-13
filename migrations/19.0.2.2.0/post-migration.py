# -*- coding: utf-8 -*-
"""Repair the stage cycle, and stop shipping it as XML.

Until 19.0.2.1.0 the stage cycle lived in ``data/crm_stage_data.xml`` and
<record>ed Odoo's own ``crm.stage_lead*`` ids. Deleting the pipeline that owned
those stages deleted them too, after which every upgrade died with
"Cannot update missing record 'crm.stage_lead1'".

The cycle is now built in code (``crm.team._ensure_default_pipeline``), which
copes with stages or the pipeline being missing. This rebuilds whatever is gone.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['crm.team']._ensure_default_pipeline()
