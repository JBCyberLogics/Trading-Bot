#!/bin/bash
# Production deployment script

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

echo "Deploying SMC Trading Bot to production"

if ! command -v ansible-playbook >/dev/null 2>&1; then
  echo "ansible-playbook not found. Install Ansible first."
  exit 1
fi

if [ ! -f config/.env.production ]; then
  echo "config/.env.production not found"
  exit 1
fi

source venv/bin/activate
python scripts/validate_for_live.py

if [ ! -f deploy/ansible/inventory ]; then
  echo "deploy/ansible/inventory not found"
  echo "Copy deploy/ansible/inventory.example to deploy/ansible/inventory and edit host/user/key."
  exit 1
fi

cd deploy/ansible
ansible-playbook -i inventory deploy.yml

echo "Deployment completed"
