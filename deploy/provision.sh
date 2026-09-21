#!/usr/bin/env bash
# Provision the unigate VM on GCE, per LLD-v2 §8: ONE e2-medium running the same
# docker compose stack as local dev. Not Cloud Run -- a global load balancer is
# ~$18/mo before traffic and Cloud SQL another ~$15, which triples the bill for
# an event that runs once.
#
# DRY RUN BY DEFAULT. Prints what it would create and the monthly cost, and
# touches nothing. Pass --apply to actually create. Idempotent either way: every
# step checks for an existing resource first, so re-running after a partial
# failure resumes rather than duplicating.
set -euo pipefail

PROJECT="${PROJECT:-my-gcp-project}"
REGION="${REGION:-asia-south1}"          # Mumbai, closest to SNU
ZONE="${ZONE:-asia-south1-a}"
VM="${VM:-unigate-vm}"
MACHINE="${MACHINE:-e2-medium}"          # 2 vCPU / 4GB -- LLD-v2 §9
DISK_GB="${DISK_GB:-20}"
SA="unigate-vm"
IP_NAME="unigate-ip"

APPLY=false
[[ "${1:-}" == "--apply" ]] && APPLY=true

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
step() { printf '  %s\n' "$*"; }
run() {
  if $APPLY; then "$@"; else printf '    would run: %s\n' "$*"; fi
}
exists() { "$@" >/dev/null 2>&1; }

say "unigate provisioning -- project=$PROJECT zone=$ZONE machine=$MACHINE"
if ! $APPLY; then
  cat <<'PLAN'

  DRY RUN. Nothing will be created. Re-run with --apply to create.

  Estimated monthly cost (asia-south1, on all month):
    e2-medium (2 vCPU, 4GB)          ~$27
    20GB pd-balanced boot disk       ~$2
    static external IP (while in use)  ~$3
    egress + Cloud Storage backups   ~$1
    ----------------------------------------
    TOTAL                            ~$33/month

  Stopping the VM between now and the event drops this to roughly $2/month
  (you keep paying for the disk and the reserved IP, not the compute).
PLAN
fi

say "1. Enable APIs (free)"
# `services enable` is idempotent server-side and accepts several at once, so
# enabling unconditionally is both correct and much faster than polling each.
APIS=(compute.googleapis.com iam.googleapis.com secretmanager.googleapis.com sheets.googleapis.com)
step "enable: ${APIS[*]}"
run gcloud services enable "${APIS[@]}" --project "$PROJECT"

say "2. Service account (free)"
SA_EMAIL="${SA}@${PROJECT}.iam.gserviceaccount.com"
if exists gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT"; then
  step "already exists: $SA_EMAIL"
else
  step "create: $SA_EMAIL"
  run gcloud iam service-accounts create "$SA" --project "$PROJECT" \
    --display-name "unigate VM runtime"
fi
step "NOTE: share the Google Sheet with $SA_EMAIL as Viewer (M3 ingestion,"
step "      so the app reads it with no JSON key file on disk)."

say "3. Firewall -- 80/443 only (free)"
if exists gcloud compute firewall-rules describe allow-web --project "$PROJECT"; then
  step "already exists: allow-web"
else
  step "create: allow-web (tcp:80,tcp:443 -> tag 'web')"
  run gcloud compute firewall-rules create allow-web --project "$PROJECT" \
    --allow=tcp:80,tcp:443 --target-tags=web \
    --description="unigate: public web only. Postgres and Redis stay inside the docker network."
fi
step "Port 5432 is deliberately NOT opened. Postgres is reachable only inside"
step "the compose network; exposing it is the fastest way to lose the database."

say "4. Static IP (~\$3/month while reserved)"
if exists gcloud compute addresses describe "$IP_NAME" --region "$REGION" --project "$PROJECT"; then
  step "already exists: $IP_NAME"
else
  step "create: $IP_NAME in $REGION"
  run gcloud compute addresses create "$IP_NAME" --region "$REGION" --project "$PROJECT"
fi

say "5. VM (~\$29/month for compute + disk)"
if exists gcloud compute instances describe "$VM" --zone "$ZONE" --project "$PROJECT"; then
  step "already exists: $VM"
else
  step "create: $VM ($MACHINE, ubuntu-2404-lts, ${DISK_GB}GB)"
  run gcloud compute instances create "$VM" --project "$PROJECT" --zone "$ZONE" \
    --machine-type="$MACHINE" \
    --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
    --boot-disk-size="${DISK_GB}GB" --boot-disk-type=pd-balanced \
    --address="$IP_NAME" --tags=web \
    --service-account="$SA_EMAIL" --scopes=cloud-platform \
    --metadata=enable-oslogin=TRUE
fi

if $APPLY; then
  IP=$(gcloud compute addresses describe "$IP_NAME" --region "$REGION" --project "$PROJECT" --format='value(address)')
  say "Done. Static IP: $IP"
  cat <<NEXT

  Next:
    1. Point DNS at $IP once the domain is bought (A record, apex and www).
    2. gcloud compute ssh $VM --zone $ZONE --project $PROJECT
    3. On the VM, run deploy/bootstrap-vm.sh
NEXT
else
  say "Dry run complete. Nothing was created. Re-run with --apply."
fi
