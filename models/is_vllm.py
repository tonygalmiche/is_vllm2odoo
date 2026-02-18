# -*- coding: utf-8 -*-

import base64
import io
import json
import logging
import requests
from odoo import api, models

_logger = logging.getLogger(__name__)


class IsVllm(models.AbstractModel):
    """Modèle générique réutilisable pour communiquer avec un serveur VLLM.

    Ce modèle abstrait fournit les méthodes nécessaires pour envoyer des prompts
    à un serveur VLLM (API compatible OpenAI) et recevoir les réponses.
    Il peut être utilisé par n'importe quel autre modèle Odoo.
    """
    _name = 'is.vllm'
    _description = 'Communication VLLM'

    @api.model
    def _get_vllm_config(self):
        """Récupère la configuration VLLM depuis la fiche société."""
        company = self.env.company
        config = {
            'url':         company.is_vllm_url or '',
            'api_key':     company.is_vllm_api_key or '',
            'model':       company.is_vllm_model or '',
            'temperature': company.is_vllm_temperature,
            'max_tokens':  company.is_vllm_max_tokens,
        }
        return config

    @api.model
    def _pdf_to_base64_images(self, pdf_data):
        """Convertit un PDF (bytes) en liste d'images base64.

        :param pdf_data: Contenu binaire du PDF
        :return: liste de chaînes base64 (une par page)
        """
        try:
            from pdf2image import convert_from_bytes
        except ImportError:
            _logger.warning("pdf2image n'est pas installé. Installez-le avec : pip install pdf2image")
            return []

        images_b64 = []
        try:
            pages = convert_from_bytes(pdf_data, dpi=200)
            for page in pages:
                buf = io.BytesIO()
                page.save(buf, format='PNG')
                img_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
                images_b64.append(img_b64)
        except Exception as e:
            _logger.error("Erreur lors de la conversion PDF en images : %s", str(e))
        return images_b64

    @api.model
    def vllm_send_prompt(self, prompt, system_prompt=None, images_b64=None, model=None, temperature=None, max_tokens=None):
        """Envoie un prompt au serveur VLLM et retourne la réponse.

        :param prompt: Le prompt utilisateur à envoyer
        :param system_prompt: Prompt système optionnel
        :param images_b64: Liste de tuples (base64_str, mime_type) pour les images à envoyer
        :param model: Modèle à utiliser (écrase la config société si fourni)
        :param temperature: Température (écrase la config société si fournie)
        :param max_tokens: Nombre max de tokens (écrase la config société si fourni)
        :return: dict avec 'success' (bool), 'response' (str) et 'error' (str) si erreur
        """
        config = self._get_vllm_config()

        url = config['url']
        if not url:
            return {'success': False, 'response': '', 'error': "L'URL du serveur VLLM n'est pas configurée dans la fiche société."}

        # Construire l'URL de l'endpoint
        if not url.endswith('/'):
            url += '/'
        endpoint = url + 'v1/chat/completions'

        # Construire les messages
        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})

        # Si des images sont fournies, utiliser le format vision (multimodal)
        if images_b64:
            content_parts = []
            content_parts.append({'type': 'text', 'text': prompt})
            for img_b64, mime_type in images_b64:
                content_parts.append({
                    'type': 'image_url',
                    'image_url': {
                        'url': 'data:%s;base64,%s' % (mime_type, img_b64),
                    },
                })
            messages.append({'role': 'user', 'content': content_parts})
        else:
            messages.append({'role': 'user', 'content': prompt})

        # Paramètres
        payload = {
            'model':       model or config['model'],
            'messages':     messages,
            'temperature':  temperature if temperature is not None else config['temperature'],
            'max_tokens':   max_tokens if max_tokens is not None else config['max_tokens'],
        }

        headers = {
            'Content-Type': 'application/json',
        }
        if config['api_key']:
            headers['Authorization'] = 'Bearer %s' % config['api_key']

        try:
            _logger.info("VLLM - Envoi du prompt vers %s", endpoint)
            response = requests.post(
                endpoint,
                headers=headers,
                data=json.dumps(payload),
                timeout=120,
            )
            response.raise_for_status()
            result = response.json()

            # Extraire le contenu de la réponse (format OpenAI)
            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0].get('message', {}).get('content', '')
                return {'success': True, 'response': content, 'error': ''}
            else:
                return {'success': False, 'response': '', 'error': "Réponse VLLM inattendue : pas de 'choices' dans la réponse."}

        except requests.exceptions.ConnectionError as e:
            msg = "Impossible de se connecter au serveur VLLM (%s) : %s" % (endpoint, str(e))
            _logger.error(msg)
            return {'success': False, 'response': '', 'error': msg}
        except requests.exceptions.Timeout:
            msg = "Timeout lors de la connexion au serveur VLLM (%s)" % endpoint
            _logger.error(msg)
            return {'success': False, 'response': '', 'error': msg}
        except requests.exceptions.HTTPError as e:
            msg = "Erreur HTTP du serveur VLLM : %s" % str(e)
            _logger.error(msg)
            return {'success': False, 'response': '', 'error': msg}
        except Exception as e:
            msg = "Erreur inattendue lors de la communication avec VLLM : %s" % str(e)
            _logger.error(msg)
            return {'success': False, 'response': '', 'error': msg}
