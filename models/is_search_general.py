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
    _order = 'model_id, question'
    _rec_name = "question"

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
        string='Domaine',
        tracking=True,
        copy=False,
        help="Domaine Odoo pour filtrer les résultats. "
             "Vous pouvez le modifier manuellement si nécessaire.",
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
    view_type = fields.Selection(
        [
            ('tree', 'Liste'),
            ('graph', 'Graphique'),
            ('pivot', 'Tableau croisé'),
        ],
        string='Type de vue',
        help="Type de vue à afficher pour les résultats. "
             "Si vide, l'IA déterminera le type le plus approprié.",
    )
    vllm_view_type_response = fields.Text(
        string='Réponse VLLM (type de vue)',
        readonly=True,
        copy=False,
    )
    group_by = fields.Char(
        string='Regroupement (group_by)',
        help="Champ(s) de regroupement pour les vues graphique et pivot. "
             "Ex: create_date:year pour regrouper par année. "
             "Si vide et type de vue = graph/pivot, l'IA le déterminera.",
    )
    vllm_group_by_response = fields.Text(
        string='Réponse VLLM (group_by)',
        readonly=True,
        copy=False,
    )
    measure = fields.Char(
        string='Mesure',
        help="Champ numérique utilisé comme mesure pour les vues graphique et pivot. "
             "Utiliser '__count' pour le nombre d'enregistrements. "
             "Si vide et type de vue = graph/pivot, l'IA le déterminera.",
    )
    vllm_measure_response = fields.Text(
        string='Réponse VLLM (mesure)',
        readonly=True,
        copy=False,
    )
    filter_id = fields.Many2one(
        'ir.filters',
        string='Favori associé',
        readonly=True,
        copy=False,
        help="Lien vers le favori créé à partir de cette recherche.",
    )

    @api.onchange('model_id')
    def _onchange_model_id(self):
        self.domain = False
        self.group_by = False
        self.measure = False
        self.view_type = False
        self.nb_results = 0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'Nouveau') == 'Nouveau':
                vals['name'] = self.env['ir.sequence'].next_by_code('is.search.general') or 'Nouveau'
        return super().create(vals_list)

    def read(self, fields=None, load='_classic_read'):
        result = super().read(fields=fields, load=load)
        if fields and 'domain' not in fields:
            return result
        for record in result:
            domain_str = record.get('domain')
            if not domain_str:
                continue
            # Récupérer le model_name depuis l'enregistrement ou la base
            model_name = record.get('model_name')
            if not model_name:
                rec = self.browse(record['id'])
                model_name = rec.model_name
            if not model_name or model_name not in self.env:
                continue
            try:
                domain = safe_eval(domain_str, {'datetime': datetime})
                self.env[model_name].search_count(domain)
            except Exception:
                _logger.warning(
                    "Domaine invalide pour is.search.general id=%s (modèle=%s) : %s",
                    record['id'], model_name, domain_str,
                )
                record['domain'] = False
        return result

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
            "- SEULS datetime.datetime, datetime.timedelta et datetime.date sont disponibles. "
            "IMPORTANT pour les graphiques et analyses par période : "
            "- Si la demande est 'graphique par année', 'graphique par mois', 'tableau par trimestre', etc., "
            "  cela signifie qu'on veut TOUS les enregistrements (toutes les années, tous les mois...), "
            "  PAS SEULEMENT la période en cours. Le regroupement se fait dans la vue, pas dans le domaine. "
            "- Dans ce cas, utilise un domaine simple comme [('create_date', '!=', False)] "
            "  pour récupérer tous les enregistrements avec une date valide. "
            "- N'ajoute des filtres de date que si la demande mentionne explicitement une période spécifique "
            "  (ex: 'ce mois', 'cette année', 'dernier trimestre', 'depuis 2019', 'depuis janvier', etc.). "
         ) % fields.Date.today().strftime('%Y-%m-%d')

        #    "IMPORTANT pour 'depuis XXXX' : "
        #     "- 'depuis 2019' signifie >= '2019-01-01' (premier jour de 2019) "
        #     "- 'depuis janvier' signifie >= 'XXXX-01-01' (premier jour de janvier de l'année en cours) "
        #     "- 'depuis le 15 mars' signifie >= 'XXXX-03-15' "
        #     "- Attention : 'depuis 2019' NE signifie PAS >= '2018-01-01', utilise l'année exacte mentionnée."




        prompt = (
            "Modèle Odoo : %s\n\n"
            "Champs disponibles dans ce modèle :\n%s\n\n"
            "Demande de l'utilisateur :\n%s\n\n"
            "Génère le domaine Odoo correspondant à cette demande."
        ) % (model_name, fields_desc, self.question)

        vllm = self.env['is.vllm']
        result = vllm.vllm_send_prompt(prompt, system_prompt=system_prompt)
        return result

    def _ask_vllm_for_view_type(self):
        """Demande à VLLM d'identifier le type de vue le plus approprié pour la question."""
        self.ensure_one()
        system_prompt = (
            "Tu es un expert Odoo 16. On te donne une demande utilisateur et tu dois déterminer "
            "le type de vue le plus approprié pour afficher les résultats. "
            "Tu as le choix entre : "
            "- 'tree' : pour afficher une liste détaillée d'enregistrements "
            "- 'graph' : pour afficher des graphiques/statistiques (barres, lignes, courbes) "
            "- 'pivot' : pour afficher un tableau croisé dynamique avec des regroupements et analyses "
            "Réponds UNIQUEMENT avec l'un de ces mots : tree, graph ou pivot, sans aucune explication."
        )
        prompt = (
            "Demande de l'utilisateur :\n%s\n\n"
            "Quel type de vue est le plus approprié pour afficher ces résultats ?"
        ) % self.question

        vllm = self.env['is.vllm']
        result = vllm.vllm_send_prompt(prompt, system_prompt=system_prompt)
        return result

    def _ask_vllm_for_measure(self, model_name):
        """Demande à VLLM quelle mesure utiliser pour les vues graph/pivot."""
        self.ensure_one()
        fields_desc = self._get_model_fields_description(model_name)
        system_prompt = (
            "Tu es un expert Odoo 16. On te donne une demande utilisateur pour un graphique ou tableau croisé. "
            "Tu dois déterminer la mesure (valeur affichée) la plus appropriée. "
            "Règles : "
            "- Si l'utilisateur veut un NOMBRE, un COMPTAGE ou une QUANTITÉ de références, réponds '__count'. "
            "- Sinon, réponds avec le nom technique du champ numérique le plus pertinent. "
            "- Ne réponds qu'avec le nom du champ (ex: dao_ca, __count, amount_total), sans aucune explication."
        )
        prompt = (
            "Modèle Odoo : %s\n\n"
            "Champs disponibles :\n%s\n\n"
            "Demande de l'utilisateur :\n%s\n\n"
            "Quel champ utiliser comme mesure ?"
        ) % (model_name, fields_desc, self.question)
        vllm = self.env['is.vllm']
        return vllm.vllm_send_prompt(prompt, system_prompt=system_prompt)

    def _ask_vllm_for_group_by(self, model_name):
        """Demande à VLLM de déterminer le regroupement approprié pour les vues graph/pivot."""
        self.ensure_one()
        fields_desc = self._get_model_fields_description(model_name)

        if self.view_type == 'pivot':
            system_prompt = (
                "Tu es un expert Odoo 16. On te donne une demande utilisateur pour un tableau croisé (pivot). "
                "Tu dois déterminer les champs de regroupement en LIGNES et en COLONNES. "
                "Règles importantes : "
                "- Les mots 'en ligne', 'par ligne', 'en lignes' indiquent le champ pour les LIGNES (row). "
                "- Les mots 'en colonne', 'par colonne', 'en colonnes' indiquent le champ pour les COLONNES (col). "
                "- Utilise uniquement des noms de champs existants dans le modèle fourni. "
                "- Format pour les dates : 'champ:year', 'champ:month', 'champ:quarter'. "
                "- Pour les autres champs (selection, many2one, char), utilise juste le nom du champ. "
                "Réponds UNIQUEMENT sur deux lignes, exactement dans ce format (sans aucune explication) :\n"
                "row: nom_du_champ_ligne\n"
                "col: nom_du_champ_colonne"
            )
            prompt = (
                "Modèle Odoo : %s\n\n"
                "Champs disponibles dans ce modèle :\n%s\n\n"
                "Demande de l'utilisateur :\n%s\n\n"
                "Quels champs utiliser pour les lignes (row) et les colonnes (col) du tableau croisé ?"
            ) % (model_name, fields_desc, self.question)
        else:
            system_prompt = (
                "Tu es un expert Odoo 16. On te donne une demande utilisateur pour un graphique. "
                "Tu dois déterminer le ou les champs de regroupement (group_by) les plus appropriés. "
                "Format pour les dates (très important) : "
                "- 'champ:year' pour regrouper par année "
                "- 'champ:quarter' pour regrouper par trimestre "
                "- 'champ:month' pour regrouper par mois "
                "- 'champ:week' pour regrouper par semaine "
                "- 'champ:day' pour regrouper par jour "
                "Pour les autres champs (many2one, selection, char), utilise juste le nom du champ. "
                "Si un seul regroupement, réponds avec un seul nom. "
                "Si deux regroupements, sépare-les par une virgule (ex: dao_motif, dao_typeclient). "
                "Si aucun regroupement n'est nécessaire, réponds 'none'. "
                "Réponds UNIQUEMENT avec le(s) nom(s) de champ(s), sans aucune explication."
            )
            prompt = (
                "Modèle Odoo : %s\n\n"
                "Champs disponibles dans ce modèle :\n%s\n\n"
                "Demande de l'utilisateur :\n%s\n\n"
                "Quel(s) champ(s) utiliser pour le regroupement (group_by) ?"
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

        # Étape 3 : Déterminer le type de vue si non renseigné
        if not self.view_type:
            result = self._ask_vllm_for_view_type()
            if result['success']:
                response = result['response'].strip()
                self.vllm_view_type_response = response
                # Nettoyer la réponse
                view_type = response.split('\n')[0].strip().strip('`').strip().lower()
                if view_type in ('tree', 'graph', 'pivot'):
                    self.view_type = view_type
                else:
                    # Par défaut, utiliser tree
                    self.view_type = 'tree'
            else:
                # En cas d'erreur, utiliser tree par défaut
                self.view_type = 'tree'

        # Étape 4 : Déterminer la mesure si nécessaire (pour graph/pivot)
        if self.view_type in ('graph', 'pivot') and not self.measure:
            # Détection rapide par mots-clés avant d'interroger VLLM
            question_lower = self.question.lower()
            count_keywords = ['nombre', 'combien', 'comptage', 'count', 'quantité de', 'nb de', 'nb d\'', 'nombre de', 'nombre d\'']
            if any(kw in question_lower for kw in count_keywords):
                self.measure = '__count'
            else:
                result = self._ask_vllm_for_measure(model_name)
                if result['success']:
                    response = result['response'].strip()
                    self.vllm_measure_response = response
                    measure = response.split('\n')[0].strip().strip('`').strip().lower()
                    if measure:
                        self.measure = measure

        # Étape 5 : Déterminer le group_by si nécessaire (pour graph/pivot)
        if self.view_type in ('graph', 'pivot') and not self.group_by:
            result = self._ask_vllm_for_group_by(model_name)
            if result['success']:
                response = result['response'].strip()
                self.vllm_group_by_response = response
                if self.view_type == 'pivot':
                    # Parser le format "row: xxx\ncol: yyy"
                    row_match = re.search(r'row\s*:\s*(\S+)', response, re.IGNORECASE)
                    col_match = re.search(r'col\s*:\s*(\S+)', response, re.IGNORECASE)
                    row_gb = row_match.group(1).strip().lower() if row_match else None
                    col_gb = col_match.group(1).strip().lower() if col_match else None
                    parts = [g for g in [row_gb, col_gb] if g and g != 'none']
                    if parts:
                        self.group_by = ', '.join(parts)
                else:
                    # Nettoyer la réponse (graphique simple)
                    group_by = response.split('\n')[0].strip().strip('`').strip().lower()
                    if group_by and group_by != 'none':
                        self.group_by = group_by

        _logger.info("Recherche générale [%s] modèle=%s domaine=%s nb=%s view_type=%s group_by=%s measure=%s", 
                     self.name, model_name, self.domain, count, self.view_type, self.group_by, self.measure)

        if count == 0:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'is.search.general',
                'res_id': self.id,
                'view_mode': 'form',
                'target': 'current',
            }

        # Étape 6 : Ouvrir la vue appropriée avec le domaine calculé
        return self._open_result_list(model_name, self.domain, self.view_type, self.group_by, self.measure)

    def action_open_results(self):
        """Ré-ouvre les résultats avec le domaine déjà calculé."""
        self.ensure_one()
        if not self.model_name or not self.domain:
            raise UserError("Lancez d'abord une recherche.")
        
        # Recalculer le nombre d'enregistrements (au cas où le domaine a été modifié)
        count = self._count_results(self.model_name, self.domain)
        self.nb_results = count
        
        # Utiliser le view_type enregistré, ou tree par défaut
        view_type = self.view_type or 'tree'
        return self._open_result_list(self.model_name, self.domain, view_type, self.group_by, self.measure)

    def action_recalculate_domain(self):
        """Recalcule le domaine et le group_by en conservant le modèle identifié."""
        self.ensure_one()
        if not self.model_name:
            raise UserError("Veuillez d'abord identifier le modèle (bouton Rechercher).")

        start = time.time()
        model_name = self.model_name

        # Étape 1 : Générer le domaine
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
        
        # Étape 2 : Recalculer le group_by si view_type est graph/pivot
        if self.view_type in ('graph', 'pivot'):
            result = self._ask_vllm_for_group_by(model_name)
            if result['success']:
                response = result['response'].strip()
                self.vllm_group_by_response = response
                # Nettoyer la réponse
                group_by = response.split('\n')[0].strip().strip('`').strip().lower()
                if group_by and group_by != 'none':
                    self.group_by = group_by
        
        # Compter les enregistrements
        count = self._count_results(model_name, self.domain)
        self.nb_results = count
        
        elapsed = time.time() - start
        self.temps_reponse = round(elapsed, 1)

        _logger.info("Domaine recalculé [%s] domaine=%s nb=%s group_by=%s", 
                     self.name, self.domain, count, self.group_by)

        # Retourner une action pour recharger le formulaire et afficher les changements
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'is.search.general',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'form_view_initial_mode': 'edit',
            },
            'flags': {
                'mode': 'edit',
            },
        }

    def action_save_as_filter(self):
        """Enregistre ou met à jour le domaine calculé comme favori (ir.filters) pour l'utilisateur."""
        self.ensure_one()
        if not self.model_name or not self.domain:
            raise UserError("Lancez d'abord une recherche.")

        filter_vals = {
            'name': self.question[:80] if self.question else 'Recherche générale',
            'model_id': self.model_name,
            'domain': self.domain,
            'user_id': self.env.uid,
            'action_id': False,
            'is_default': False,
        }

        if self.filter_id:
            # Mettre à jour le filtre existant
            self.filter_id.sudo().write(filter_vals)
            message = 'Le favori a été mis à jour.'
            title = 'Favori mis à jour'
        else:
            # Créer un nouveau filtre
            new_filter = self.env['ir.filters'].sudo().create(filter_vals)
            self.filter_id = new_filter.id
            message = 'Le filtre a été ajouté à vos favoris.'
            title = 'Favori enregistré'

        # Invalider le cache pour forcer le rechargement
        self.invalidate_recordset(['filter_id'])

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': 'success',
                'sticky': False,
                'next': {
                    'type': 'ir.actions.act_window',
                    'res_model': 'is.search.general',
                    'res_id': self.id,
                    'view_mode': 'form',
                    'target': 'current',
                    'views': [(False, 'form')],
                },
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

    def _validate_group_by(self, model_name, gb_list):
        """Filtre la liste group_by : supprime les champs inexistants et retire la granularité sur les non-dates."""
        DATE_GRANULARITIES = ('year', 'quarter', 'month', 'week', 'day')
        DATE_TYPES = ('date', 'datetime')
        try:
            model_obj = self.env[model_name]
        except KeyError:
            return gb_list
        result = []
        for gb in gb_list:
            if ':' in gb:
                fname, granularity = gb.split(':', 1)
                if granularity not in DATE_GRANULARITIES:
                    fname_clean = fname
                else:
                    fname_clean = fname
                field = model_obj._fields.get(fname_clean)
                if field is None:
                    _logger.warning("group_by: champ '%s' inexistant sur %s, ignoré", fname_clean, model_name)
                    continue
                if field.type not in DATE_TYPES:
                    _logger.warning("group_by: '%s' n'est pas date/datetime sur %s, granularité supprimée", fname_clean, model_name)
                    result.append(fname_clean)
                else:
                    result.append(gb)
            else:
                field = model_obj._fields.get(gb)
                if field is None:
                    _logger.warning("group_by: champ '%s' inexistant sur %s, ignoré", gb, model_name)
                    continue
                result.append(gb)
        return result

    def _open_result_list(self, model_name, domain_str, view_type='tree', group_by=None, measure=None):
        """Ouvre la vue du modèle avec le domaine donné et le type de vue approprié."""
        try:
            domain = safe_eval(domain_str, {
                'datetime': datetime,
                'context_today': datetime.datetime.now,
            })
        except Exception as e:
            raise UserError("Erreur lors de l'évaluation du domaine :\n%s\n\n%s" % (domain_str, str(e)))

        # Déterminer le view_mode en fonction du type de vue
        if view_type == 'graph':
            view_mode = 'graph,tree,form'
        elif view_type == 'pivot':
            view_mode = 'pivot,tree,form'
        else:  # tree par défaut
            view_mode = 'tree,form'

        action = {
            'type': 'ir.actions.act_window',
            'name': 'Résultats : %s' % self.question[:80],
            'res_model': model_name,
            'view_mode': view_mode,
            'domain': domain,
            'target': 'current',
        }

        # Pour les vues pivot et graph, ajouter un contexte avec le group_by et la mesure
        if view_type in ('graph', 'pivot'):
            context = {}
            if group_by:
                gb_list = group_by if isinstance(group_by, list) else [g.strip() for g in group_by.split(',') if g.strip()]
                gb_list = self._validate_group_by(model_name, gb_list)
                if view_type == 'pivot' and len(gb_list) >= 2:
                    context['pivot_row_groupby'] = [gb_list[0]]
                    context['pivot_column_groupby'] = [gb_list[1]]
                elif view_type == 'pivot' and len(gb_list) == 1:
                    context['pivot_row_groupby'] = [gb_list[0]]
                else:
                    context['group_by'] = gb_list
            if measure:
                if view_type == 'pivot':
                    context['pivot_measures'] = [measure]
                else:
                    context['graph_measure'] = measure
            action['context'] = context

        return action
