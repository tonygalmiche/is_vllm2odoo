# -*- coding: utf-8 -*-

import base64
import time
from odoo import api, fields, models
from odoo.exceptions import UserError


#tony@debian:~$ ssh -R 8000:10.1.5.57:8000 odoo@plastigray -N



class IsChatVllm(models.Model):
    """Formulaire de chat simple avec VLLM (type ChatGPT light)."""
    _name = 'is.chat.vllm'
    _description = 'Chat VLLM'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(
        string='N°',
        readonly=True,
        default='Nouveau',
        copy=False,
    )
    question = fields.Text(
        string='Question',
        required=True,
        tracking=True,
    )
    response = fields.Html(
        string='Réponse',
        readonly=True,
        sanitize=False,
        copy=False,
    )
    temps_reponse = fields.Float(
        string='Temps de réponse (s)',
        readonly=True,
        copy=False,
    )
    piece_jointe_ids = fields.Many2many(
        'ir.attachment',
        string='Pièces jointes',
        help="Images ou PDF à envoyer au serveur VLLM pour analyse",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'Nouveau') == 'Nouveau':
                vals['name'] = self.env['ir.sequence'].next_by_code('is.chat.vllm') or 'Nouveau'
        return super().create(vals_list)

    def _get_images_from_attachments(self):
        """Extrait les images base64 depuis les pièces jointes.

        Les images (JPEG, PNG, GIF, WEBP) sont envoyées directement.
        Les PDF sont convertis en images page par page.

        :return: liste de tuples (base64_str, mime_type)
        """
        self.ensure_one()
        vllm = self.env['is.vllm']
        images = []
        image_mimes = ('image/jpeg', 'image/png', 'image/gif', 'image/webp')

        for attachment in self.piece_jointe_ids:
            mimetype = attachment.mimetype or ''
            if mimetype in image_mimes:
                # Image : envoyer directement en base64
                img_b64 = attachment.datas.decode('utf-8') if isinstance(attachment.datas, bytes) else attachment.datas
                images.append((img_b64, mimetype))
            elif mimetype == 'application/pdf':
                # PDF : convertir chaque page en image PNG
                pdf_data = base64.b64decode(attachment.datas)
                pages_b64 = vllm._pdf_to_base64_images(pdf_data)
                for page_b64 in pages_b64:
                    images.append((page_b64, 'image/png'))
        return images

    def action_send_question(self):
        """Envoie la question au serveur VLLM et met à jour la réponse."""
        self.ensure_one()
        if not self.question:
            raise UserError("Veuillez saisir une question.")

        vllm = self.env['is.vllm']

        # Récupérer les images depuis les pièces jointes
        images_b64 = self._get_images_from_attachments()

        start = time.time()
        result = vllm.vllm_send_prompt(self.question, images_b64=images_b64)
        elapsed = time.time() - start

        if result['success']:
            self.response = result['response']
            self.temps_reponse = round(elapsed, 1)
            # Poster la question et la réponse dans le chatter
            pj_info = ''
            if self.piece_jointe_ids:
                pj_names = ', '.join(self.piece_jointe_ids.mapped('name'))
                pj_info = '<br/><i>Pièces jointes : %s</i>' % pj_names
            body = "<b>Question :</b><br/>%s%s<br/><br/><b>Réponse :</b><br/>%s" % (
                self.question.replace('\n', '<br/>'),
                pj_info,
                result['response'].replace('\n', '<br/>'),
            )
            self.message_post(body=body, message_type='comment')
        else:
            raise UserError("Erreur VLLM : %s" % result['error'])
