#!/usr/bin/env python3
"""
Calc simple DORA metrics for a GitHub repo.

Usage:
  export GITHUB_TOKEN="ghp_..."
  python scripts/calc_dora.py --repo donkoakoweraphael/dora-demo --days 30

Le script récupère :
- les déploiements (API /deployments)
- les statuses de chaque déploiement (/deployments/{id}/statuses)
- pour chaque déploiement, recherche la/les PR(s) associée(s) au commit via /commits/{sha}/pulls
- les issues labellées 'incident' (state=all) et qui contiennent `deployment_id: <id>` dans le corps

Et calcule :
- Deployment Frequency (DF) sur la période
- Lead Time for Changes (LT) médian/moyen (deploy.created_at - pr.merged_at)
- Change Failure Rate (CFR) = fraction de deployments avec status != success ou ayant incident lié
- MTTR : moyenne(duration des incidents liés à des deployments)
"""
import os
import sys
import argparse
import requests
from datetime import datetime, timedelta, timezone
from dateutil import parser as dateparse
import statistics
import re

GITHUB_API = "https://api.github.com"

def gh_get(url, token, params=None, accept=None):
    headers = {"Authorization": f"token {token}", "Accept": accept or "application/vnd.github+json"}
    resp = requests.get(url, headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()

def list_deployments(owner, repo, token, since_dt=None):
    url = f"{GITHUB_API}/repos/{owner}/{repo}/deployments"
    params = {"per_page": 100}
    all_items = []
    page = 1
    while True:
        params["page"] = page
        items = gh_get(url, token, params=params)
        if not items:
            break
        for it in items:
            created_at = dateparse.parse(it.get("created_at"))
            if since_dt and created_at < since_dt:
                continue
            all_items.append(it)
        page += 1
        if len(items) < 100:
            break
    return all_items

def get_deployment_statuses(owner, repo, deployment_id, token):
    url = f"{GITHUB_API}/repos/{owner}/{repo}/deployments/{deployment_id}/statuses"
    params = {"per_page": 100}
    return gh_get(url, token, params=params)

def get_prs_for_commit(owner, repo, commit_sha, token):
    # endpoint: GET /repos/{owner}/{repo}/commits/{commit_sha}/pulls
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits/{commit_sha}/pulls"
    # special preview header for this endpoint historically; safe to include
    accept = "application/vnd.github.groot-preview+json"
    try:
        prs = gh_get(url, token, params={"per_page":100}, accept=accept)
    except requests.HTTPError as e:
        # if commit not found, return empty
        return []
    return prs

def list_incidents(owner, repo, token):
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues"
    params = {"state": "all", "labels": "incident", "per_page": 100}
    return gh_get(url, token, params=params)

def parse_deployment_id_from_body(body):
    if not body:
        return None
    m = re.search(r"deployment_id\s*:\s*(\d+)", body)
    if m:
        return int(m.group(1))
    return None

def iso_str(dt):
    return dt.astimezone(timezone.utc).isoformat()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--days", type=int, default=30, help="période en jours (défaut 30)")
    ap.add_argument("--token-env", default="GITHUB_TOKEN", help="nom de la variable d'env contenant le token")
    args = ap.parse_args()

    token = os.environ.get(args.token_env)
    if not token:
        print(f"Erreur: la variable d'environnement {args.token_env} n'est définie. Crée un PAT et exporte-le.")
        sys.exit(2)

    owner, repo = args.repo.split("/")
    since_dt = datetime.now(timezone.utc) - timedelta(days=args.days)
    print(f"Collecte des données pour {args.repo} depuis {iso_str(since_dt)}...")

    deployments = list_deployments(owner, repo, token, since_dt=since_dt)
    print(f"Déploiements trouvés (filtrés): {len(deployments)}")

    # collect per-deployment info
    per_deploy = []
    for d in deployments:
        dep_id = d.get("id")
        dep_ref = d.get("ref") or d.get("sha") or ""
        dep_created = dateparse.parse(d.get("created_at"))
        # statuses
        try:
            statuses = get_deployment_statuses(owner, repo, dep_id, token)
        except requests.HTTPError as e:
            statuses = []
        last_status = statuses[0].get("state") if statuses else None
        # attempts to find PRs for commit/ref
        prs = []
        if dep_ref:
            try:
                prs = get_prs_for_commit(owner, repo, dep_ref, token)
            except Exception:
                prs = []
        # pick the first merged PR (if any)
        linked_pr = None
        for p in prs:
            if p.get("merged_at"):
                linked_pr = p
                break
        per_deploy.append({
            "id": dep_id,
            "ref": dep_ref,
            "created_at": dep_created,
            "last_status": last_status,
            "linked_pr": linked_pr
        })

    # incidents
    incidents_raw = list_incidents(owner, repo, token)
    incidents = []
    for it in incidents_raw:
        depid = parse_deployment_id_from_body(it.get("body","") or "")
        created_at = dateparse.parse(it.get("created_at"))
        closed_at = None
        if it.get("closed_at"):
            closed_at = dateparse.parse(it.get("closed_at"))
        incidents.append({
            "issue_number": it.get("number"),
            "title": it.get("title"),
            "deployment_id": depid,
            "created_at": created_at,
            "closed_at": closed_at
        })

    # Deployment Frequency (DF)
    df_count = len(per_deploy)
    df_per_day = df_count / max(1, args.days)
    print("\n--- Deployment Frequency (période {} jours) ---".format(args.days))
    print(f"Total deployments: {df_count}")
    print(f"Deployment per day (moyenne): {df_per_day:.3f}")

    # Lead Time (LT)
    lead_times_minutes = []
    for d in per_deploy:
        pr = d.get("linked_pr")
        if pr and pr.get("merged_at"):
            merged_at = dateparse.parse(pr.get("merged_at"))
            delta = d["created_at"] - merged_at
            lead_minutes = delta.total_seconds() / 60.0
            if lead_minutes >= 0:
                lead_times_minutes.append(lead_minutes)
    print("\n--- Lead Time for Changes (minutes) ---")
    if lead_times_minutes:
        print(f"Nombre de déploiements liés à un PR mergé: {len(lead_times_minutes)}")
        print(f"Médiane lead time (minutes): {statistics.median(lead_times_minutes):.1f}")
        print(f"Moyenne lead time (minutes): {statistics.mean(lead_times_minutes):.1f}")
    else:
        print("Aucun lead time calculable (pas de PR mergé identifié pour les déploiements).")

    # Change Failure Rate (CFR)
    # On considère failed si last_status != 'success' OR s'il existe un incident lié au deployment
    deployment_ids_with_incident = set([inc["deployment_id"] for inc in incidents if inc["deployment_id"]])
    failed_count = 0
    for d in per_deploy:
        failed = False
        if d["last_status"] and d["last_status"].lower() != "success":
            failed = True
        if d["id"] in deployment_ids_with_incident:
            failed = True
        if failed:
            failed_count += 1
    cfr = (failed_count / df_count) * 100 if df_count > 0 else 0.0
    print("\n--- Change Failure Rate (CFR) ---")
    print(f"Deployments with failure or incident: {failed_count}/{df_count} => {cfr:.2f}%")

    # MTTR: average duration of incidents linked to a deployment
    durations_minutes = []
    for inc in incidents:
        if inc["deployment_id"] and inc["closed_at"]:
            delta = inc["closed_at"] - inc["created_at"]
            durations_minutes.append(delta.total_seconds() / 60.0)
    print("\n--- Mean Time To Restore (MTTR) ---")
    if durations_minutes:
        print(f"Nombre d'incidents clos et liés à un déploiement: {len(durations_minutes)}")
        print(f"MTTR médian (minutes): {statistics.median(durations_minutes):.1f}")
        print(f"MTTR moyenne (minutes): {statistics.mean(durations_minutes):.1f}")
    else:
        print("Aucun incident clos lié à un déploiement pour calculer MTTR.")

    # Détails par déploiement (liste courte)
    print("\n--- Détails des déploiements (dernier {}) ---".format(args.days))
    for d in per_deploy:
        dep_id = d["id"]
        created = d["created_at"].isoformat()
        status = d["last_status"]
        pr_info = None
        if d["linked_pr"]:
            pr_info = f"PR#{d['linked_pr'].get('number')} merged_at={d['linked_pr'].get('merged_at')}"
        else:
            pr_info = "no linked merged PR found"
        incident_linked = "yes" if dep_id in deployment_ids_with_incident else "no"
        print(f"- id={dep_id} created_at={created} status={status} incident_linked={incident_linked} {pr_info}")

if __name__ == "__main__":
    main()