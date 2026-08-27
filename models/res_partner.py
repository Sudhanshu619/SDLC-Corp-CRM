# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # AC's Contact Score. Recomputed by the scoring engine from the ledger.
    contact_score = fields.Integer(
        string='Contact Score', readonly=True, index=True,
        help="Rule-based fit+intent score. Crossing a threshold triggers the "
             "score->deal handoff (blueprint B3).")

    # Marks that this contact has already been handed off, so the handoff
    # automation is idempotent and does not spawn duplicate deals.
    crm_handoff_done = fields.Boolean(
        string='Handed off to Sales', default=False, copy=False,
        help="Set once a deal has been created from this contact's score, so "
             "the handoff automation runs at most once per contact.")

    def _sdlc_dedupe_domain(self):
        """AC dedupes contacts on email; Odoo does not dedupe res.partner.
        Returns a domain matching an existing contact with the same email."""
        self.ensure_one()
        if not self.email:
            return False
        return [
            ('email', '=ilike', self.email),
            ('id', '!=', self.id),
            ('is_company', '=', False),
        ]

    @api.model_create_multi
    def create(self, vals_list):
        # Soft dedupe: log (do not silently merge) when a new individual
        # contact reuses an existing email. Merging is destructive and is left
        # to Odoo's native merge wizard; we surface the collision instead.
        partners = super().create(vals_list)
        for partner in partners:
            if partner.is_company or not partner.email:
                continue
            dup = self.search(partner._sdlc_dedupe_domain(), limit=1) if partner._sdlc_dedupe_domain() else False
            if dup:
                _logger.info(
                    "SDLC CRM: possible duplicate contact %s (%s) already exists as %s",
                    partner.display_name, partner.email, dup.display_name)
        return partners
