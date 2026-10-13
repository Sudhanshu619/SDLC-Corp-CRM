# -*- coding: utf-8 -*-
from . import models


def post_init_hook(env):
    """Make the CRM usable the moment it is installed.

    Builds the default pipeline and its stage cycle in code rather than in a
    data file, so a deleted stage or pipeline can never break an upgrade.
    """
    env['crm.team']._ensure_default_pipeline()
