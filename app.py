"""FlowGen Mini — serveur web. Lancement : python app.py"""
import json
import re
import shutil
import threading
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

load_dotenv(Path(__file__).parent / ".env")

import actions  # noqa: E402  (après load_dotenv)
import planner  # noqa: E402

app = Flask(__name__)
EXEMPLES = actions.DOSSIER / "exemples"
WORKFLOWS = actions.DOSSIER / "workflows"
PORT = 5050

SCENARIOS = [
    {"id": "1_releve_bancaire", "titre": "Relevé bancaire", "icone": "🏦",
     "demande": "Lis le relevé bancaire CSV, classe chaque transaction dans une catégorie (Alimentation, "
                "Logement, Transport, Loisirs, Santé, Abonnements, Revenus, Épargne, Autres) avec l'IA, "
                "puis crée un rapport Excel avec une feuille par catégorie et une synthèse des montants."},
    {"id": "2_cv", "titre": "Tri de CV", "icone": "👩‍💼",
     "demande": "Lis tous les CV en PDF, extrais pour chaque candidat son nom, ses compétences et ses années "
                "d'expérience, donne un score sur 100 par rapport à l'offre d'emploi offre_emploi.txt avec une "
                "justification, puis crée un classement Excel du meilleur au moins bon."},
    {"id": "3_factures", "titre": "Factures", "icone": "🧾",
     "demande": "Lis toutes les factures PDF, extrais le fournisseur, le numéro de facture, la date, le "
                "montant HT et le montant TTC, puis crée un tableau récapitulatif Excel trié par date."},
    {"id": "4_etudiants", "titre": "Emails étudiants", "icone": "🎓",
     "demande": "Lis la liste des étudiants (Excel) et rédige pour chacun un email personnalisé et bienveillant "
                "qui annonce sa note finale, reprend ses points forts et ses axes d'amélioration. "
                "Enregistre le résultat dans un fichier Excel."},
]


def liste_fichiers():
    return [{"nom": f.name, "taille": f.stat().st_size}
            for f in sorted(actions.ENTREES.iterdir()) if f.is_file()]


def vider_entrees():
    for f in actions.ENTREES.iterdir():
        if f.is_file():
            f.unlink()


def charger_exemples(dossier):
    source = EXEMPLES / dossier
    if not source.is_dir():
        raise FileNotFoundError(f"Dossier d'exemples introuvable : {dossier}")
    vider_entrees()
    for f in source.iterdir():
        if f.is_file():
            shutil.copy(f, actions.ENTREES / f.name)


@app.get("/")
def accueil():
    return render_template("index.html")


@app.get("/api/etat")
def etat():
    catalogue = {k: {"libelle": v["libelle"], "icone": v["icone"]} for k, v in actions.ACTIONS.items()}
    workflows = sorted(f.stem for f in WORKFLOWS.glob("*.json"))
    return jsonify(modele=f"{actions.fournisseur().capitalize()} · {actions.modele()}", cle=actions.cle_configuree(), fichiers=liste_fichiers(),
                   scenarios=SCENARIOS, actions=catalogue, workflows=workflows)


@app.post("/api/fichiers")
def upload():
    for f in request.files.getlist("fichiers"):
        nom = secure_filename(f.filename) or "fichier"
        f.save(actions.ENTREES / nom)
    return jsonify(fichiers=liste_fichiers())


@app.delete("/api/fichiers/<nom>")
def supprimer_fichier(nom):
    chemin = actions.ENTREES / secure_filename(nom)
    if chemin.exists():
        chemin.unlink()
    return jsonify(fichiers=liste_fichiers())


@app.delete("/api/fichiers")
def supprimer_tout():
    vider_entrees()
    return jsonify(fichiers=liste_fichiers())


@app.post("/api/scenario/<sid>")
def scenario(sid):
    s = next((s for s in SCENARIOS if s["id"] == sid), None)
    if not s:
        return jsonify(erreur="Scénario inconnu"), 404
    charger_exemples(s["id"])
    return jsonify(fichiers=liste_fichiers(), demande=s["demande"])


@app.post("/api/plan")
def plan():
    demande = (request.json or {}).get("demande", "").strip()
    if not demande:
        return jsonify(erreur="Écrivez d'abord ce que vous voulez automatiser."), 400
    try:
        resultat, journal = planner.generer_plan(demande)
        return jsonify(plan=resultat, journal=journal)
    except planner.PlanInvalide as e:
        return jsonify(erreur=str(e), journal=e.journal), 422
    except Exception as e:
        return jsonify(erreur=str(e)), 500


@app.post("/api/reparer")
def reparer():
    d = request.json or {}
    try:
        resultat, journal = planner.reparer_plan(d.get("demande", ""), d["plan"], d["etape"], d["erreur"])
        return jsonify(plan=resultat, journal=journal)
    except planner.PlanInvalide as e:
        return jsonify(erreur=str(e), journal=e.journal), 422
    except Exception as e:
        return jsonify(erreur=str(e)), 500


@app.post("/api/executer")
def executer():
    plan_courant = (request.json or {}).get("plan")
    erreurs = planner.valider_plan(plan_courant)
    if erreurs:
        return jsonify(erreur="Plan invalide : " + " ; ".join(erreurs)), 400

    def flux():  # une ligne JSON par événement, lue en direct par la page
        for evenement in actions.executer(plan_courant):
            yield json.dumps(evenement, ensure_ascii=False) + "\n"

    return Response(flux(), mimetype="application/x-ndjson", headers={"X-Accel-Buffering": "no"})


@app.get("/telecharger/<nom>")
def telecharger(nom):
    return send_from_directory(actions.SORTIES, nom, as_attachment=True)


@app.post("/api/workflows")
def sauvegarder():
    d = request.json or {}
    nom = re.sub(r"[^a-z0-9_-]+", "_", (d.get("nom") or "workflow").lower()).strip("_") or "workflow"
    contenu = {k: d.get(k) for k in ("nom", "demande", "plan", "exemples") if d.get(k)}
    (WORKFLOWS / f"{nom}.json").write_text(json.dumps(contenu, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify(nom=nom)


@app.get("/api/workflows/<nom>")
def ouvrir(nom):
    chemin = WORKFLOWS / f"{secure_filename(nom)}.json"
    if not chemin.exists():
        return jsonify(erreur="Workflow introuvable"), 404
    workflow = json.loads(chemin.read_text(encoding="utf-8"))
    if workflow.get("exemples"):  # recharge les fichiers d'exemple associés
        charger_exemples(workflow["exemples"])
    return jsonify(workflow=workflow, fichiers=liste_fichiers())


if __name__ == "__main__":
    print(f"\n  FlowGen Mini démarre sur http://localhost:{PORT}  (IA : {actions.fournisseur()} · {actions.modele()})\n")
    if not actions.cle_configuree():
        print(f"  ⚠️  Clé {actions.fournisseur()} absente : copiez .env.example en .env et ajoutez votre clé.\n")
    threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    app.run(port=PORT, threaded=True)
