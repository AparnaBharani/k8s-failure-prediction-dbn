#!/usr/bin/env bash
# Deploy 20-30 TrainTicket Core Microservices via Helm with memory caps (128MB-256MB)
echo "Deploying TrainTicket Core Suite to local Kubernetes..."

kubectl create namespace trainticket || true

# Deploy core services
helm install trainticket-core ./helm/trainticket-core \
  --namespace trainticket \
  --set global.resources.limits.memory=256Mi \
  --set global.resources.requests.memory=128Mi

# Enable HPA on core stateless services
kubectl autoscale deployment ts-train-service -n trainticket --cpu-percent=60 --min=2 --max=10
kubectl autoscale deployment ts-user-service -n trainticket --cpu-percent=60 --min=2 --max=10
kubectl autoscale deployment ts-order-service -n trainticket --cpu-percent=60 --min=2 --max=10
kubectl autoscale deployment ts-station-service -n trainticket --cpu-percent=60 --min=2 --max=10

echo "TrainTicket Core Suite deployed! Checking status..."
kubectl get pods -n trainticket
