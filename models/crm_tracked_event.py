# -*- coding: utf-8 -*-
import fnmatch
import logging
from dateutil.relativedelta import relativedelta

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Email-engagement events (typically fed by Woodpecker) award intent points on
# the contact. Kept as a small, tunable map; awarded once per type per contact
# so repeated opens/clicks don't inflate the score. Reply is the strong signal
# that -- stacked with fit points -- pushes the contact past the handoff
# threshold, at which point the existing "score >= 75 -> create deal"
# automation opens a deal and the real pipeline begins.
ENGAGEMENT_POINTS = {
    'email_opened': 5,
    'email_clicked': 15,
    'email_replied': 50,
    'email_bounced': -20,
    'unsubscribed': -100,
}


class CrmTrackedEvent(models.Model):
    """Behavioural fact store (blueprint A1 #2, C1 e-comm/behavioural, D1 #43).

    AC treats behaviour -- page visits, custom events, downloads -- as
    first-class stored facts any segment/score/automation can query. Odoo has
    no equivalent on Community, so we build a lightweight event store fed by a
    REST endpoint (see controllers/main.py) and readable by score-rule domains.
    It also ingests email-engagement events from Woodpecker (open/click/reply/
    bounce) and turns them into contact-score intent points.
    """
    _name = 'crm.tracked.event'
    _description = 'Behavioural Tracked Event'
    _order = 'event_date desc, id desc'

    name = fields.Char(string='Event Name', required=True, index=True,
                       help="e.g. 'page_visit', 'pricing_viewed', 'demo_requested'.")
    event_type = fields.Selection([
        ('page_visit', 'Page Visit'),
        ('custom', 'Custom Event'),
        ('download', 'File Download'),
        ('form', 'Form Submission'),
        # Email engagement (Woodpecker)
        ('email_sent', 'Email Sent'),
        ('email_opened', 'Email Opened'),
        ('email_clicked', 'Email Clicked'),
        ('email_replied', 'Email Replied'),
        ('email_bounced', 'Email Bounced'),
        ('unsubscribed', 'Unsubscribed / Opted Out'),
    ], default='custom', required=True, index=True)

    partner_id = fields.Many2one('res.partner', string='Contact', index=True, ondelete='cascade')
    email = fields.Char(index=True, help="Identity key used to resolve the contact.")
    campaign_name = fields.Char('Campaign', index=True,
                                help="Source campaign, e.g. the Woodpecker campaign name.")

    url = fields.Char('URL / Page')
    event_data = fields.Text('Payload (JSON)')
    event_date = fields.Datetime(default=lambda s: fields.Datetime.now(), index=True)

    @api.model
    def _resolve_partner(self, email):
        if not email:
            return self.env['res.partner']
        return self.env['res.partner'].search(
            [('email', '=ilike', email)], limit=1)

    @api.model
    def _resolve_or_create_partner(self, vals):
        """Find the contact by email, or create it. Woodpecker repliers are
        usually brand-new people, so an inbound reply must be able to mint the
        contact that then flows into the pipeline."""
        email = vals.get('email')
        partner = self._resolve_partner(email)
        if partner or not email:
            return partner
        Partner = self.env['res.partner'].sudo()
        parent = self.env['res.partner']
        company_name = vals.get('company_name')
        if company_name:
            parent = Partner.search(
                [('is_company', '=', True), ('name', '=ilike', company_name)], limit=1)
            if not parent:
                parent = Partner.create({'name': company_name, 'is_company': True})
        return Partner.create({
            'name': vals.get('contact_name') or email,
            'email': email,
            'company_type': 'person',
            'parent_id': parent.id or False,
        })

    @api.model
    def record_event(self, vals):
        """Ingest one event. Resolves (or creates) the contact by email, fires
        the flow event, and -- for email-engagement events -- awards intent
        points so the contact score can cross the handoff threshold."""
        partner = self._resolve_or_create_partner(vals)
        if partner and not vals.get('partner_id'):
            vals['partner_id'] = partner.id
        # Keep only real model fields on the tracked-event record.
        event = self.create({
            'name': vals.get('name') or vals.get('event_type') or 'custom',
            'event_type': vals.get('event_type') or 'custom',
            'email': vals.get('email'),
            'partner_id': vals.get('partner_id'),
            'campaign_name': vals.get('campaign_name'),
            'url': vals.get('url'),
            'event_data': vals.get('event_data'),
        })
        if partner:
            # Make behaviour observable in the unified event stream.
            self.env['crm.journey.event'].sudo().create({
                'event': 'event_recorded',
                'res_model': 'res.partner',
                'res_id': partner.id,
                'partner_id': partner.id,
                'value_to': event.name,
                'note': event.campaign_name or event.url or event.event_type,
            })
            # Email engagement -> contact intent points -> (maybe) a deal.
            if event.event_type in ENGAGEMENT_POINTS:
                self.sudo()._award_engagement(partner, event)
                self.sudo()._refresh_contact_score(partner)
        return event

    # ------------------------------------------------------------------
    # Engagement scoring
    # ------------------------------------------------------------------
    @api.model
    def _award_engagement(self, partner, event):
        """Write an engagement ledger row for the contact, once per event type
        so repeated opens/clicks don't inflate the score."""
        points = ENGAGEMENT_POINTS.get(event.event_type, 0)
        if not points:
            return
        score = self.env.ref('sdlc_CRM.score_contact_engagement', raise_if_not_found=False)
        if not score:
            return
        Ledger = self.env['crm.score.ledger'].sudo()
        reason = 'Woodpecker: %s' % event.event_type
        if Ledger.search_count([
                ('score_id', '=', score.id),
                ('res_model', '=', 'res.partner'),
                ('res_id', '=', partner.id),
                ('reason', '=', reason)]):
            return  # already awarded this engagement type for this contact
        expiry = False
        if score.expiry_days:
            expiry = fields.Datetime.now() + relativedelta(days=score.expiry_days)
        Ledger.create({
            'score_id': score.id,
            'rule_id': False,
            'res_model': 'res.partner',
            'res_id': partner.id,
            'points': points,
            'reason': reason,
            'date_expiry': expiry,
        })

    # ------------------------------------------------------------------
    # Native Woodpecker integration: pull replied prospects from the
    # Woodpecker REST API on a cron (no Zapier). Config via System Parameters:
    #   sdlc_crm.woodpecker_api_key   (required to enable)
    #   sdlc_crm.woodpecker_base_url  (default https://api.woodpecker.co)
    #   sdlc_crm.woodpecker_auth      ('basic' default, or 'bearer')
    # ------------------------------------------------------------------
    @api.model
    def _woodpecker_config(self):
        ICP = self.env['ir.config_parameter'].sudo()
        return {
            'api_key': ICP.get_param('sdlc_crm.woodpecker_api_key'),
            'base_url': (ICP.get_param('sdlc_crm.woodpecker_base_url')
                         or 'https://api.woodpecker.co').rstrip('/'),
            'auth': (ICP.get_param('sdlc_crm.woodpecker_auth') or 'basic').lower(),
        }

    @api.model
    def _ingest_replied_prospects(self, prospects, default_campaign=False):
        """Feed a list of Woodpecker prospect dicts into the reply flow.

        Idempotent: a prospect whose reply we've already recorded is skipped, so
        re-polling the same REPLIED prospect neither double-scores nor spams the
        event log. Returns the number of new replies ingested."""
        count = 0
        for p in prospects or []:
            email = (p.get('email') or '').strip()
            if not email:
                continue
            if self.search_count([('email', '=ilike', email),
                                   ('event_type', '=', 'email_replied')]):
                continue  # reply already ingested for this contact
            campaign = p.get('campaign') or p.get('campaign_name') or default_campaign
            self.record_event({
                'name': 'email_replied',
                'event_type': 'email_replied',
                'email': email,
                'contact_name': ((p.get('first_name') or '') + ' '
                                 + (p.get('last_name') or '')).strip() or p.get('name'),
                'company_name': p.get('company') or p.get('organization'),
                'campaign_name': campaign,
            })
            count += 1
        return count

    @api.model
    def _cron_poll_woodpecker_replies(self):
        """Cron entry: GET replied prospects from Woodpecker and ingest them."""
        cfg = self._woodpecker_config()
        if not cfg['api_key']:
            _logger.info("Woodpecker poll skipped: no API key set "
                         "(System Parameter 'sdlc_crm.woodpecker_api_key').")
            return False
        url = '%s/rest/v1/prospects' % cfg['base_url']
        headers, auth = {}, None
        if cfg['auth'] == 'bearer':
            headers['Authorization'] = 'Bearer %s' % cfg['api_key']
        else:
            auth = (cfg['api_key'], '')  # Woodpecker Basic: key as username
        try:
            resp = requests.get(url, params={'status': 'REPLIED'},
                                headers=headers, auth=auth, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            _logger.warning("Woodpecker poll failed: %s", e)
            return False
        # Woodpecker responses vary: a bare list, or {"prospects":[...]}.
        prospects = data.get('prospects', data) if isinstance(data, dict) else data
        ingested = self._ingest_replied_prospects(prospects)
        _logger.info("Woodpecker poll: %s new repl%s ingested.",
                     ingested, 'y' if ingested == 1 else 'ies')
        return ingested

    @api.model
    def _refresh_contact_score(self, partner):
        """Recompute the contact's total. Re-evaluating the MQL score also
        awards the 'once' fit rules immediately (business email/company/phone)
        so a genuine replier crosses the threshold without waiting for the cron;
        crossing 75 makes the handoff automation open the deal."""
        mql = self.env.ref('sdlc_CRM.score_contact_mql', raise_if_not_found=False)
        if mql:
            mql.sudo()._evaluate(partner)
            return
        score = self.env.ref('sdlc_CRM.score_contact_engagement', raise_if_not_found=False)
        if score:
            score.sudo()._recompute_totals(partner)

    @api.model
    def partner_visited(self, partner, url_pattern):
        """Wildcard page-match helper for score rules / automations
        (blueprint F2 #8: support 'domain.com/*' and 'domain.com/*/page')."""
        if not partner:
            return False
        events = self.search([
            ('partner_id', '=', partner.id),
            ('event_type', '=', 'page_visit'),
        ])
        for ev in events:
            if ev.url and fnmatch.fnmatch(ev.url, url_pattern):
                return True
        return False
