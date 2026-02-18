# -*- coding: utf-8 -*-

import logging
import re
import time
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval, datetime

_logger = logging.getLogger(__name__)


class IsSearchGeneral(models.Model):
    _name = 'is.search.general'
    _description = 'Recherche générale'
    _inherit = ['mail.thread']
    _order = 'create_date desc'

    name = fields.Char(
        string='N°',
        readonly=True,
        default='Nouveau',
        copy=False,
    )
    question = fields.Text(
        string='Recherche',
        required=True,
        tracking=True,
        help="Décrivez en langage naturel ce que vous recherchez. "
             "Ex: Liste des factures de ce mois",
    )
    model_id = fields.Many2one(
        'ir.model',
        string='Modèle',
        help="Modèle Odoo dans lequel chercher. "
             "Si vide, VLLM le déterminera automatiquement.",
    )
    model_name = fields.Char(
        string='Nom technique du modèle',
        related='model_id.model',
        store=True,
        readonly=True,
    )
    domain = fields.Text(
        string='Domaine calculé',
        readonly=True,
        tracking=True,
        copy=False,
    )
    vllm_model_response = fields.Text(
        string='Réponse VLLM (modèle)',
        readonly=True,
        copy=False,
    )
    vllm_domain_response = fields.Text(
        string='Réponse VLLM (domaine)',
        readonly=True,
        copy=False,
    )
    temps_reponse = fields.Float(
        string='Temps de réponse (s)',
        readonly=True,
        copy=False,
    )
    nb_results = fields.Integer(
        string="Nombre d'enregistrements",
        readonly=True,
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'Nouveau') == 'Nouveau':
                vals['name'] = self.env['ir.sequence'].next_by_code('is.search.general') or 'Nouveau'
        return super().create(vals_list)

    def _get_installed_models_list(self):
        """Retourne la liste des modèles installés avec leur description."""
        models = self.env['ir.model'].sudo().search([
            ('transient', '=', False),
        ], order='model')
        lines = []
        for m in models:
            lines.append('%s (%s)' % (m.model, m.name))
        return '\n'.join(lines)

    def _get_model_fields_description(self, model_name):
        """Retourne une description des champs du modèle pour aider VLLM."""
        try:
            model_obj = self.env[model_name]
        except KeyError:
            return ""
        fields_desc = []
        for fname, fobj in model_obj._fields.items():
            if fname.startswith('_') or not fobj.store:
                continue
            ftype = fobj.type
            flabel = fobj.string or fname
            info = "%s (%s, type=%s)" % (fname, flabel, ftype)
            if ftype == 'selection' and fobj.selection:
                try:
                    sel = fobj.selection
                    if callable(sel):
                        sel = sel(model_obj)
                    sel_str = ', '.join("'%s'" % s[0] for s in sel[:20])
                    info += " [%s]" % sel_str
                except Exception:
                    pass
            if ftype in ('many2one', 'one2many', 'many2many'):
                info += " -> %s" % (fobj.comodel_name or '')
            fields_desc.append(info)
        return '\n'.join(fields_desc)

    def _extract_text_from_response(self, response_text, marker=None):
        """Extrait un texte depuis la réponse VLLM (bloc code ou texte brut)."""
        code_block = re.search(r'```(?:python)?\s*\n?(.*?)\n?```', response_text, re.DOTALL)
        if code_block:
            return code_block.group(1).strip()
        if marker:
            list_match = re.search(r'(\[.*\])', response_text, re.DOTALL)
            if list_match:
                return list_match.group(1).strip()
        return response_text.strip()

    def _validate_domain(self, domain_str):
        """Valide qu'une chaîne est un domaine Odoo valide."""
        eval_context = {
            'datetime': datetime,
            'context_today': datetime.datetime.now,
        }
        try:
            domain = safe_eval(domain_str, eval_context)
        except Exception as e:
            return {'valid': False, 'domain': domain_str, 'error': "Syntaxe invalide : %s" % str(e)}
        if not isinstance(domain, list):
            return {'valid': False, 'domain': domain_str, 'error': "Le domaine doit être une liste."}
        valid_operators = ('&', '|', '!')
        for item in domain:
            if isinstance(item, str):
                if item not in valid_operators:
                    return {'valid': False, 'domain': domain_str,
                            'error': "Opérateur invalide '%s'." % item}
            elif isinstance(item, (list, tuple)):
                if len(item) != 3:
                    return {'valid': False, 'domain': domain_str,
                            'error': "Chaque condition doit avoir 3 éléments. Trouvé : %s" % str(item)}
        return {'valid': True, 'domain': domain_str, 'error': ''}

    def _ask_vllm_for_model(self):
        """Demande à VLLM d'identifier le modèle Odoo correspondant à la question."""
        self.ensure_one()
        models_list = self._get_installed_models_list()
        system_prompt = (
            "Tu es un expert Odoo 16. On te donne une demande utilisateur et la liste des modèles Odoo installés. "
            "Tu dois identifier le modèle Odoo le plus pertinent pour répondre à la demande. "
            "Réponds UNIQUEMENT avec le nom technique du modèle (ex: account.move), sans aucune explication."
        )
        prompt = (
            "Demande de l'utilisateur :\n%s\n\n"
            "Liste des modèles Odoo installés :\n%s\n\n"
            "Quel est le modèle Odoo le plus pertinent ?"
        ) % (self.question, models_list)

        vllm = self.env['is.vllm']
        result = vllm.vllm_send_prompt(prompt, system_prompt=system_prompt)
        return result

    def _ask_vllm_for_domain(self, model_name):
        """Demande à VLLM de générer un domaine Odoo pour le modèle identifié."""
        self.ensure_one()
        fields_desc = self._get_model_fields_description(model_name)
        system_prompt = (
            "Tu es un expert Odoo 16. Tu dois générer un domaine Odoo (domain) valide "
            "en format Python (liste de tuples). "
            "Réponds UNIQUEMENT avec le domaine entre ```python et ```, sans aucune explication. "
            "Un domaine Odoo est une liste de tuples (champ, opérateur, valeur). "
            "Les opérateurs valides sont : =, !=, >, >=, <, <=, like, ilike, in, not in, "
            "child_of, parent_of, =like, =ilike, not like, not ilike. "
            "Les opérateurs logiques sont : '&' (ET, par défaut), '|' (OU), '!' (NON). "
            "Utilise UNIQUEMENT des champs qui existent dans le modèle. "
            "IMPORTANT pour les champs many2one (relationnels) : "
            "- Pour filtrer sur un champ many2one par son nom/libellé, utilise TOUJOURS "
            "  l'opérateur 'ilike' directement sur le champ (ex: ('client_id', 'ilike', 'sefam')). "
            "  Odoo résout automatiquement le name_search sur les many2one avec ilike. "
            "- NE JAMAIS utiliser '=' avec une valeur texte sur un many2one. "
            "- '=' sur un many2one attend un ID numérique. "
            "Pour les dates, utilise UNIQUEMENT datetime.datetime et datetime.timedelta. "
            "La date du jour est : %s. "
            "IMPORTANT pour les calculs de dates : "
            "- Premier jour du mois courant : datetime.datetime.now().strftime('%%Y-%%m-01') "
            "- Pour le mois courant, utilise la technique du premier jour du mois suivant avec < : "
            "  ('date', '>=', datetime.datetime.now().strftime('%%Y-%%m-01')), "
            "  ('date', '<', (datetime.datetime.now().replace(day=1) + datetime.timedelta(days=32)).replace(day=1).strftime('%%Y-%%m-%%d')) "
            "- NE JAMAIS utiliser un jour fixe comme 28, 30 ou 31 pour le dernier jour du mois. "
            "- NE PAS utiliser le module calendar, il n'est pas disponible. "
            "- SEULS datetime.datetime, datetime.timedelta et datetime.date sont disponibles."
        ) % fields.Date.today().strftime('%Y-%m-%d')
        prompt = (
            "Modèle Odoo : %s\n\n"
            "Champs disponibles dans ce modèle :\n%s\n\n"
            "Demande de l'utilisateur :\n%s\n\n"
            "Génère le domaine Odoo correspondant à cette demande."
        ) % (model_name, fields_desc, self.question)

        vllm = self.env['is.vllm']
        result = vllm.vllm_send_prompt(prompt, system_prompt=system_prompt)
        return result

    def action_search(self):
        """Lance la recherche : identifie le modèle puis génère le domaine."""
        self.ensure_one()
        if not self.question:
            raise UserError("Veuillez saisir une recherche.")

        start = time.time()
        model_name = None

        # Étape 1 : Identifier le modèle
        if self.model_id:
            model_name = self.model_id.model
            self.vllm_model_response = "Modèle sélectionné manuellement : %s" % model_name
        else:
            result = self._ask_vllm_for_model()
            if not result['success']:
                raise UserError("Erreur VLLM (identification modèle) : %s" % result['error'])

            response = result['response'].strip()
            self.vllm_model_response = response

            # Nettoyer la réponse (enlever les éventuels retours à la ligne, espaces, etc.)
            candidate = response.split('\n')[0].strip().strip('`').strip()

            # Vérifier que le modèle existe
            model_rec = self.env['ir.model'].sudo().search([('model', '=', candidate)], limit=1)
            if not model_rec:
                raise UserError(
                    "VLLM a proposé le modèle '%s' mais il n'existe pas dans Odoo.\n\n"
                    "Réponse VLLM :\n%s\n\n"
                    "Vous pouvez sélectionner le modèle manuellement." % (candidate, response)
                )
            self.model_id = model_rec.id
            model_name = candidate

        # Étape 2 : Générer le domaine
        result = self._ask_vllm_for_domain(model_name)
        if not result['success']:
            raise UserError("Erreur VLLM (génération domaine) : %s" % result['error'])

        response = result['response']
        self.vllm_domain_response = response

        domain_str = self._extract_text_from_response(response, marker='[')
        if not domain_str:
            raise UserError(
                "VLLM n'a pas retourné de domaine valide.\n\n"
                "Réponse reçue :\n%s" % response
            )

        validation = self._validate_domain(domain_str)
        if not validation['valid']:
            raise UserError(
                "Le domaine proposé par VLLM n'est pas valide :\n%s\n\n"
                "Domaine proposé :\n%s" % (validation['error'], domain_str)
            )

        self.domain = validation['domain']
        elapsed = time.time() - start
        self.temps_reponse = round(elapsed, 1)

        # Compter les enregistrements
        count = self._count_results(model_name, self.domain)
        self.nb_results = count

        _logger.info("Recherche générale [%s] modèle=%s domaine=%s nb=%s", self.name, model_name, self.domain, count)

        if count == 0:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'is.search.general',
                'res_id': self.id,
                'view_mode': 'form',
                'target': 'current',
            }

        # Étape 3 : Ouvrir la vue liste avec le domaine calculé
        return self._open_result_list(model_name, self.domain)

    def action_open_results(self):
        """Ré-ouvre les résultats avec le domaine déjà calculé."""
        self.ensure_one()
        if not self.model_name or not self.domain:
            raise UserError("Lancez d'abord une recherche.")
        return self._open_result_list(self.model_name, self.domain)

    def action_save_as_filter(self):
        """Enregistre le domaine calculé comme favori (ir.filters) pour l'utilisateur."""
        self.ensure_one()
        if not self.model_name or not self.domain:
            raise UserError("Lancez d'abord une recherche.")

        # Chercher l'action principale du modèle pour l'associer au filtre
        action = self.env['ir.actions.act_window'].sudo().search([
            ('res_model', '=', self.model_name),
            ('view_mode', 'ilike', 'tree'),
        ], limit=1)

        self.env['ir.filters'].create({
            'name': self.question[:80] if self.question else 'Recherche générale',
            'model_id': self.model_name,
            'domain': self.domain,
            'user_id': self.env.uid,
            'action_id': action.id if action else False,
            'is_default': False,
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Favori enregistré',
                'message': 'Le filtre a été ajouté à vos favoris.',
                'type': 'success',
                'sticky': False,
            },
        }

    def _count_results(self, model_name, domain_str):
        """Compte le nombre d'enregistrements correspondant au domaine."""
        try:
            domain = safe_eval(domain_str, {
                'datetime': datetime,
                'context_today': datetime.datetime.now,
            })
            return self.env[model_name].sudo().search_count(domain)
        except Exception:
            return 0

    def _open_result_list(self, model_name, domain_str):
        """Ouvre la vue liste du modèle avec le domaine donné."""
        try:
            domain = safe_eval(domain_str, {
                'datetime': datetime,
                'context_today': datetime.datetime.now,
            })
        except Exception as e:
            raise UserError("Erreur lors de l'évaluation du domaine :\n%s\n\n%s" % (domain_str, str(e)))

        return {
            'type': 'ir.actions.act_window',
            'name': 'Résultats : %s' % self.question[:80],
            'res_model': model_name,
            'view_mode': 'tree,form',
            'domain': domain,
            'target': 'current',
        }
