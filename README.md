# dora-demo
Démonstration DORA metrics — pipeline + instrumentation

# DORA demo

Démonstration pas-à-pas pour instrumenter les indicateurs DORA (Deployment Frequency, Lead Time, Change Failure Rate, MTTR).

Étapes :
1. Exemple d'app Node.js minimale.
2. Workflow GitHub Actions qui crée un Deployment + statut (success).
3. Téléchargement des artefacts (deployment.json / status.json) pour valider.
4. Puis nous collecterons ces événements et calculerons les métriques.

Voir les autres fichiers dans le repo.test leadtime
