---
title: Cannabis Research Intelligence
emoji: 🌿
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
suggested_hardware: cpu-basic
pinned: false
license: mit
datasets:
  - mckenziansolutions/cannabis-papers-catalog
---

# Cannabis Research Intelligence

Searchable dashboard of cannabis / cannabinoid papers from PubMed. Classification uses **Maude** (deterministic), not a paid LLM.

## Where this runs

| Piece | Where | Cost |
| --- | --- | --- |
| Dashboard | Hugging Face Space [`mckenziansolutions/cannabis-paper-scraper`](https://huggingface.co/spaces/mckenziansolutions/cannabis-paper-scraper) at `https://mckenziansolutions-cannabis-paper-scraper.hf.space` | Creating a **Docker** Space requires Hugging Face **PRO**. CPU Basic hardware is $0/hour after that. |
| Catalog file | Hub dataset [`mckenziansolutions/cannabis-papers-catalog`](https://huggingface.co/datasets/mckenziansolutions/cannabis-papers-catalog) (`cannabis_papers.db`) | Free |
| Daily harvest | GitHub Actions `.github/workflows/daily-harvest.yml` | Free on a public repo |

The Space disk is wiped when the container sleeps. Every boot downloads `cannabis_papers.db` from the dataset (or Cloudflare R2 if those env vars are set). Harvest does **not** use Fly.

Hugging Face (as of 2026) does not offer free Docker Spaces. A free `mckenziansolutions` account can still hold the catalog dataset; the dashboard stays offline until Docker Space creation is allowed (PRO, or HF changing the policy).

## GitHub secrets

Add these on the GitHub repo (Settings → Secrets and variables → Actions):

| Secret | Required | Purpose |
| --- | --- | --- |
| `HF_TOKEN` | yes | Write token for the `mckenziansolutions` Hub account (deploy Space + upload dataset) |
| `CATALOG_RELOAD_TOKEN` | yes | Shared with the Space secret of the same name |
| `ENTREZ_EMAIL` | recommended | NCBI contact for PubMed |
| `NCBI_API_KEY` | optional | Higher PubMed rate limit |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `R2_ENDPOINT` / `R2_BUCKET` | optional | Cloudflare R2 instead of the Hub dataset |

Space variables (set by `scripts/deploy_hf_space.py`): `PORT=7860`, `DATABASE_PATH=/tmp/cannabis_papers.db`, `CHEAP_OPS=1`, `INPROCESS_DAILY_HARVEST=0`, `CATALOG_DATASET_ID=mckenziansolutions/cannabis-papers-catalog`.

## Operator commands

```bash
export HF_TOKEN=hf_...
python3 scripts/bootstrap_catalog_from_live.py --sqlite-path /tmp/cannabis_papers.db --upload
python3 scripts/deploy_hf_space.py
```

Harvest locally without publishing:

```bash
unset DATABASE_URL
python3 scripts/ci_daily_harvest.py --skip-store --sqlite-path /tmp/cannabis_papers.db
```
