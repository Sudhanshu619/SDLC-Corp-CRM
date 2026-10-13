# -*- coding: utf-8 -*-
"""Retire per-campaign stages: fold them back into the one shared cycle.

A campaign could own a private copy of the stage cycle. In practice that only
produced duplicate columns carrying the same names as the shared ones, so the
board looked identical whichever campaign you opened - while quietly splitting
the deals across two sets of "New", "Qualified", ... Stages live in one place
now (Configuration > Pipeline Setup).

Folding rules, in order:

1. A scoped stage whose name matches a shared stage is a duplicate: its deals
   move to the shared stage and the duplicate is deleted.
2. A scoped stage with no shared twin is the only one of its kind (the "Won"
   stage ended up here), so it is kept and simply unscoped - deleting it would
   strand its deals.
3. The relation table is dropped last, once nothing reads it.

Deals are never deleted and never left pointing at a removed stage.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

REL = 'crm_stage_utm_campaign_rel'


def _name(stage):
    """Stage names are translatable (jsonb); compare on the plain value."""
    return (stage.name or '').strip().lower()


def migrate(cr, version):
    if not version:
        return

    cr.execute("SELECT to_regclass(%s)", (REL,))
    if not cr.fetchone()[0]:
        _logger.info("sdlc_CRM: no %s table, nothing to fold", REL)
        return

    cr.execute("SELECT DISTINCT stage_id FROM %s" % REL)
    scoped_ids = [row[0] for row in cr.fetchall()]
    if not scoped_ids:
        cr.execute("DROP TABLE IF EXISTS %s" % REL)
        _logger.info("sdlc_CRM: no campaign-scoped stages; dropped %s", REL)
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Stage = env['crm.stage'].sudo()
    Lead = env['crm.lead'].with_context(active_test=False).sudo()

    scoped = Stage.browse(scoped_ids).exists()
    shared = Stage.search([('id', 'not in', scoped.ids)], order='sequence, id')
    shared_by_name = {}
    for stage in shared:
        shared_by_name.setdefault(_name(stage), stage)

    merged = moved = kept = 0
    for stage in scoped:
        twin = shared_by_name.get(_name(stage))
        deals = Lead.search([('stage_id', '=', stage.id)])
        label = stage.display_name          # read it BEFORE the unlink
        if twin:
            if deals:
                deals.write({'stage_id': twin.id})
                moved += len(deals)
            stage.unlink()
            merged += 1
            _logger.info("sdlc_CRM: folded duplicate stage %r into the shared one "
                         "(%d deal(s) moved)", label, len(deals))
        else:
            # No shared equivalent - keep it, it is now part of the shared set.
            kept += 1
            shared_by_name[_name(stage)] = stage
            _logger.info("sdlc_CRM: kept stage %r (no shared twin); it is now "
                         "shared by every campaign", label)

    cr.execute("DROP TABLE IF EXISTS %s" % REL)
    _logger.info("sdlc_CRM: per-campaign stages retired - %d duplicate(s) merged, "
                 "%d deal(s) moved, %d stage(s) kept and unscoped", merged, moved, kept)
