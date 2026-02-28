# # -*- coding: utf-8 -*-

# import ast
# import logging
# import re
# from odoo import api, fields, models
# from odoo.exceptions import UserError
# from odoo.tools.safe_eval import safe_eval, datetime

# _logger = logging.getLogger(__name__)


# class IrFilters(models.Model):
#     _inherit = 'ir.filters'

#     is_vllm_question = fields.Text(
#         string='Demande à VLLM',
#         help="Décrivez en langage naturel le filtre souhaité. "
#              "VLLM modifiera le domaine du favori en conséquence.",
#     )
#     is_vllm_domain_backup = fields.Text(
#         string='Domaine avant modification',
#         readonly=True,
#         help="Sauvegarde automatique du domaine avant modification par VLLM. "
#              "Vous pouvez le recopier dans le champ Domaine en cas de problème.",
#     )

#     def _get_model_fields_description(self):
#         """Retourne une description des champs du modèle pour aider VLLM."""
#         self.ensure_one()
#         if not self.model_id:
#             return ""
#         try:
#             model_obj = self.env[self.model_id]
#         except KeyError:
#             return ""

#         fields_desc = []
#         for fname, fobj in model_obj._fields.items():
#             if fname.startswith('_') or not fobj.store:
#                 continue
#             ftype = fobj.type
#             flabel = fobj.string or fname
#             info = "%s (%s, type=%s)" % (fname, flabel, ftype)
#             if ftype == 'selection' and fobj.selection:
#                 try:
#                     sel = fobj.selection
#                     if callable(sel):
#                         sel = sel(model_obj)
#                     sel_str = ', '.join("'%s'" % s[0] for s in sel[:20])
#                     info += " [%s]" % sel_str
#                 except Exception:
#                     pass
#             if ftype in ('many2one', 'one2many', 'many2many'):
#                 info += " -> %s" % (fobj.comodel_name or '')
#             fields_desc.append(info)

#         return '\n'.join(fields_desc)

#     def _validate_domain(self, domain_str):
#         """Valide qu'une chaîne est un domaine Odoo valide.

#         :param domain_str: Chaîne représentant un domaine Odoo
#         :return: dict avec 'valid' (bool), 'domain' (str nettoyé), 'error' (str)
#         """
#         try:
#             # Parser la chaîne comme du Python
#             domain = ast.literal_eval(domain_str)
#         except (ValueError, SyntaxError) as e:
#             return {'valid': False, 'domain': domain_str, 'error': "Syntaxe invalide : %s" % str(e)}

#         if not isinstance(domain, list):
#             return {'valid': False, 'domain': domain_str, 'error': "Le domaine doit être une liste."}

#         # Vérifier que chaque élément est un tuple/liste de 3 éléments ou un opérateur
#         valid_operators = ('&', '|', '!')
#         for item in domain:
#             if isinstance(item, str):
#                 if item not in valid_operators:
#                     return {'valid': False, 'domain': domain_str,
#                             'error': "Opérateur invalide '%s'. Utiliser '&', '|' ou '!'." % item}
#             elif isinstance(item, (list, tuple)):
#                 if len(item) != 3:
#                     return {'valid': False, 'domain': domain_str,
#                             'error': "Chaque condition doit avoir 3 éléments (champ, opérateur, valeur). Trouvé : %s" % str(item)}
#             else:
#                 return {'valid': False, 'domain': domain_str,
#                         'error': "Élément invalide dans le domaine : %s" % str(item)}

#         # Tester l'évaluation avec safe_eval
#         try:
#             safe_eval(domain_str, {
#                 'datetime': datetime,
#                 'context_today': datetime.datetime.now,
#             })
#         except Exception as e:
#             return {'valid': False, 'domain': domain_str, 'error': "Erreur d'évaluation : %s" % str(e)}

#         return {'valid': True, 'domain': domain_str, 'error': ''}

