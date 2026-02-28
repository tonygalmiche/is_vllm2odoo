# -*- coding: utf-8 -*-

from . import models


def post_init_hook(cr, registry):
    """Hook appelé après l'installation du module pour activer les groupes IA"""
    cr.execute("""
        UPDATE res_groups 
        SET active = TRUE 
        WHERE id IN (
            SELECT res_id 
            FROM ir_model_data 
            WHERE module = 'is_vllm2odoo' 
            AND model = 'res.groups'
            AND name IN ('group_ia_recherche_generale', 'group_ia_admin')
        )
        AND active IS NULL
    """)
