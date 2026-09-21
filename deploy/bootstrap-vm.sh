#!/usr/bin/env bash
# Run ONCE on the VM, over SSH, to turn a bare Ubuntu box into a host that can
# run the unigate compose stack. Idempotent -- safe to re-run.
set -euo pipefail

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1. Docker"
if command -v docker >/dev/null 2>&1; then
  echo "  already installed: $(docker --version)"
else
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "  installed. Log out and back in for the group to take effect."
fi

say "2. Swap"
# e2-medium has 4GB. Postgres plus the API plus an image build can spike past
# that, and the OOM killer takes the database first. 2GB of swap is cheap
# insurance against losing Postgres mid-build.
if sudo swapon --show | grep -q /swapfile; then
  echo "  already active"
else
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo "  2GB swap added and made persistent"
fi

say "3. Unattended security updates"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unattended-upgrades >/dev/null
echo "  enabled"

say "4. Repo"
if [[ -d ~/unigatev1/.git ]]; then
  echo "  already cloned"
else
  echo "  Clone it yourself with a read-only deploy key:"
  echo "    ssh-keygen -t ed25519 -C unigate-vm -f ~/.ssh/id_ed25519 -N ''"
  echo "    cat ~/.ssh/id_ed25519.pub   # add to GitHub repo > Deploy keys (read-only)"
  echo "    git clone git@github.com:p4ralyn/unigatev1.git ~/unigatev1"
fi

say "Done."
cat <<'NEXT'
  Next:
    1. Create ~/unigatev1/.env on the VM. Never commit it.
       APP_ENV=prod  COOKIE_SECURE=true  AUTH_PROVIDER=google
       APP_BASE_URL=https://<your-domain>
       plus SECRET_KEY, GOOGLE_CLIENT_ID/SECRET, DB and SMTP values.
       APP_ENV=prod makes Settings refuse to boot with the dev auth provider
       or with insecure cookies -- that guard is deliberate.
    2. ./deploy/deploy.sh
NEXT
