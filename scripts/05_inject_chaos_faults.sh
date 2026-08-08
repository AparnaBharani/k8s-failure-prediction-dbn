#!/usr/bin/env bash
# Inject Chaos Mesh Faults: CPU Stress, Memory Leak, and Network Latency
echo "Installing Chaos Mesh..."
helm repo add chaos-mesh https://charts.chaos-mesh.org
helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh --create-namespace || true

echo "Applying CPU Stress Fault on ts-train-service..."
cat <<EOF | kubectl apply -f -
apiVersion: chaos-mesh.org/v1alpha1
kind: StressChaos
metadata:
  name: cpu-stress-train
  namespace: trainticket
spec:
  mode: one
  selector:
    namespaces:
      - trainticket
    labelSelectors:
      'app': 'ts-train-service'
  stressors:
    cpu:
      workers: 2
      load: 80
  duration: '10m'
EOF

echo "Applying Network Latency Fault on ts-station-service..."
cat <<EOF | kubectl apply -f -
apiVersion: chaos-mesh.org/v1alpha1
kind: NetworkChaos
metadata:
  name: network-delay-station
  namespace: trainticket
spec:
  action: delay
  mode: one
  selector:
    namespaces:
      - trainticket
    labelSelectors:
      'app': 'ts-station-service'
  delay:
    latency: '300ms'
    jitter: '50ms'
  duration: '10m'
EOF

echo "Fault injection active!"
