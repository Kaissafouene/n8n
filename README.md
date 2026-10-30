# ⚡ FlowGen Mini

Un mini outil d'automatisation façon n8n, piloté par l'IA. On écrit en français ce qu'on veut automatiser,
l'IA (OpenAI ou Gemini) le découpe en étapes, l'interface affiche la chaîne de blocs, puis on l'exécute bloc par bloc
en voyant l'entrée, la sortie et le statut ✅ / ❌ de chaque étape.

## Installation

Python 3.10 ou plus récent.

```bash
pip install -r requirements.txt
cp .env.example .env        # Windows : copy .env.example .env
```

Ouvrez `.env` et mettez votre clé, puis choisissez le fournisseur avec `FOURNISSEUR_IA` :

- `FOURNISSEUR_IA=openai` : clé dans `OPENAI_API_KEY`, modèle dans `OPENAI_MODEL` (défaut `gpt-4o-mini`)
- `FOURNISSEUR_IA=gemini` : clé dans `GEMINI_API_KEY`, modèle dans `GEMINI_MODEL` (défaut `gemini-2.5-flash`)

Le fournisseur et le modèle utilisés s'affichent en haut de l'interface. Relancez `python app.py` après avoir modifié `.env`.

## Lancement

```bash
python app.py
```

Le navigateur s'ouvre sur http://localhost:5050.

## Comment ça marche

| Fichier | Rôle |
|---|---|
| `app.py` | Serveur web (Flask) : upload, génération du plan, exécution en direct, sauvegarde |
| `planner.py` | Demande le plan à l'IA, le **valide**, et renvoie les erreurs au modèle (3 tentatives max) |
| `actions.py` | Les 6 actions autorisées et l'exécuteur qui enchaîne les blocs |
| `templates/index.html` | L'interface |
| `exemples/` | Les fichiers des 4 scénarios de démo |
| `workflows/` | Les workflows sauvegardés (JSON), dont les 4 scénarios prêts à relancer |

Les 6 actions : 📄 Lire un fichier · 🧠 Analyser avec l'IA · 🔧 Transformer les données ·
💾 Écrire un fichier · 🌐 Appel HTTP · ✍️ Générer un texte.
L'IA ne peut rien utiliser d'autre : un plan avec une action inventée, un paramètre manquant
ou un fichier inexistant est refusé et renvoyé au modèle pour correction.
Si une étape échoue à l'exécution, le bouton **🔧 Demander à l'IA de corriger** renvoie l'erreur au modèle.

## Déroulé de la démo (≈ 20 min)

1. **Présenter l'interface** : à gauche les fichiers et la demande, à droite le workflow.
2. **Scénario 1 – Relevé bancaire** : cliquez sur « 🏦 Relevé bancaire » (charge le CSV et la demande),
   puis « ✨ Générer le workflow ». Montrez les blocs et leurs paramètres, puis « ▶ Exécuter ».
   Téléchargez l'Excel : une feuille par catégorie + une synthèse.
3. **Scénario 2 – Tri de CV** : 5 CV PDF + une offre d'emploi. Montrez la colonne `score`
   et le classement Excel. Bon moment pour expliquer la « sortie structurée ».
4. **Scénario 3 – Factures** : l'IA retrouve fournisseur, date et montants malgré des formats de date tous différents.
5. **Scénario 4 – Emails étudiants** : un email personnalisé par ligne du fichier Excel.
6. **Sauvegarder / relancer** : « 💾 Sauvegarder » enregistre le workflow (et le télécharge en JSON) ;
   « 📂 Ouvrir un workflow… » le recharge avec ses fichiers d'exemple.
7. **À vous de jouer** : faites écrire aux étudiants leur propre demande, par exemple
   « garde seulement les dépenses de plus de 50 € et trie-les par montant ».

💡 **Plan B sans réseau fiable** : les 4 scénarios sont déjà dans `workflows/`.
Ouvrez-les avec « 📂 Ouvrir un workflow… » pour sauter l'étape de génération du plan.
L'exécution, elle, a toujours besoin de l'API pour les étapes d'IA.

## Tests

```bash
python tests/test_scenarios.py
```

Ces tests exécutent les 4 scénarios de bout en bout avec un **faux modèle** (pas besoin de clé).
Ils vérifient la mécanique : validation, correction du plan, exécution et fichiers Excel produits.
