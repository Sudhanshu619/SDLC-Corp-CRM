# -*- coding: utf-8 -*-
"""Classify the contacts that already exist.

``contact_scope`` is computed-but-stored, so it fills itself in for new records
and for anyone linked to a user. Existing rows need one pass: the company's own
partners and anyone holding an internal login are ours, everybody else is an
external prospect or client.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    partners = env['res.partner'].with_context(active_test=False).search([])
    partners._compute_contact_scope()
    partners.filtered(lambda p: not p.contact_scope).write({'contact_scope': 'external'})
