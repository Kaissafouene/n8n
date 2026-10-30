"""Planificateur : transforme une demande en français en workflow JSON validé.

Le modèle ne peut utiliser que les actions de actions.ACTIONS. Chaque plan est vérifié ;
en cas d'erreur, les erreurs sont renvoyées au modèle pour correction (3 tentatives max).
"""
import json

import actions

MAX_TENTATIVES = 3
MAX_ETAPES = 8
TYPES = {"texte": str, "objet": dict, "liste": list, "booleen": bool}
BESOIN_ENTREE = {"analyser_ia", "transformer", "ecrire_fichier"}


class PlanInvalide(Exception):
    def __init__(self, journal):
        super().__init__("Le modèle n'a pas produit de plan valide après "
                         f"{MAX_TENTATIVES} tentatives.")
        self.journal = journal


def catalogue():
    lignes = []
    for nom, a in actions.ACTIONS.items():
        lignes.append(f"\n### {nom} — {a['libelle']}\n{a['description']}\nParamètres :")
        for p, d in a["parametres"].items():
            lignes.append(f"  - {p} ({d['type']}, {'obligatoire' if d['requis'] else 'optionnel'}) : {d['aide']}")
    lignes.append("\nOpérations possibles pour 'transformer' (agrégations : somme, moyenne, nombre, min, max ; "
                  "conditions et formules en syntaxe pandas, colonnes avec espaces entre `backticks`) :")
    for nom, o in actions.OPERATIONS.items():
        lignes.append(f"  - {json.dumps(o['exemple'], ensure_ascii=False)}")
    return "\n".join(lignes)


def prompt_systeme():
    return f"""Tu es le planificateur de FlowGen Mini, un outil d'automatisation.
Tu découpes la demande de l'utilisateur en étapes. Chaque étape utilise UNE action du catalogue.
Tu n'as JAMAIS le droit d'utiliser une action qui n'est pas dans ce catalogue.

CATALOGUE DES ACTIONS :
{catalogue()}

RÈGLES :
- Les données passent d'une étape à la suivante. Une étape peut lire la sortie d'une étape plus ancienne
  avec le champ "entree": "<id de l'étape>".
- Pour "generer_texte" sans pour_chaque_ligne, "entree" peut être une LISTE d'ids pour combiner plusieurs
  sorties en un seul texte (ex : le cours + le travail de l'étudiant).
- L'utilisateur n'a pas besoin d'avoir uploadé ses fichiers à l'avance : si un fichier nécessaire est absent,
  utilise quand même "lire_fichier" avec un motif précis et un paramètre "demande" clair. Le workflow se mettra
  en pause et demandera le fichier au bon moment. Utilise une étape "lire_fichier" par document distinct
  (ex : le cours au début, puis le travail de l'étudiant plus tard).
- La sortie de "ecrire_fichier" n'est pas une donnée : l'étape suivante reçoit encore la table précédente.
- Utilise les noms de colonnes exacts des fichiers uploadés, et les noms de champs créés par les étapes précédentes.
- {MAX_ETAPES} étapes maximum. Titres courts en français.

Réponds UNIQUEMENT avec ce JSON :
{{"titre": "titre court du workflow",
  "etapes": [{{"id": "etape_1", "action": "<nom d'action>", "titre": "...", "parametres": {{...}}}}, ...]}}"""


def valider_plan(plan):
    """Renvoie la liste des erreurs du plan (vide si le plan est valide)."""
    if not isinstance(plan, dict) or not isinstance(plan.get("etapes"), list) or not plan["etapes"]:
        return ["Le JSON doit contenir une liste non vide 'etapes'."]
    erreurs, ids = [], []
    if len(plan["etapes"]) > MAX_ETAPES:
        erreurs.append(f"Trop d'étapes ({len(plan['etapes'])}), maximum {MAX_ETAPES}.")
    for n, etape in enumerate(plan["etapes"], 1):
        if not isinstance(etape, dict):
            erreurs.append(f"Étape {n} : doit être un objet JSON.")
            continue
        eid, action = etape.get("id"), etape.get("action")
        nom = f"Étape {n} ({eid})"
        if not eid or eid in ids:
            erreurs.append(f"{nom} : 'id' manquant ou déjà utilisé.")
        if action not in actions.ACTIONS:
            erreurs.append(f"{nom} : l'action « {action} » n'existe pas. Actions autorisées : "
                           f"{', '.join(actions.ACTIONS)}.")
            ids.append(eid)
            continue
        entree = etape.get("entree")
        if isinstance(entree, list):
            if action != "generer_texte" or etape.get("parametres", {}).get("pour_chaque_ligne"):
                erreurs.append(f"{nom} : une liste dans 'entree' n'est possible que pour generer_texte "
                               "sans pour_chaque_ligne.")
            for i in entree:
                if i not in ids:
                    erreurs.append(f"{nom} : 'entree' doit désigner des étapes PRÉCÉDENTES (reçu « {i} »).")
        elif entree and entree not in ids:
            erreurs.append(f"{nom} : 'entree' doit désigner une étape PRÉCÉDENTE (reçu « {entree} »).")
        if action in BESOIN_ENTREE and n == 1:
            erreurs.append(f"{nom} : « {action} » a besoin de données, commence par lire_fichier ou appel_http.")

        params = etape.get("parametres")
        if not isinstance(params, dict):
            erreurs.append(f"{nom} : 'parametres' doit être un objet.")
            ids.append(eid)
            continue
        spec = actions.ACTIONS[action]["parametres"]
        avant = len(erreurs)
        for p in params:
            if p not in spec:
                erreurs.append(f"{nom} : paramètre inconnu « {p} » pour {action}. Paramètres possibles : {list(spec)}.")
        for p, d in spec.items():
            if p not in params or params[p] in (None, ""):
                if d["requis"]:
                    erreurs.append(f"{nom} : paramètre obligatoire « {p} » manquant.")
            elif not isinstance(params[p], TYPES[d["type"]]):
                erreurs.append(f"{nom} : « {p} » doit être de type {d['type']}.")
        if len(erreurs) == avant:
            erreurs += _verifier_details(nom, action, params)
        ids.append(eid)
    return erreurs


