"""Bibliothèque d'actions de FlowGen Mini + exécuteur pas à pas.

Les données qui circulent entre les blocs sont toujours :
  - une table  : liste de dictionnaires (une ligne = un dict)
  - un texte   : chaîne de caractères
  - un fichier : dict {"fichier", "url", "lignes"} (sortie de "ecrire_fichier")
"""
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatch
from pathlib import Path

import pandas as pd
import pdfplumber
import requests
from openai import OpenAI

DOSSIER = Path(__file__).parent
ENTREES = DOSSIER / "espace" / "entrees"   # fichiers uploadés
ETAPES = ENTREES / "etapes"                # fichiers déposés pendant l'exécution, un dossier par étape
SORTIES = DOSSIER / "espace" / "sorties"   # fichiers générés
ENTREES.mkdir(parents=True, exist_ok=True)
SORTIES.mkdir(parents=True, exist_ok=True)

EXT_TABLE = {".csv", ".xlsx", ".xls"}
EXT_TEXTE = {".pdf", ".txt", ".md"}

# --------------------------------------------------------------------------
# Catalogue : la SEULE liste d'actions que l'IA a le droit d'utiliser
# --------------------------------------------------------------------------
ACTIONS = {
    "lire_fichier": {
        "libelle": "Lire un fichier", "icone": "📄",
        "description": "Lit un ou plusieurs fichiers. CSV/Excel -> une ligne par ligne du tableur. "
                       "PDF/texte -> une ligne par fichier avec les colonnes 'fichier' et 'texte'. "
                       "Si aucun fichier ne correspond, le workflow se met en pause et demande le fichier "
                       "à l'utilisateur (message 'demande').",
        "parametres": {
            "fichiers": {"type": "texte", "requis": True,
                         "aide": "nom exact d'un fichier uploadé ou motif, ex : 'cv_*.pdf', '*.csv', 'cours*'"},
            "demande": {"type": "texte", "requis": False,
                        "aide": "message affiché si le fichier doit être demandé, ex : 'Déposez votre cours (PDF)'"},
        },
    },
    "analyser_ia": {
        "libelle": "Analyser avec l'IA", "icone": "🧠",
        "description": "Pour CHAQUE ligne de la table d'entrée, l'IA produit des champs structurés "
                       "(résumer, extraire, classer, scorer). Les champs sont ajoutés comme nouvelles colonnes "
                       "(la longue colonne 'texte' est retirée de la sortie).",
        "parametres": {
            "instruction": {"type": "texte", "requis": True, "aide": "ce que l'IA doit faire pour chaque ligne"},
            "champs": {"type": "objet", "requis": True,
                       "aide": "dictionnaire {nom_colonne: description}, ex : {\"categorie\": \"Alimentation, Transport...\"}"},
            "contexte": {"type": "texte", "requis": False, "aide": "informations supplémentaires utiles (optionnel)"},
            "fichier_contexte": {"type": "texte", "requis": False,
                                 "aide": "nom d'un fichier DÉJÀ uploadé dont le texte sert de référence (ex : une offre d'emploi)"},
        },
    },
    "transformer": {
        "libelle": "Transformer les données", "icone": "🔧",
        "description": "Applique une liste d'opérations sur la table : filtrer, calculer, regrouper, trier, "
                       "selectionner, renommer, limiter.",
        "parametres": {
            "operations": {"type": "liste", "requis": True, "aide": "liste d'opérations, voir le format ci-dessous"},
        },
    },
    "ecrire_fichier": {
        "libelle": "Écrire un fichier", "icone": "💾",
        "description": "Écrit la table en Excel (.xlsx) ou CSV (.csv), ou un texte en .txt, téléchargeable. "
                       "Avec 'feuille_par', crée une feuille Excel par valeur de la colonne + une feuille 'Synthèse'.",
        "parametres": {
            "nom_fichier": {"type": "texte", "requis": True, "aide": "ex : 'rapport.xlsx', 'resultats.csv', 'rapport.txt'"},
            "feuille_par": {"type": "texte", "requis": False, "aide": "colonne pour créer une feuille par valeur (xlsx uniquement)"},
        },
    },
    "appel_http": {
        "libelle": "Appel HTTP", "icone": "🌐",
        "description": "Appelle une API externe. Une réponse JSON liste -> table ; objet -> une ligne ; sinon texte.",
        "parametres": {
            "url": {"type": "texte", "requis": True, "aide": "URL complète (https://...)"},
            "methode": {"type": "texte", "requis": False, "aide": "GET (défaut), POST, PUT, PATCH, DELETE"},
            "en_tetes": {"type": "objet", "requis": False, "aide": "en-têtes HTTP"},
            "corps": {"type": "objet", "requis": False, "aide": "corps JSON (POST/PUT/PATCH)"},
            "chemin": {"type": "texte", "requis": False, "aide": "chemin dans la réponse JSON, ex : 'data.items'"},
        },
    },
    "generer_texte": {
        "libelle": "Générer un texte", "icone": "✍️",
        "description": "Rédige un texte avec l'IA. Si pour_chaque_ligne=true : un texte par ligne (ex : un email "
                       "par étudiant) ajouté dans la colonne 'colonne_sortie'. Sinon : un seul texte (ex : rapport) "
                       "à partir de toutes les données.",
        "parametres": {
            "instruction": {"type": "texte", "requis": True, "aide": "consigne de rédaction"},
            "pour_chaque_ligne": {"type": "booleen", "requis": False, "aide": "true = un texte par ligne"},
            "colonne_sortie": {"type": "texte", "requis": False, "aide": "nom de la colonne créée (défaut : 'texte_genere')"},
        },
    },
}

