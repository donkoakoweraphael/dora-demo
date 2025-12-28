#!/usr/bin/env python3
"""
Simple visualizer for DORA metrics.
Usage:
  export GITHUB_TOKEN="ghp_..."
  python scripts/plot_dora.py --repo donkoakoweraphael/dora-demo --days 30

Sorties:
  output/deployments_per_day.png
  output/leadtime_hist.png
  output/cfr_mttr_summary.txt
"""
import os
import argparse
import requests
from datetime import datetime, timedelta, timezone
from dateutil import parser as dateparse
import pandas as pd
import matplotlib.pyplot as plt
import re

API = "https://api.github.com"

def gh_get(url, token, params=None, accept=None):
    headers = {"Authorization": f"token {token}", "Accept": accept or "application/vnd.github+json"}
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    return r.json()

def list_deployments(owner, repo, token, since=None):
    url = f"{API}/repos/{owner}/{repo}/deployments"
    params = {"per_page": 100}
    page = 1
    out = []
    while True:
        params["page"]=page
        items = gh_get(url, token, params=params)
        if not items:
            break
        for it in items:
            dt = dateparse.parse(it.get("created_at"))
            if since and dt < since:
                continue
            out.append(it)
        page += 1
        if len(items) < 100:
            break
    return out

def get_deployment_statuses(owner, repo, id_, token):
    url = f"{API}/repos/{owner}/{repo}/deployments/{id_}/statuses"
    return gh_get(url, token, params={"per_page":100})

def get_prs_for_commit(owner, repo, sha, token):
    url = f"{API}/repos/{owner}/{repo}/commits/{sha}/pulls"
    accept = "application/vnd.github.groot-preview+json"
    try:
        return gh_get(url, token, params={"per_page":100}, accept=accept)
    except:
        return []

def list_incidents(owner, repo, token):
    url = f"{API}/repos/{owner}/{repo}/issues"
    return gh_get(url, token, params={"state":"all","labels":"incident","per_page":100})

def parse_deployment_id(body):
    if not body: return None
    m = re.search(r"deployment_id\s*:\s*(\d+)", body)
    return int(m.group(1)) if m else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--token-env", default="GITHUB_TOKEN")
    args = ap.parse_args()
    token = os.environ.get(args.token_env)
    if not token:
        print("Exporte ton token dans la variable d'environnement GITHUB_TOKEN (ou change token-env).")
        return
    owner, repo = args.repo.split("/")
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    print("Collecte des déploiements...")
    deps = list_deployments(owner, repo, token, since=since)
    if not deps:
        print("Aucun déploiement trouvé pour la période.")
        return
    # DataFrame for deployments per day
    rows = []
    lead_minutes = []
    failed_flags = []
    for d in deps:
        dep_id = d.get("id")
        created = dateparse.parse(d.get("created_at"))
        sha = d.get("ref") or d.get("sha") or ""
        statuses = []
        try:
            statuses = get_deployment_statuses(owner, repo, dep_id, token)
        except:
            statuses = []
        last_status = statuses[0].get("state") if statuses else None
        prs = []
        if sha:
            prs = get_prs_for_commit(owner, repo, sha, token)
        linked_pr = None
        for p in prs:
            if p.get("merged_at"):
                linked_pr = p
                break
        lt = None
        if linked_pr:
            merged_at = dateparse.parse(linked_pr.get("merged_at"))
            delta = created - merged_at
            lt = delta.total_seconds()/60.0
            if lt >= 0:
                lead_minutes.append(lt)
        rows.append({"id":dep_id, "created":created.date(), "last_status": last_status, "lead_min": lt})
        failed = (last_status and last_status.lower() != "success")
        failed_flags.append(int(failed))
    df = pd.DataFrame(rows)
    # deployments per day
    dpd = df.groupby("created").size().reset_index(name="deployments")
    os.makedirs("output", exist_ok=True)
    plt.figure(figsize=(8,4))
    plt.bar(dpd["created"].astype(str), dpd["deployments"], color="tab:blue")
    plt.xticks(rotation=45)
    plt.title("Deployments per day")
    plt.tight_layout()
    plt.savefig("output/deployments_per_day.png")
    print("Saved output/deployments_per_day.png")
    # lead time histogram
    if lead_minutes:
        plt.figure(figsize=(6,4))
        plt.hist(lead_minutes, bins=20, color="tab:green")
        plt.title("Lead time for changes (minutes)")
        plt.xlabel("minutes")
        plt.ylabel("count")
        plt.tight_layout()
        plt.savefig("output/leadtime_hist.png")
        print("Saved output/leadtime_hist.png")
    else:
        print("No lead time data to plot.")
    # CFR basic
    total = len(df)
    failed = sum(1 for f in failed_flags if f)
    cfr = 100.0*failed/total if total>0 else 0.0
    # MTTR: get incidents and compute durations for those linked to deployments
    incidents = list_incidents(owner, repo, token)
    durations = []
    for it in incidents:
        depid = parse_deployment_id(it.get("body","") or "")
        if depid:
            created = dateparse.parse(it.get("created_at"))
            if it.get("closed_at"):
                closed = dateparse.parse(it.get("closed_at"))
                durations.append((closed-created).total_seconds()/60.0)
    mttr = sum(durations)/len(durations) if durations else None
    # save summary
    with open("output/cfr_mttr_summary.txt","w") as f:
        f.write(f"Total deployments: {total}\n")
        f.write(f"Failed deployments: {failed}\n")
        f.write(f"CFR (%): {cfr:.2f}\n")
        if mttr is not None:
            f.write(f"MTTR (minutes avg): {mttr:.1f}\n")
        else:
            f.write("MTTR: no closed incidents linked to deployments found\n")
    print("Saved output/cfr_mttr_summary.txt")
    print("Done.")

if __name__ == '__main__':
    main()