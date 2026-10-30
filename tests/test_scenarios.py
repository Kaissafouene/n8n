"""Teste les 4 scénarios de bout en bout SANS clé OpenAI.

Le modèle est remplacé par un faux modèle déterministe : on vérifie toute la mécanique
(planification, validation, boucle de correction, exécution, fichiers produits),
pas la qualité des réponses de l'IA.  Lancement : python tests/test_scenarios.py
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import actions  # noqa: E402
import app as serveur  # noqa: E402
import planner  # noqa: E402

PLANS = {json.loads(f.read_text())["exemples"]: json.loads(f.read_text())["plan"]
         for f in serveur.WORKFLOWS.glob("scenario_*.json")}
appels = {"plan": 0}

CATEGORIES = {"SALAIRE": "Revenus", "REMBOURSEMENT": "Revenus", "LOYER": "Logement", "EDF": "Logement",
              "CARREFOUR": "Alimentation", "LIDL": "Alimentation", "AUCHAN": "Alimentation", "MONOPRIX": "Alimentation",
              "BOULANGERIE": "Alimentation", "TOTAL": "Transport", "SHELL": "Transport", "SNCF": "Transport",
              "NETFLIX": "Abonnements", "SPOTIFY": "Abonnements", "ORANGE": "Abonnements", "FITPARK": "Abonnements",
              "PHARMACIE": "Santé", "DOCTEUR": "Santé", "CINEMA": "Loisirs", "RESTAURANT": "Loisirs",
              "PIZZERIA": "Loisirs", "UBER": "Loisirs", "DECATHLON": "Loisirs", "FNAC": "Loisirs", "EPARGNE": "Épargne"}
COMPETENCES = ["Python", "pandas", "SQL", "Power BI", "Tableau", "Excel", "statistiques", "machine learning", "anglais"]


def faux_modele(messages, json_mode=True):
    systeme, dernier = messages[0]["content"], messages[-1]["content"]
    if systeme.startswith("Tu es le planificateur"):
        appels["plan"] += 1
        if "a échoué" in dernier:  # réparation après une erreur d'exécution
            return PLANS["3_factures"]
        scenario = next(s["id"] for s in serveur.SCENARIOS if s["demande"] in messages[1]["content"])
        if scenario == "1_releve_bancaire" and len(messages) == 2:
            # 1re réponse volontairement fausse : action inventée -> doit être corrigée
            return {"titre": "x", "etapes": [{"id": "etape_1", "action": "envoyer_sms", "parametres": {}}]}
        return PLANS[scenario]

    if not json_mode:  # generer_texte
        if "Corrige le travail" in dernier:
            assert "cours" in dernier.lower() and "Exercice 1" in dernier and "if age = 18" in dernier
            return "Correction\n\nExercice 1 : faux, 3.5 est un float.\nNote : 9/20\nPoints à améliorer : ..."
        if "résumé" in dernier:
            assert "Les conditions" in dernier
            return "Résumé du cours...\n\nExercice 1 : quel est le type de 3.5 ?\nExercice 2 : ..."
        d = json.loads(dernier.split("Données :\n", 1)[1])
        return (f"Objet : Votre note finale\n\nBonjour {d['prenom']},\n\nVotre note finale est de "
                f"{d['note_finale']}/20. Points forts : {d['points_forts']}. À travailler : "
                f"{d['axes_amelioration']}.\n\nL'équipe pédagogique")

    elements = json.loads(dernier)
    resultats = []
    for e in elements:
        r = {"index": e["index"]}
        if "libelle" in e:
            r["categorie"] = next((c for k, c in CATEGORIES.items() if k in e["libelle"]), "Autres")
        elif "offre" in systeme.lower():
            texte = e["texte"]
            trouvees = [c for c in COMPETENCES if c.lower() in texte.lower()]
            r.update(nom=texte.splitlines()[0], competences=trouvees,
                     annees_experience=int(m.group(1)) if (m := re.search(r"\((\d+) an", texte)) else 0,
                     score=min(100, 12 * len(trouvees)), justification=f"{len(trouvees)} compétences de l'offre.")
        else:
            texte = e["texte"]
            r.update(fournisseur=texte.splitlines()[0],
                     numero=re.search(r"Facture n° (\S+)", texte).group(1),
                     date="2026-09-01", montant_ht=float(re.search(r"Total HT : ([\d.]+)", texte).group(1)),
                     montant_ttc=float(re.search(r"TOTAL TTC : ([\d.]+)", texte).group(1)))
        resultats.append(r)
    return {"resultats": resultats}


actions.appeler_ia = faux_modele
client = serveur.app.test_client()


def executer(plan):
    rep = client.post("/api/executer", json={"plan": plan})
    assert rep.status_code == 200, rep.get_json()
    return [json.loads(l) for l in rep.get_data(as_text=True).splitlines() if l.strip()]


def scenario(sid, fichier_attendu):
    demande = client.post(f"/api/scenario/{sid}").get_json()["demande"]
    rep = client.post("/api/plan", json={"demande": demande}).get_json()
    assert "plan" in rep, rep
    evenements = executer(rep["plan"])
    fins = [e for e in evenements if e["evt"] == "fin"]
    for e in fins:
        print(f"   {'✅' if e['statut'] == 'ok' else '❌'} {e['id']}" + (f" : {e.get('erreur')}" if e.get("erreur") else ""))
    assert evenements[-1] == {"evt": "termine", "statut": "ok"}, evenements[-1]
    chemin = actions.SORTIES / fichier_attendu
    assert chemin.exists()
    return rep, pd.read_excel(chemin, sheet_name=None)


print("Scénario 1 : relevé bancaire")
rep, feuilles = scenario("1_releve_bancaire", "rapport_categories.xlsx")
assert [j["tentative"] for j in rep["journal"]] == [1, 2] and rep["journal"][0]["erreurs"], "correction attendue"
print("   correction du plan :", rep["journal"][0]["erreurs"][0][:80], "…")
assert "Synthèse" in feuilles and "Alimentation" in feuilles and len(feuilles["Toutes les données"]) == 30
synthese = feuilles["Synthèse"].set_index("categorie")
assert round(synthese.loc["Alimentation", "total_montant"], 2) == -301.97
print("   feuilles :", list(feuilles))

print("Scénario 2 : CV")
_, feuilles = scenario("2_cv", "classement_candidats.xlsx")
df = feuilles["Données"]
assert len(df) == 5 and list(df["score"]) == sorted(df["score"], reverse=True)
assert df.iloc[0]["nom"] in ("Sarah Benali", "Karim Haddad") and df.iloc[-1]["nom"] in ("Lucie Petit", "Thomas Durand")
print("   classement :", list(df["nom"]))

print("Scénario 3 : factures")
_, feuilles = scenario("3_factures", "recap_factures.xlsx")
df = feuilles["Données"]
assert len(df) == 5 and round(df["montant_ttc"].sum(), 2) == 2042.88
print("   total TTC :", round(df["montant_ttc"].sum(), 2))

print("Scénario 4 : emails étudiants")
_, feuilles = scenario("4_etudiants", "emails_etudiants.xlsx")
df = feuilles["Données"]
assert len(df) == 8 and df["email"].str.contains("Bonjour").all()
print("   exemple :", df.iloc[0]["email"][:60].replace("\n", " "), "…")

print("Scénario 5 : cours → exercices → pause → correction")
demande = client.post("/api/scenario/5_cours").get_json()["demande"]
plan5 = client.post("/api/plan", json={"demande": demande}).get_json()["plan"]
travail = serveur.EXEMPLES / "5_cours" / "pendant_la_pause" / "mon_travail.txt"
evenements = []
for e in actions.executer(plan5):  # simule l'utilisateur qui dépose son travail pendant la pause
    evenements.append(e)
    if e["evt"] == "attente":
        print("   ⏸ pause :", e["demande"])
        assert e["id"] == "etape_4"
        with travail.open("rb") as f:
            assert client.post("/api/fichiers?etape=etape_4", data={"fichiers": (f, "mes_reponses.txt")}).status_code == 200
assert [e["id"] for e in evenements if e["evt"] == "attente"] == ["etape_4"]
assert evenements[-1] == {"evt": "termine", "statut": "ok"}, evenements[-1]
assert "Note : 9/20" in (actions.SORTIES / "correction.txt").read_text(encoding="utf-8")
assert (actions.SORTIES / "resume_et_exercices.txt").exists()
print("   ✅ correction.txt et resume_et_exercices.txt créés")

print("Validation : actions inventées et paramètres faux refusés")
assert planner.valider_plan({"etapes": [{"id": "a", "action": "envoyer_email", "parametres": {}}]})
assert planner.valider_plan({"etapes": [{"id": "a", "action": "lire_fichier", "parametres": {"fichiers": "*.csv"}},
                                        {"id": "b", "action": "transformer", "entree": ["a"], "parametres": {"operations": []}}]})
assert planner.valider_plan({"etapes": [{"id": "a", "action": "transformer", "parametres": {"operations": []}}]})

print("Erreur d'exécution puis réparation par l'IA")
client.post("/api/scenario/3_factures")
plan_casse = json.loads(json.dumps(PLANS["3_factures"]))
plan_casse["etapes"][2]["parametres"]["operations"][0]["par"] = "colonne_qui_nexiste_pas"
evenements = executer(plan_casse)
assert evenements[-1]["statut"] == "erreur" and "colonne" in evenements[-1]["erreur"]
print("   ❌", evenements[-1]["erreur"][:90], "…")
rep = client.post("/api/reparer", json={"demande": "", "plan": plan_casse, "etape": "etape_3",
                                        "erreur": evenements[-1]["erreur"]}).get_json()
assert executer(rep["plan"])[-1]["statut"] == "ok"
print("   ✅ plan corrigé et ré-exécuté")

print("Sauvegarde et réouverture d'un workflow")
assert client.post("/api/workflows", json={"nom": "Test sauvegarde", "plan": rep["plan"]}).get_json()["nom"] == "test_sauvegarde"
assert client.get("/api/workflows/test_sauvegarde").get_json()["workflow"]["plan"] == rep["plan"]
(serveur.WORKFLOWS / "test_sauvegarde.json").unlink()

print("\nTous les tests sont passés ✅")