OPERATIONS = {
    "filtrer": {"requis": ["condition"], "exemple": {"type": "filtrer", "condition": "montant < 0"}},
    "calculer": {"requis": ["colonne", "formule"], "exemple": {"type": "calculer", "colonne": "montant_abs", "formule": "abs(montant)"}},
    "regrouper": {"requis": ["par", "agregations"],
                  "exemple": {"type": "regrouper", "par": ["categorie"], "agregations": {"montant": "somme"}}},
    "trier": {"requis": ["par"], "exemple": {"type": "trier", "par": "score", "ordre": "decroissant"}},
    "selectionner": {"requis": ["colonnes"], "exemple": {"type": "selectionner", "colonnes": ["nom", "score"]}},
    "renommer": {"requis": ["colonnes"], "exemple": {"type": "renommer", "colonnes": {"ancien": "nouveau"}}},
    "limiter": {"requis": ["n"], "exemple": {"type": "limiter", "n": 10}},
}
AGREGATIONS = {"somme": "sum", "moyenne": "mean", "nombre": "count", "min": "min", "max": "max"}


# --------------------------------------------------------------------------
# Appel au modèle OpenAI
# --------------------------------------------------------------------------
# Gemini est appelé via son API compatible OpenAI : le même code sert aux deux fournisseurs.
FOURNISSEURS = {
    "openai": {"cle": "OPENAI_API_KEY", "modele": "OPENAI_MODEL", "defaut": "gpt-4o-mini", "url": None},
    "gemini": {"cle": "GEMINI_API_KEY", "modele": "GEMINI_MODEL", "defaut": "gemini-2.5-flash",
               "url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
}


def fournisseur():
    nom = os.getenv("FOURNISSEUR_IA", "openai").strip().lower()
    return nom if nom in FOURNISSEURS else "openai"


def modele():
    f = FOURNISSEURS[fournisseur()]
    return os.getenv(f["modele"]) or f["defaut"]


def cle_configuree():
    cle = os.getenv(FOURNISSEURS[fournisseur()]["cle"], "")
    return bool(cle) and "votre" not in cle


def appeler_ia(messages, json_mode=True):
    """Envoie les messages au modèle. Renvoie un dict (json_mode) ou du texte."""
    f = FOURNISSEURS[fournisseur()]
    if not cle_configuree():
        raise RuntimeError(f"Clé manquante : renseignez {f['cle']} dans le fichier .env.")
    client = OpenAI(api_key=os.getenv(f["cle"]), base_url=f["url"])
    options = {"response_format": {"type": "json_object"}} if json_mode else {}
    reponse = client.chat.completions.create(model=modele(), messages=messages, **options)
    contenu = reponse.choices[0].message.content or ""
    return json.loads(contenu) if json_mode else contenu.strip()


# --------------------------------------------------------------------------
# Outils
# --------------------------------------------------------------------------
def _nombres(df):
    """Convertit en nombres les colonnes qui en contiennent (gère '1 234,56')."""
    for col in df.columns:
        if not (df[col].dtype == object or pd.api.types.is_string_dtype(df[col])):
            continue
        valeurs = df[col].dropna().astype(str).str.strip()
        valeurs = valeurs[valeurs != ""]
        if valeurs.empty:
            continue
        nettoye = valeurs.str.replace(" ", "").str.replace(" ", "").str.replace(",", ".")
        converti = pd.to_numeric(nettoye, errors="coerce")
        if converti.notna().all():
            df[col] = pd.to_numeric(
                df[col].astype(str).str.strip().str.replace(" ", "").str.replace(" ", "").str.replace(",", "."),
                errors="coerce")
    return df


def _propre(valeur):
    if isinstance(valeur, float) and pd.isna(valeur):
        return None
    if isinstance(valeur, pd.Timestamp):
        return valeur.strftime("%Y-%m-%d")
    if hasattr(valeur, "item"):  # types numpy
        return valeur.item()
    if isinstance(valeur, list):
        return ", ".join(str(v) for v in valeur)
    if isinstance(valeur, dict):
        return json.dumps(valeur, ensure_ascii=False)
    return valeur


def _vers_lignes(df):
    return [{k: _propre(v) for k, v in ligne.items()} for ligne in df.to_dict(orient="records")]


def _table(donnees, action):
    if isinstance(donnees, list):
        return donnees
    if donnees is None:
        raise ValueError(f"« {action} » n'a reçu aucune donnée en entrée.")
    raise ValueError(f"« {action} » attend un tableau de données en entrée, il a reçu : {type(donnees).__name__}.")


def fichiers_correspondants(motif):
    return sorted(f for f in ENTREES.iterdir() if f.is_file() and fnmatch(f.name.lower(), motif.lower()))


def lire_texte(chemin):
    if chemin.suffix.lower() == ".pdf":
        with pdfplumber.open(chemin) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
    for encodage in ("utf-8", "latin-1"):
        try:
            return chemin.read_text(encoding=encodage)
        except UnicodeDecodeError:
            continue
    return ""


def lire_tableur(chemin):
    if chemin.suffix.lower() == ".csv":
        for encodage in ("utf-8-sig", "latin-1"):
            try:
                df = pd.read_csv(chemin, sep=None, engine="python", encoding=encodage, dtype=str)
                break
            except UnicodeDecodeError:
                continue
    else:
        df = pd.read_excel(chemin)
    return _nombres(df)


def description_fichiers():
    """Résumé des fichiers uploadés, envoyé au planificateur pour qu'il connaisse les colonnes."""
    infos = []
    for f in sorted(ENTREES.iterdir()):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        try:
            if ext in EXT_TABLE:
                df = lire_tableur(f)
                infos.append(f"- {f.name} (tableur, {len(df)} lignes) colonnes : {list(df.columns)} ; "
                             f"exemple : {json.dumps(_vers_lignes(df.head(2)), ensure_ascii=False)}")
            elif ext in EXT_TEXTE:
                extrait = lire_texte(f)[:200].replace("\n", " ")
                infos.append(f"- {f.name} (document) début : « {extrait}… »")
            else:
                infos.append(f"- {f.name} (format non pris en charge)")
        except Exception as e:  # un fichier illisible ne doit pas bloquer la planification
            infos.append(f"- {f.name} (illisible : {e})")
    return "\n".join(infos) if infos else "(aucun fichier uploadé)"


# --------------------------------------------------------------------------
# Les 6 actions : chacune reçoit (donnees_entree, parametres) et renvoie sa sortie
# --------------------------------------------------------------------------
def fichiers_etape(etape_id):
    dossier = ETAPES / etape_id
    return sorted(f for f in dossier.iterdir() if f.is_file()) if dossier.is_dir() else []


def lire_fichier(donnees, p, fichiers=None):
    fichiers = fichiers or fichiers_correspondants(p["fichiers"])
    if not fichiers:
        raise FileNotFoundError(f"Aucun fichier uploadé ne correspond à « {p['fichiers']} ».")
    lignes = []
    for f in fichiers:
        ext = f.suffix.lower()
        if ext in EXT_TABLE:
            lignes_fichier = _vers_lignes(lire_tableur(f))
            if len(fichiers) > 1:
                lignes_fichier = [{"fichier": f.name, **l} for l in lignes_fichier]
            lignes += lignes_fichier
        elif ext in EXT_TEXTE:
            texte = lire_texte(f)
            if not texte:
                raise ValueError(f"Impossible d'extraire du texte de {f.name} (PDF scanné ?).")
            lignes.append({"fichier": f.name, "texte": texte})
        else:
            raise ValueError(f"Format non pris en charge : {f.name}")
    return lignes


def _lots(lignes, max_lignes=20, max_caracteres=12000):
    lot, taille = [], 0
    for i, ligne in enumerate(lignes):
        t = len(json.dumps(ligne, ensure_ascii=False))
        if lot and (len(lot) >= max_lignes or taille + t > max_caracteres):
            yield lot
            lot, taille = [], 0
        lot.append(i)
        taille += t
    if lot:
        yield lot


def analyser_ia(donnees, p):
    lignes = _table(donnees, "Analyser avec l'IA")
    champs = p["champs"]
    contexte = p.get("contexte", "")
    if p.get("fichier_contexte"):
        chemin = ENTREES / p["fichier_contexte"]
        if not chemin.exists():
            raise FileNotFoundError(f"Fichier de contexte introuvable : {p['fichier_contexte']}")
        contexte += f"\n\nDocument de référence ({chemin.name}) :\n{lire_texte(chemin)[:6000]}"

    systeme = (
        "Tu es un assistant d'analyse de données. Tu reçois une liste d'éléments numérotés (index). "
        f"Tâche : {p['instruction']}\n"
        f"Pour chaque élément, renvoie exactement ces champs : {json.dumps(champs, ensure_ascii=False)}.\n"
        "Les nombres doivent être des nombres JSON (pas de texte), les dates au format AAAA-MM-JJ. "
        "Si une information est absente, mets null. "
        'Réponds UNIQUEMENT en JSON : {"resultats": [{"index": 0, ...champs...}, ...]} avec un résultat par élément.'
    )
    if contexte.strip():
        systeme += f"\n\nContexte :\n{contexte.strip()}"

    resultats = {}
    for lot in _lots(lignes):
        elements = []
        for i in lot:
            ligne = {k: (v[:8000] if isinstance(v, str) else v) for k, v in lignes[i].items()}
            elements.append({"index": i, **ligne})
        for essai in range(2):
            reponse = appeler_ia([
                {"role": "system", "content": systeme},
                {"role": "user", "content": json.dumps(elements, ensure_ascii=False)},
            ])
            recus = {r.get("index"): r for r in reponse.get("resultats", []) if isinstance(r, dict)}
            if all(i in recus for i in lot):
                break
        else:
            raise ValueError("L'IA n'a pas renvoyé un résultat pour chaque ligne.")
        resultats.update(recus)

    sortie = []
    for i, ligne in enumerate(lignes):
        nouvelle = {k: v for k, v in ligne.items() if k != "texte"}
        for champ in champs:
            nouvelle[champ] = _propre(resultats[i].get(champ))
        sortie.append(nouvelle)
    return sortie


def transformer(donnees, p):
    df = _nombres(pd.DataFrame(_table(donnees, "Transformer les données")))
    for op in p["operations"]:
        t = op["type"]
        try:
            if t == "filtrer":
                df = df.query(op["condition"], engine="python")
            elif t == "calculer":
                df[op["colonne"]] = df.eval(op["formule"], engine="python")
            elif t == "regrouper":
                par = op["par"] if isinstance(op["par"], list) else [op["par"]]
                agg = {col: AGREGATIONS.get(f, f) for col, f in op["agregations"].items()}
                df = df.groupby(par, dropna=False).agg(agg).reset_index()
            elif t == "trier":
                df = df.sort_values(op["par"], ascending=op.get("ordre", "croissant") != "decroissant")
            elif t == "selectionner":
                df = df[op["colonnes"]]
            elif t == "renommer":
                df = df.rename(columns=op["colonnes"])
            elif t == "limiter":
                df = df.head(int(op["n"]))
        except KeyError as e:
            raise ValueError(f"Opération « {t} » : colonne inconnue {e}. Colonnes disponibles : {list(df.columns)}")
        except Exception as e:
            raise ValueError(f"Opération « {t} » impossible : {e}. Colonnes disponibles : {list(df.columns)}")
    return _vers_lignes(df.reset_index(drop=True))


def _nom_feuille(nom):
    nom = re.sub(r"[\[\]\*\?/\\:]", "-", str(nom) if nom not in (None, "") else "(vide)")
    return nom[:31]


def _mettre_en_forme(feuille):
    from openpyxl.styles import Alignment, Font, PatternFill
    for cellule in feuille[1]:
        cellule.font = Font(bold=True, color="FFFFFF")
        cellule.fill = PatternFill("solid", fgColor="4F46E5")
    for colonne in feuille.columns:
        longueur = max(len(str(c.value)) if c.value is not None else 0 for c in colonne)
        feuille.column_dimensions[colonne[0].column_letter].width = min(max(12, longueur + 2), 70)
        if longueur > 70:
            for c in colonne[1:]:
                c.alignment = Alignment(wrap_text=True, vertical="top")
    feuille.freeze_panes = "A2"


def ecrire_fichier(donnees, p):
    nom = Path(p["nom_fichier"]).name
    ext = Path(nom).suffix.lower()
    chemin = SORTIES / nom
    if isinstance(donnees, str):
        if ext not in (".txt", ".md"):
            raise ValueError("Les données sont un texte : utilisez un nom de fichier en .txt.")
        chemin.write_text(donnees, encoding="utf-8")
        return {"fichier": nom, "url": f"/telecharger/{nom}", "lignes": donnees.count("\n") + 1}

    df = _nombres(pd.DataFrame(_table(donnees, "Écrire un fichier")))
    if ext == ".csv":
        df.to_csv(chemin, index=False, sep=";", encoding="utf-8-sig")
    elif ext == ".xlsx":
        par = p.get("feuille_par")
        with pd.ExcelWriter(chemin, engine="openpyxl") as excel:
            if par:
                if par not in df.columns:
                    raise ValueError(f"Colonne « {par} » introuvable. Colonnes disponibles : {list(df.columns)}")
                numeriques = [c for c in df.select_dtypes("number").columns if c != par]
                synthese = df.groupby(par, dropna=False).agg(nombre=(par, "size"),
                                                            **{f"total_{c}": (c, "sum") for c in numeriques})
                synthese.reset_index().to_excel(excel, sheet_name="Synthèse", index=False)
                for valeur, groupe in df.groupby(par, dropna=False):
                    groupe.to_excel(excel, sheet_name=_nom_feuille(valeur), index=False)
                df.to_excel(excel, sheet_name="Toutes les données", index=False)
            else:
                df.to_excel(excel, sheet_name="Données", index=False)
            for feuille in excel.book.worksheets:
                _mettre_en_forme(feuille)
    else:
        raise ValueError("Extension non prise en charge : utilisez .xlsx, .csv ou .txt")
    return {"fichier": nom, "url": f"/telecharger/{nom}", "lignes": len(df)}


def appel_http(donnees, p):
    methode = p.get("methode", "GET").upper()
    reponse = requests.request(methode, p["url"], headers=p.get("en_tetes"),
                               json=p.get("corps") if methode != "GET" else None, timeout=20)
    if reponse.status_code >= 400:
        raise ValueError(f"L'API a répondu {reponse.status_code} : {reponse.text[:300]}")
    try:
        resultat = reponse.json()
    except ValueError:
        return reponse.text
    for cle in [c for c in p.get("chemin", "").split(".") if c]:
        resultat = resultat[int(cle)] if isinstance(resultat, list) else resultat[cle]
    if isinstance(resultat, list):
        return [{k: _propre(v) for k, v in r.items()} if isinstance(r, dict) else {"valeur": r} for r in resultat]
    if isinstance(resultat, dict):
        return [{k: _propre(v) for k, v in resultat.items()}]
    return str(resultat)


def generer_texte(donnees, p):
    systeme = ("Tu es un rédacteur professionnel francophone. Écris directement le texte demandé, "
               "sans commentaire ni balises Markdown superflues.")
    if p.get("pour_chaque_ligne"):
        lignes = _table(donnees, "Générer un texte")
        colonne = p.get("colonne_sortie") or "texte_genere"

        def rediger(ligne):
            return appeler_ia([
                {"role": "system", "content": systeme},
                {"role": "user", "content": f"Consigne : {p['instruction']}\n\nDonnées :\n"
                                            f"{json.dumps(ligne, ensure_ascii=False)}"},
            ], json_mode=False)

        with ThreadPoolExecutor(max_workers=5) as pool:
            textes = list(pool.map(rediger, lignes))
        return [{**ligne, colonne: texte} for ligne, texte in zip(lignes, textes)]

    if isinstance(donnees, list):
        contenu = json.dumps(donnees[:300], ensure_ascii=False)[:20000]
    else:
        contenu = (donnees or "")[:20000]
    return appeler_ia([
        {"role": "system", "content": systeme},
        {"role": "user", "content": f"Consigne : {p['instruction']}\n\nDonnées :\n{contenu}"},
    ], json_mode=False)


FONCTIONS = {
    "lire_fichier": lire_fichier, "analyser_ia": analyser_ia, "transformer": transformer,
    "ecrire_fichier": ecrire_fichier, "appel_http": appel_http, "generer_texte": generer_texte,
}


# --------------------------------------------------------------------------
# Exécuteur : enchaîne les blocs et émet un événement par étape
# --------------------------------------------------------------------------
def apercu(donnees, max_lignes=8):
    """Version allégée des données pour l'affichage dans l'interface."""
    def court(v):
        return v[:160] + "…" if isinstance(v, str) and len(v) > 160 else v

    if donnees is None:
        return {"type": "vide"}
    if isinstance(donnees, str):
        return {"type": "texte", "texte": donnees[:3000], "total": len(donnees)}
    if isinstance(donnees, dict) and "url" in donnees:
        return {"type": "fichier", **donnees}
    if isinstance(donnees, list):
        colonnes = list(dict.fromkeys(k for l in donnees[:50] for k in l))
        lignes = [{k: court(l.get(k)) for k in colonnes} for l in donnees[:max_lignes]]
        return {"type": "table", "colonnes": colonnes, "lignes": lignes, "total": len(donnees)}
    return {"type": "texte", "texte": json.dumps(donnees, ensure_ascii=False)[:3000]}


def _en_texte(donnees):
    if isinstance(donnees, str):
        return donnees
    if isinstance(donnees, list) and donnees and all("texte" in l for l in donnees):
        return "\n\n".join(f"[{l.get('fichier', '')}]\n{l['texte']}" for l in donnees)
    return json.dumps(donnees, ensure_ascii=False)


def combiner(plan, resultats, ids):
    """Réunit les sorties de plusieurs étapes en un seul texte (entree = liste d'étapes)."""
    titres = {e["id"]: e.get("titre", e["id"]) for e in plan["etapes"]}
    return "\n\n".join(f"### {titres[i]}\n{_en_texte(resultats.get(i))}" for i in ids)


def attendre_fichier(etape):
    """Met le workflow en pause jusqu'à ce que l'utilisateur dépose un fichier pour cette étape."""
    p = etape["parametres"]
    yield {"evt": "attente", "id": etape["id"],
           "demande": p.get("demande") or f"Déposez le fichier demandé ({p['fichiers']})"}
    limite = time.time() + 30 * 60
    while not fichiers_etape(etape["id"]):
        if time.time() > limite:
            raise TimeoutError("Aucun fichier déposé après 30 minutes.")
        time.sleep(1)
        yield {"evt": "ping"}  # permet de détecter une page fermée


def executer(plan):
    """Générateur : exécute les étapes une par une et produit des événements pour l'interface."""
    fichiers = [f.name for f in sorted(ENTREES.iterdir()) if f.is_file()]
    yield {"evt": "fin", "id": "declencheur", "statut": "ok", "duree": 0,
           "sortie": {"type": "texte", "texte": "Fichiers disponibles : " + (", ".join(fichiers) or "aucun")}}
    resultats, courant = {}, None
    for etape in plan["etapes"]:
        yield {"evt": "debut", "id": etape["id"]}
        if isinstance(etape.get("entree"), list):
            entree = combiner(plan, resultats, etape["entree"])
        else:
            entree = resultats.get(etape["entree"]) if etape.get("entree") else courant
        debut = time.time()
        try:
            if etape["action"] == "lire_fichier":
                p = etape["parametres"]
                fichiers = fichiers_etape(etape["id"]) or fichiers_correspondants(p["fichiers"])
                if not fichiers:
                    yield from attendre_fichier(etape)
                    fichiers = fichiers_etape(etape["id"])
                    debut = time.time()
                sortie = lire_fichier(entree, p, fichiers)
            else:
                sortie = FONCTIONS[etape["action"]](entree, etape.get("parametres", {}))
        except Exception as e:
            yield {"evt": "fin", "id": etape["id"], "statut": "erreur", "erreur": str(e),
                   "entree": apercu(entree), "duree": round(time.time() - debut, 1)}
            yield {"evt": "termine", "statut": "erreur", "etape": etape["id"], "erreur": str(e)}
            return
        resultats[etape["id"]] = sortie
        if etape["action"] != "ecrire_fichier":  # un fichier écrit ne remplace pas les données courantes
            courant = sortie
        yield {"evt": "fin", "id": etape["id"], "statut": "ok", "entree": apercu(entree),
               "sortie": apercu(sortie), "duree": round(time.time() - debut, 1)}
    yield {"evt": "termine", "statut": "ok"}
