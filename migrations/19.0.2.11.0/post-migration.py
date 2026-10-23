# -*- coding: utf-8 -*-
"""Backfill 'LC - Status Change' and 'Lost at Stage' on existing deals.

Both are written going forward by crm.lead.write(); deals that changed status
before this version would otherwise show them blank.

* LC - Status Change: the latest status_changed journey event, which is exactly
  what write() would have stamped. Won deals with no such event fall back to
  date_closed. Lost deals with neither stay blank rather than guess.
* Lost at Stage: a lost deal still carries the stage it was lost in, unless
  someone moved it since - the best snapshot available now.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE crm_lead l
           SET lc_status_change_date = ev.last_change
          FROM (SELECT lead_id, max(create_date)::date AS last_change
                  FROM crm_journey_event
                 WHERE event = 'status_changed' AND lead_id IS NOT NULL
              GROUP BY lead_id) ev
         WHERE ev.lead_id = l.id
           AND l.lc_status_change_date IS NULL
    """)
    from_events = cr.rowcount
    cr.execute("""
        UPDATE crm_lead
           SET lc_status_change_date = date_closed::date
         WHERE lc_status_change_date IS NULL
           AND ac_deal_status = 'won'
           AND date_closed IS NOT NULL
    """)
    from_closed = cr.rowcount
    cr.execute("""
        UPDATE crm_lead
           SET lost_stage_id = stage_id
         WHERE lost_stage_id IS NULL
           AND ac_deal_status = 'lost'
           AND type = 'opportunity'
    """)
    _logger.info(
        "sdlc_CRM 19.0.2.11.0: LC - Status Change backfilled on %s deals from "
        "journey events and %s from close dates; Lost at Stage on %s deals.",
        from_events, from_closed, cr.rowcount)
