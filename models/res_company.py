# -*- coding: utf-8 -*-

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    is_vllm_url = fields.Char(
        string='URL du serveur VLLM',
        help="URL de base du serveur VLLM (ex: http://mon-serveur:8000)",
    )
    is_vllm_api_key = fields.Char(
        string='Clé API',
        help="Clé API pour l'authentification (optionnel)",
    )
    is_vllm_model = fields.Char(
        string='Modèle',
        help="Nom du modèle à utiliser (ex: meta-llama/Llama-2-7b-chat-hf)",
    )
    is_vllm_temperature = fields.Float(
        string='Température',
        default=0.7,
        help="Contrôle la créativité des réponses (0 = déterministe, 1 = créatif)",
    )
    is_vllm_max_tokens = fields.Integer(
        string='Tokens max',
        default=2048,
        help="Nombre maximum de tokens dans la réponse",
    )
