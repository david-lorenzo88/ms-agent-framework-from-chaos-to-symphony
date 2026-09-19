#!/usr/bin/env bash
#
# Deploy the showcase to Azure Container Apps.
#
#   az login
#   ./infra/deploy.sh
#
# The image is built in Azure by ACR, so Docker is not needed locally.
# Every step checks what already exists, so re-running resumes rather than
# starting over. Overridable by environment variable:
#
#   RESOURCE_GROUP=rg-chaos LOCATION=northeurope ./infra/deploy.sh
#
# NOTE ON `az containerapp up`: this script deliberately does not use it.
# It is a convenience wrapper that rejects the global az arguments, and its
# source-to-cloud path crashes inside the CLI's own queue_acr_build helper
# ("'NoneType' object has no attribute 'linux'"). The explicit commands below
# do the same three things - registry, build, app - and are the documented
# code-to-cloud path.
#
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-chaos-to-symphony}"
LOCATION="${LOCATION:-westeurope}"
ENVIRONMENT="${ENVIRONMENT:-env-chaos-to-symphony}"
APP_NAME="${APP_NAME:-chaos-to-symphony}"
IMAGE_NAME="${IMAGE_NAME:-chaos-to-symphony}"

# Two python processes in one container. 1 vCPU / 2Gi is comfortable; 0.5/1Gi
# is tight once DevUI has built all twelve workflows.
CPU="${CPU:-1.0}"
MEMORY="${MEMORY:-2.0Gi}"

# One replica always warm. Scale-to-zero is cheaper, but the first visitor then
# pays a cold start - not what you want when it is the person in row one.
MIN_REPLICAS="${MIN_REPLICAS:-1}"
MAX_REPLICAS="${MAX_REPLICAS:-3}"

# Offline by default: no keys, no model spend, and a public URL nobody can run
# up a bill on. See the README before changing this on a public app.
CHAOS_PROVIDER="${CHAOS_PROVIDER:-offline}"

command -v az >/dev/null || { echo "Azure CLI not found: https://aka.ms/azure-cli"; exit 1; }
az account show >/dev/null 2>&1 || { echo "Not logged in. Run: az login"; exit 1; }

SUBSCRIPTION_ID="$(az account show --query id -o tsv)"

echo "Subscription : $(az account show --query name -o tsv)"
echo "Resource grp : $RESOURCE_GROUP  ($LOCATION)"
echo "App          : $APP_NAME"
echo "Provider     : $CHAOS_PROVIDER"
echo

az extension add --name containerapp --upgrade --only-show-errors >/dev/null 2>&1 || true
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry; do
  az provider register --namespace "$ns" --only-show-errors >/dev/null 2>&1 || true
done

az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --only-show-errors -o none

# ---------------------------------------------------------------------------
# 1. Managed environment
#
# Created with --no-wait and polled in short calls. Letting the CLI hold one
# long poll means a single dropped connection to management.azure.com kills
# the deploy with a traceback, even though the environment is provisioning
# happily server-side.
# ---------------------------------------------------------------------------

env_state() {
  az containerapp env show --name "$ENVIRONMENT" --resource-group "$RESOURCE_GROUP" \
     --query properties.provisioningState -o tsv --only-show-errors 2>/dev/null || true
}

STATE="$(env_state)"
if [ -z "$STATE" ]; then
  echo "Creating the Container Apps environment (a few minutes)..."
  az containerapp env create --name "$ENVIRONMENT" --resource-group "$RESOURCE_GROUP" \
     --location "$LOCATION" --no-wait --only-show-errors -o none
else
  echo "Environment exists (state: $STATE)"
fi

if [ "$STATE" != "Succeeded" ]; then
  printf "Waiting for the environment"
  for _ in $(seq 1 90); do
    STATE="$(env_state)"
    case "$STATE" in
      Succeeded) break ;;
      Failed|Canceled) echo; echo "Environment provisioning reported: $STATE"; exit 1 ;;
      *) printf "." ;;
    esac
    sleep 10
  done
  echo
  [ "$STATE" = "Succeeded" ] || {
    echo "Environment not ready (last state: ${STATE:-unknown}). Re-run to resume."; exit 1; }
  echo "Environment ready."
fi

# ---------------------------------------------------------------------------
# 2. Container registry
# ---------------------------------------------------------------------------

ACR_NAME="${ACR_NAME:-$(az acr list --resource-group "$RESOURCE_GROUP" \
           --query "[0].name" -o tsv --only-show-errors 2>/dev/null || true)}"

