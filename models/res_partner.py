# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResPartner(models.Model):
    """Contacts, split into the people we work *with* and the people we work *for*.

    Everyone a deal touches ends up here, so the pipeline has a real contact
    record to hang history on. What they are not is users: a contact has no
    login at all until someone explicitly grants one, and granting one is what
    turns them internal.
    """
    _inherit = 'res.partner'

    contact_scope = fields.Selection(
        [('external', 'External (prospect or client)'),
         ('internal', 'Internal (our team)')],
        string='Contact Type', default='external', index=True,
        compute='_compute_contact_scope', store=True, readonly=False,
        help="External contacts are prospects and clients: they can never sign "
             "in unless you grant them access. Internal contacts are the people "
             "working for us; a contact becomes internal automatically once it "
             "is linked to an internal user.")

    has_system_access = fields.Boolean(
        string='Has Access', compute='_compute_has_system_access',
        help="Whether this contact can sign in. A contact with no user account "
             "cannot log in at all.")

    @api.depends('user_ids', 'user_ids.share', 'user_ids.active')
    def _compute_contact_scope(self):
        """Internal the moment a real (non-portal) user is linked.

        Stored and editable, so a manual choice sticks; this only ever promotes
        someone who has been given a login, and never demotes a contact that was
        marked internal by hand.
        """
        company_partners = self.env['res.company'].sudo().search([]).partner_id
        for partner in self:
            internal_user = partner.user_ids.filtered(lambda u: not u.share)
            if internal_user or partner in company_partners:
                partner.contact_scope = 'internal'
            elif not partner.contact_scope:
                partner.contact_scope = 'external'

    @api.depends('user_ids', 'user_ids.active')
    def _compute_has_system_access(self):
        for partner in self:
            partner.has_system_access = bool(partner.user_ids.filtered('active'))