#     def _extract_domain_from_response(self, response_text):
#         """Extrait le domaine Odoo depuis la réponse VLLM.

#         Cherche un bloc de code Python ou une liste entre crochets.
#         :return: chaîne du domaine ou None
#         """
#         # Chercher dans un bloc de code ```python ... ``` ou ``` ... ```
#         code_block = re.search(r'```(?:python)?\s*\n?(.*?)\n?```', response_text, re.DOTALL)
#         if code_block:
#             candidate = code_block.group(1).strip()
#             if candidate.startswith('['):
#                 return candidate

#         # Chercher une liste directement dans le texte
#         list_match = re.search(r'(\[.*\])', response_text, re.DOTALL)
#         if list_match:
#             return list_match.group(1).strip()

#         return None

#     def action_ask_vllm_domain(self):
#         """Demande à VLLM de modifier le domaine du filtre."""
#         self.ensure_one()
#         if not self.is_vllm_question:
#             raise UserError("Veuillez saisir votre demande dans le champ 'Demande à VLLM'.")

#         if not self.model_id:
#             raise UserError("Le modèle du filtre doit être défini.")

#         # Construire le prompt avec le contexte
#         fields_desc = self._get_model_fields_description()

#         system_prompt = (
#             "Tu es un expert Odoo 16. Tu dois générer un domaine Odoo (domain) valide "
#             "en format Python (liste de tuples). "
#             "Réponds UNIQUEMENT avec le domaine entre ```python et ```, sans aucune explication. "
#             "Un domaine Odoo est une liste de tuples (champ, opérateur, valeur). "
#             "Les opérateurs valides sont : =, !=, >, >=, <, <=, like, ilike, in, not in, "
#             "child_of, parent_of, =like, =ilike, not like, not ilike. "
#             "Les opérateurs logiques sont : '&' (ET, par défaut), '|' (OU), '!' (NON). "
#             "Utilise UNIQUEMENT des champs qui existent dans le modèle."
#         )

#         prompt = (
#             "Modèle Odoo : %s\n\n"
#             "Domaine actuel :\n%s\n\n"
#             "Champs disponibles dans ce modèle :\n%s\n\n"
#             "Demande de l'utilisateur :\n%s\n\n"
#             "Génère le nouveau domaine Odoo correspondant à la demande."
#         ) % (self.model_id, self.domain or '[]', fields_desc, self.is_vllm_question)

#         # Appeler VLLM
#         vllm = self.env['is.vllm']
#         result = vllm.vllm_send_prompt(prompt, system_prompt=system_prompt)

#         if not result['success']:
#             raise UserError("Erreur VLLM : %s" % result['error'])

#         # Extraire le domaine de la réponse
#         response = result['response']
#         domain_str = self._extract_domain_from_response(response)

#         if not domain_str:
#             raise UserError(
#                 "VLLM n'a pas retourné de domaine valide.\n\n"
#                 "Réponse reçue :\n%s" % response
#             )

#         # Valider le domaine
#         validation = self._validate_domain(domain_str)

#         if not validation['valid']:
#             raise UserError(
#                 "Le domaine proposé par VLLM n'est pas valide :\n%s\n\n"
#                 "Domaine proposé :\n%s\n\n"
#                 "Réponse complète :\n%s" % (validation['error'], domain_str, response)
#             )

#         # Appliquer le nouveau domaine
#         old_domain = self.domain
#         self.is_vllm_domain_backup = old_domain
#         self.domain = validation['domain']

#         _logger.info("VLLM - Domaine du filtre '%s' modifié de %s vers %s",
#                       self.name, old_domain, self.domain)

#         return {
#             'type': 'ir.actions.client',
#             'tag': 'display_notification',
#             'params': {
#                 'title': 'Domaine modifié avec succès',
#                 'message': 'Le domaine a été mis à jour par VLLM.',
#                 'type': 'success',
#                 'sticky': False,
#                 'next': {'type': 'ir.actions.act_window_close'},
#             }
#         }