def _verifier_details(nom, action, p):
    erreurs = []
    if action == "analyser_ia":
        if not p["champs"] or not all(isinstance(v, str) for v in p["champs"].values()):
            erreurs.append(f"{nom} : 'champs' doit être un dictionnaire non vide {{nom: description texte}}.")
    if action == "transformer":
        for i, op in enumerate(p["operations"], 1):
            if not isinstance(op, dict) or op.get("type") not in actions.OPERATIONS:
                erreurs.append(f"{nom}, opération {i} : type inconnu. Types possibles : {list(actions.OPERATIONS)}.")
                continue
            for champ in actions.OPERATIONS[op["type"]]["requis"]:
                if champ not in op:
                    erreurs.append(f"{nom}, opération {i} ({op['type']}) : champ « {champ} » manquant.")
            if op["type"] == "regrouper" and isinstance(op.get("agregations"), dict):
                for f in op["agregations"].values():
                    if f not in actions.AGREGATIONS:
                        erreurs.append(f"{nom} : agrégation « {f} » inconnue ({list(actions.AGREGATIONS)}).")
    if action == "ecrire_fichier" and not p["nom_fichier"].lower().endswith((".xlsx", ".csv", ".txt")):
        erreurs.append(f"{nom} : nom_fichier doit finir par .xlsx, .csv ou .txt.")
    if action == "appel_http":
        if not p["url"].startswith(("http://", "https://")):
            erreurs.append(f"{nom} : l'URL doit commencer par http:// ou https://.")
        if p.get("methode", "GET").upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            erreurs.append(f"{nom} : méthode HTTP invalide.")
    return erreurs


def _boucle(messages):
    """Demande un plan au modèle, le valide, et renvoie les erreurs au modèle si besoin."""
    journal = []
    for tentative in range(1, MAX_TENTATIVES + 1):
        try:
            plan = actions.appeler_ia(messages)
            erreurs = valider_plan(plan)
        except json.JSONDecodeError:
            plan, erreurs = None, ["La réponse n'est pas un JSON valide."]
        journal.append({"tentative": tentative, "erreurs": erreurs})
        if not erreurs:
            return plan, journal
        messages += [
            {"role": "assistant", "content": json.dumps(plan, ensure_ascii=False) if plan else "(JSON invalide)"},
            {"role": "user", "content": "Ce plan est invalide :\n- " + "\n- ".join(erreurs)
                                        + "\nCorrige-le et renvoie le plan complet en JSON."},
        ]
    raise PlanInvalide(journal)


def generer_plan(demande):
    return _boucle([
        {"role": "system", "content": prompt_systeme()},
        {"role": "user", "content": f"Fichiers uploadés :\n{actions.description_fichiers()}\n\nDemande :\n{demande}"},
    ])


def reparer_plan(demande, plan, etape_id, erreur):
    """Renvoie au modèle une erreur survenue pendant l'exécution pour qu'il corrige le plan."""
    return _boucle([
        {"role": "system", "content": prompt_systeme()},
        {"role": "user", "content": f"Fichiers uploadés :\n{actions.description_fichiers()}\n\nDemande :\n{demande}"},
        {"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)},
        {"role": "user", "content": f"À l'exécution, l'étape « {etape_id} » a échoué avec l'erreur :\n{erreur}\n"
                                    "Corrige le plan et renvoie le plan complet en JSON."},
    ])
