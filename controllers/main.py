# -*- coding: utf-8 -*-
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class SdlcCrmTracking(http.Controller):
    """REST ingest endpoint for behavioural events (blueprint D1 #43, E1 tracking).

    A JS snippet on any site (Odoo-hosted or external) POSTs behavioural events
    here; they land in ``crm.tracked.event`` and feed behavioural score rules
    and automation triggers.

    Example:
        POST /sdlc_crm/track
        {"jsonrpc":"2.0","params":{
            "email":"jane@acme.com","name":"pricing_viewed",
            "event_type":"page_visit","url":"https://acme.com/pricing"}}
    """

    @http.route('/sdlc_crm/track', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    def track(self, **kwargs):
        params = request.get_json_data().get('params', kwargs) if request.httprequest.data else kwargs
        vals = {
            'name': params.get('name') or 'custom',
            'event_type': params.get('event_type') or 'custom',
            'email': params.get('email'),
            'url': params.get('url'),
            'event_data': params.get('event_data'),
        }
        if not vals['email'] and not params.get('partner_id'):
            return {'status': 'error', 'message': 'email or partner_id required'}
        if params.get('partner_id'):
            vals['partner_id'] = int(params['partner_id'])
        event = request.env['crm.tracked.event'].sudo().record_event(vals)
        return {'status': 'ok', 'event_id': event.id, 'partner_id': event.partner_id.id or False}


# ----------------------------------------------------------------------
# Woodpecker -> CRM webhook
# Woodpecker owns the outbound email + sequences; when a prospect engages
# (opens / clicks / replies / bounces) it POSTs here. We normalise the event,
# resolve-or-create the contact, and award intent points. A reply that pushes
# the contact past the handoff threshold opens a deal via the existing
# score>=75 automation -> the real pipeline begins.
# ----------------------------------------------------------------------
WOODPECKER_EVENT_MAP = {
    # Woodpecker-style keys
    'email_sent': 'email_sent', 'sent': 'email_sent', 'EMAIL_SENT': 'email_sent',
    'email_opened': 'email_opened', 'opened': 'email_opened', 'open': 'email_opened',
    'EMAIL_OPENED': 'email_opened', 'PROSPECT_OPENED': 'email_opened',
    'email_clicked': 'email_clicked', 'clicked': 'email_clicked', 'click': 'email_clicked',
    'LINK_CLICKED': 'email_clicked',
    'email_replied': 'email_replied', 'replied': 'email_replied', 'reply': 'email_replied',
    'PROSPECT_REPLIED': 'email_replied', 'REPLIED': 'email_replied',
    'email_bounced': 'email_bounced', 'bounced': 'email_bounced', 'bounce': 'email_bounced',
    'EMAIL_BOUNCED': 'email_bounced',
    'unsubscribed': 'unsubscribed', 'opted_out': 'unsubscribed', 'opt-out': 'unsubscribed',
    'PROSPECT_OPTED_OUT': 'unsubscribed', 'OPTED_OUT': 'unsubscribed',
}


class SdlcCrmWoodpecker(http.Controller):

    @staticmethod
    def _map_event(raw):
        if not raw:
            return None
        return WOODPECKER_EVENT_MAP.get(raw) or WOODPECKER_EVENT_MAP.get(str(raw).lower())

    @staticmethod
    def _check_secret(params):
        """Optional shared-secret guard. Set System Parameter
        'sdlc_crm.woodpecker_secret' to enforce it. Woodpecker's custom-webhook
        UI only offers a URL (no custom headers), so the token may be passed as
        a URL query param (…/woodpecker?token=SECRET); we also accept it via the
        X-Woodpecker-Token header or a 'token' body field."""
        expected = request.env['ir.config_parameter'].sudo().get_param('sdlc_crm.woodpecker_secret')
        if not expected:
            return True
        provided = (request.httprequest.headers.get('X-Woodpecker-Token')
                    or request.httprequest.args.get('token')
                    or params.get('token'))
        return provided == expected

    def _ingest(self, params):
        """Normalise a Woodpecker payload and record it. Returns a result dict."""
        if not self._check_secret(params):
            return {'status': 'error', 'message': 'invalid or missing token'}
        # Woodpecker nests contact fields under a "prospect" object; accept both
        # the nested and a flat shape.
        prospect = params.get('prospect') or params
        email = prospect.get('email') or params.get('email')
        event_type = self._map_event(params.get('event') or params.get('event_type'))
        if not email:
            return {'status': 'error', 'message': 'prospect email required'}
        if not event_type:
            return {'status': 'error', 'message': 'unknown or missing event'}

        campaign = params.get('campaign') or {}
        campaign_name = (campaign.get('name') if isinstance(campaign, dict) else campaign) \
            or params.get('campaign_name')
        first = prospect.get('first_name') or ''
        last = prospect.get('last_name') or ''
        contact_name = (first + ' ' + last).strip() or prospect.get('name')

        event = request.env['crm.tracked.event'].sudo().record_event({
            'name': event_type,
            'event_type': event_type,
            'email': email,
            'contact_name': contact_name,
            'company_name': prospect.get('company') or prospect.get('organization'),
            'campaign_name': campaign_name,
            'url': params.get('url') or params.get('link'),
        })
        partner = event.partner_id
        return {
            'status': 'ok',
            'event_id': event.id,
            'partner_id': partner.id or False,
            'contact_score': partner.contact_score if partner else 0,
            'handed_off': bool(partner and partner.crm_handoff_done),
        }

    # Plain-HTTP endpoint: this is the one Woodpecker / Zapier / Make POST to,
    # since they send a raw JSON body (no JSON-RPC envelope).
    @http.route(['/sdlc_crm/woodpecker', '/webhooks/woodpecker'],
                type='http', auth='public', methods=['POST'], csrf=False)
    def woodpecker_http(self, **kwargs):
        body = request.httprequest.get_data() or b'{}'
        # Optional: log the raw payload so the first live Woodpecker POST reveals
        # its exact field shape. Enable with System Parameter
        # 'sdlc_crm.woodpecker_debug' = 1, disable once the mapping is confirmed.
        if request.env['ir.config_parameter'].sudo().get_param('sdlc_crm.woodpecker_debug'):
            _logger.info("Woodpecker webhook raw payload: %s", body[:4000])
        try:
            params = json.loads(body.decode('utf-8')) if body.strip() else {}
        except (ValueError, UnicodeDecodeError):
            params = {}
        if not isinstance(params, dict) or not params:
            params = kwargs
        result = self._ingest(params)
        return request.make_json_response(result)

    # JSON-RPC variant, for internal/Odoo-style callers using the envelope.
    @http.route('/sdlc_crm/woodpecker/rpc', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    def woodpecker_rpc(self, **kwargs):
        params = request.get_json_data().get('params', kwargs) if request.httprequest.data else kwargs
        return self._ingest(params)
