#!/usr/bin/env bash
#
# Deploy the showcase to Azure Container Apps.
#
#   az login
#   ./infra/deploy.sh
#
# Builds the image in Azure (ACR build tasks), so Docker is not needed locally.
# Everything is overridable by environment variable:
#
#   RESOURCE_GROUP=rg-chaos LOCATION=westeurope ./infra/deploy.sh
#
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-chaos-to-symphony}"
LOCATION="${LOCATION:-westeurope}"
ENVIRONMENT="${ENVIRONMENT:-env-chaos-to-symphony}"
APP_NAME="${APP_NAME:-chaos-to-symphony}"

# Two python processes in one container. 1 vCPU / 2Gi is comfortable; 0.5/1Gi
# is tight once DevUI has built all twelve workflows.
CPU="${CPU:-1.0}"
MEMORY="${MEMORY:-2.0Gi}"

# One replica always warm. Scale-to-zero is cheaper, but the first visitor then
# pays a cold start - not what you want when it is the person in row one.
# After the conference:  MIN_REPLICAS=0 ./infra/deploy.sh
MIN_REPLICAS="${MIN_REPLICAS:-1}"
MAX_REPLICAS="${MAX_REPLICAS:-3}"

# Offline by default: no keys, no model spend, and a public URL nobody can run
# up a bill on. See the README before changing this on a public app.
CHAOS_PROVIDER="${CHAOS_PROVIDER:-offline}"

command -v az >/dev/null || { echo "Azure CLI not found: https://aka.ms/azure-cli"; exit 1; }
az account show >/dev/null 2>&1 || { echo "Not logged in. Run: az login"; exit 1; }

echo "Subscription : $(az account show --query name -o tsv)"
echo "Resource grp : $RESOURCE_GROUP  ($LOCATION)"
echo "App          : $APP_NAME"
echo "Provider     : $CHAOS_PROVIDER"
echo

az extension add --name containerapp --upgrade --only-show-errors >/dev/null 2>&1 || true
az provider register --namespace Microsoft.App --only-show-errors >/dev/null 2>&1 || true
az provider register --namespace Microsoft.OperationalInsights --only-show-errors >/dev/null 2>&1 || true

az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --only-show-errors -o none

# ---------------------------------------------------------------------------
# The managed environment, created separately and on purpose.
#
# Letting `up` create it means the CLI holds one long poll against
# management.azure.com for several minutes, and a single dropped connection
# takes the whole deploy down with a traceback - even though the environment
# is provisioning happily server-side. Creating it with --no-wait and polling
# in short calls survives that: each probe is a fresh request, and a failed
# one is just a dot.
# ---------------------------------------------------------------------------

env_state() {
  az containerapp env show --name "$ENVIRONMENT" --resource-group "$RESOURCE_GROUP" \
     --query properties.provisioningState -o tsv --only-show-errors 2>/dev/null || true
}

STATE="$(env_state)"

if [ -z "$STATE" ]; then
  echo "Creating the Container Apps environment (this takes a few minutes)..."
  az containerapp env create \
    --name "$ENVIRONMENT" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --no-wait --only-show-errors -o none
else
  echo "Environment already exists (state: $STATE)"
fi

if [ "$STATE" != "Succeeded" ]; then
  printf "Waiting for the environment"
  for _ in $(seq 1 90); do          # up to ~15 minutes
    STATE="$(env_state)"
    case "$STATE" in
      Succeeded) break ;;
      Failed|Canceled)
        echo
        echo "Environment provisioning reported: $STATE"
        echo "  az containerapp env show -n $ENVIRONMENT -g $RESOURCE_GROUP"
        exit 1 ;;
      *) printf "." ;;              # in progress, not visible yet, or a network blip
    esac
    sleep 10
  done
  echo
  if [ "$STATE" != "Succeeded" ]; then
    echo "The environment did not reach Succeeded in time (last state: ${STATE:-unknown})."
    echo "It may still be provisioning - re-run this script, it picks up where it left off."
    exit 1
  fi
  echo "Environment ready."
fi

echo
echo "Building in Azure and deploying (first run takes a few minutes)..."
echo "If this drops out on a network error, just run the script again - every"
echo "step is idempotent and it will resume from here."
echo

# `az containerapp up` is not a normal command: it rejects the global
# arguments (--only-show-errors, --output). Pass it only its own parameters.
# Its output is left on screen on purpose - it is the build log, and it is the
# only thing worth reading when a deploy goes wrong.
az containerapp up \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --environment "$ENVIRONMENT" \
  --source . \
  --ingress external \
  --target-port 8000 \
  --env-vars \
      CHAOS_PROVIDER="$CHAOS_PROVIDER" \
      CHAOS_PROXY_DEVUI=1 \
      CHAOS_HOST=0.0.0.0 \
      SHOWCASE_PORT=8000 \
      DEVUI_PORT=8080

# Sizing and scale are not parameters of `up`, so they need a second pass.
echo
echo "Applying sizing and scale..."
az containerapp update \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --cpu "$CPU" --memory "$MEMORY" \
  --min-replicas "$MIN_REPLICAS" --max-replicas "$MAX_REPLICAS" \
  --only-show-errors -o none

FQDN=$(az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
        --query properties.configuration.ingress.fqdn -o tsv --only-show-errors)

if [ -z "$FQDN" ]; then
  echo "Deployed, but no ingress hostname came back. Check:"
  echo "  az containerapp show -n $APP_NAME -g $RESOURCE_GROUP"
  exit 1
fi

URL="https://${FQDN}"
echo
echo "  $URL"
echo

# Don't just print a URL and claim success - the revision needs a moment, and a
# container that crash-loops would otherwise look like a good deploy.
echo -n "Waiting for it to answer"
for _ in $(seq 1 30); do
  if curl -fsS --max-time 5 "${URL}/api/health" >/dev/null 2>&1; then
    echo
    echo "Healthy:"
    curl -fsS "${URL}/api/health"
    echo
    echo
    echo "  Showcase : $URL"
    echo "  DevUI    : ${URL}/devui/"
    echo "  Logs     : az containerapp logs show -n $APP_NAME -g $RESOURCE_GROUP --follow"
    echo "  Tear down: az group delete -n $RESOURCE_GROUP --yes --no-wait"
    exit 0
  fi
  echo -n "."
  sleep 5
done

echo
echo "It deployed but did not answer /api/health within 150s. Look at the logs:"
echo "  az containerapp logs show -n $APP_NAME -g $RESOURCE_GROUP --follow"
exit 1
