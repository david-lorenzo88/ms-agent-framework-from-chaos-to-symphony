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

echo "Building in Azure and deploying (first run takes a few minutes)..."
az containerapp up \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --environment "$ENVIRONMENT" \
  --source . \
  --ingress external \
  --target-port 8000 \
  --only-show-errors

# `up` does not take these, so apply them in a second pass.
az containerapp update \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --cpu "$CPU" --memory "$MEMORY" \
  --min-replicas "$MIN_REPLICAS" --max-replicas "$MAX_REPLICAS" \
  --set-env-vars \
      CHAOS_PROVIDER="$CHAOS_PROVIDER" \
      CHAOS_PROXY_DEVUI=1 \
      CHAOS_HOST=0.0.0.0 \
      SHOWCASE_PORT=8000 \
      DEVUI_PORT=8080 \
  --only-show-errors -o none

FQDN=$(az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
        --query properties.configuration.ingress.fqdn -o tsv)

echo
echo "  https://${FQDN}"
echo
echo "Check it:   curl -s https://${FQDN}/api/health"
echo "Logs:       az containerapp logs show -n $APP_NAME -g $RESOURCE_GROUP --follow"
echo "Tear down:  az group delete -n $RESOURCE_GROUP --yes --no-wait"
