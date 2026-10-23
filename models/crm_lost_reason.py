# -*- coding: utf-8 -*-
from odoo import api, fields, models

# The fixed buckets loss analysis groups on. Lost reasons stay free to add and
# rename; each one is filed under exactly one of these.
LOSS_CATEGORIES = [
    ('price', 'Price'),
    ('product_fit', 'Product / Fit'),
    ('competitor', 'Competitor'),
    ('timing', 'Timing'),
    ('no_decision', 'No Decision'),
    ('other', 'Other'),
]


class CrmLostReason(models.Model):
    _inherit = 'crm.lost.reason'

    loss_category = fields.Selection(
        LOSS_CATEGORIES, string='Loss Category',
        help="Bucket this reason is reported under. Choosing a 'Competitor' "
             "reason makes the competitor name mandatory when a deal is lost.")

    @api.model
    def _sdlc_categorise_default_reasons(self):
        """File Odoo's three shipped reasons under a category.

        crm loads them noupdate, so a data file cannot touch them on upgrade.
        Only empty categories are filled: a category someone set by hand wins.
        """
        defaults = {
            'crm.lost_reason_1': 'price',        # Too expensive
            'crm.lost_reason_2': 'product_fit',  # We don't have people/skills
            'crm.lost_reason_3': 'product_fit',  # Not enough stock
        }
        for xmlid, category in defaults.items():
            reason = self.env.ref(xmlid, raise_if_not_found=False)
            if reason and not reason.loss_category:
                reason.loss_category = category
        return True
