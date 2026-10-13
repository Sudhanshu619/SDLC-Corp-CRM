# -*- coding: utf-8 -*-
"""Complete the stage cycle and fill in the stage task configuration.

Two faults, both only visible on a database that was INSTALLED rather than
migrated up:

* the house cycle was created only when no stage existed at all. Odoo's crm
  module already ships New / Qualified / Proposition / Won, so the condition
  never fired and "Negotiation" was simply missing.
* the stage task defaults were applied with a ``team_ids``-only domain, while
  the house stages are deliberately unscoped - so the search matched nothing
  and no stage ever scheduled its task.

``_ensure_default_pipeline`` now repairs both and is safe to re-run.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['crm.team']._ensure_default_pipeline()
    stages = env['crm.stage'].sudo().search([], order='sequence')
    configured = stages.filtered(lambda s: s.default_activity_type_id)
    _logger.info("sdlc_CRM: stage cycle is now %s; %d of %d carry a stage task",
                 stages.mapped('name'), len(configured), len(stages))
