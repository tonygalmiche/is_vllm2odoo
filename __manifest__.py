# -*- coding: utf-8 -*-
{
    'name': 'Intégration de VLLM dans  Odoo',
    'version': '16.0.1.1.0',
    'summary': 'Module Odoo 16 pour Plastigray pour intégrer VLLM',
    'description': """
    """,
    "author"   : "InfoSaône",
    "category" : "InfoSaône",
    'website': '',
    'license': 'LGPL-3',
    'depends': ['base', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'security/is_chat_vllm_rules.xml',
        'views/is_chat_vllm_views.xml',
        'views/is_search_general_views.xml',
        'views/ir_filters_views.xml',
        'views/res_company_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