if [ -z "$ACR_NAME" ]; then
  # Registry names are globally unique and alphanumeric only, so derive a
  # stable one from the subscription and group rather than a random suffix -
  # that way a re-run finds the same registry instead of making another.
  SUFFIX="$(printf '%s' "${SUBSCRIPTION_ID}${RESOURCE_GROUP}" \
            | openssl dgst -sha256 | awk '{print $NF}' | cut -c1-12)"
  ACR_NAME="acr${SUFFIX}"
  echo "Creating container registry $ACR_NAME..."
  az acr create --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" \
     --sku Basic --admin-enabled true --only-show-errors -o none
else
  echo "Registry: $ACR_NAME"
fi

# ---------------------------------------------------------------------------
# 3. Build, in Azure
# ---------------------------------------------------------------------------

TAG="$(date -u +%Y%m%d%H%M%S)"
IMAGE="${ACR_NAME}.azurecr.io/${IMAGE_NAME}:${TAG}"

echo
echo "Building ${IMAGE_NAME}:${TAG} in ACR (first build takes a few minutes)..."
echo
az acr build --registry "$ACR_NAME" --image "${IMAGE_NAME}:${TAG}" --file Dockerfile .

# ---------------------------------------------------------------------------
# 4. The app
# ---------------------------------------------------------------------------

ACR_USER="$(az acr credential show --name "$ACR_NAME" --query username -o tsv --only-show-errors)"
ACR_PASS="$(az acr credential show --name "$ACR_NAME" --query 'passwords[0].value' -o tsv --only-show-errors)"

ENV_VARS=(
  "CHAOS_PROVIDER=$CHAOS_PROVIDER"
  "CHAOS_PROXY_DEVUI=1"
  "CHAOS_HOST=0.0.0.0"
  "SHOWCASE_PORT=8000"
  "DEVUI_PORT=8080"
)

echo
if az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
     -o none --only-show-errors 2>/dev/null; then
  echo "Updating $APP_NAME..."
  # Registry credentials are configured on the app already; update only needs
  # to be told which image to move to.
  az containerapp registry set --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
     --server "${ACR_NAME}.azurecr.io" --username "$ACR_USER" --password "$ACR_PASS" \
     --only-show-errors -o none
  az containerapp update --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
     --image "$IMAGE" \
     --cpu "$CPU" --memory "$MEMORY" \
     --min-replicas "$MIN_REPLICAS" --max-replicas "$MAX_REPLICAS" \
     --set-env-vars "${ENV_VARS[@]}" \
     --only-show-errors -o none
else
  echo "Creating $APP_NAME..."
  az containerapp create --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
     --environment "$ENVIRONMENT" \
     --image "$IMAGE" \
     --target-port 8000 --ingress external \
     --registry-server "${ACR_NAME}.azurecr.io" \
     --registry-username "$ACR_USER" --registry-password "$ACR_PASS" \
     --cpu "$CPU" --memory "$MEMORY" \
     --min-replicas "$MIN_REPLICAS" --max-replicas "$MAX_REPLICAS" \
     --env-vars "${ENV_VARS[@]}" \
     --only-show-errors -o none
fi

FQDN="$(az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
        --query properties.configuration.ingress.fqdn -o tsv --only-show-errors)"

[ -n "$FQDN" ] || { echo "Deployed, but no ingress hostname came back."; exit 1; }

URL="https://${FQDN}"
echo
echo "  $URL"
echo

# Don't just print a URL and claim success - a crash-looping container would
# otherwise look like a good deploy.
printf "Waiting for it to answer"
for _ in $(seq 1 40); do
  if curl -fsS --max-time 5 "${URL}/api/health" >/dev/null 2>&1; then
    echo
    echo "Healthy:"
    curl -fsS "${URL}/api/health"; echo
    echo
    echo "  Showcase : $URL"
    echo "  DevUI    : ${URL}/devui/"
    echo "  Logs     : az containerapp logs show -n $APP_NAME -g $RESOURCE_GROUP --follow"
    echo "  Tear down: az group delete -n $RESOURCE_GROUP --yes --no-wait"
    exit 0
  fi
  printf "."
  sleep 5
done

echo
echo "Deployed but /api/health did not answer within 200s. Look at the logs:"
echo "  az containerapp logs show -n $APP_NAME -g $RESOURCE_GROUP --follow"
exit 1
