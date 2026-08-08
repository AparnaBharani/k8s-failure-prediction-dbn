#!/usr/bin/env bash
# Deploy Istio Minimal Profile and Prometheus Monitoring Stack
echo "Installing Istio Minimal Profile for Service Graph Telemetry..."
istioctl install --set profile=minimal -y
kubectl label namespace trainticket istio-injection=enabled --overwrite

echo "Deploying Prometheus & Grafana monitoring stack..."
kubectl create namespace monitoring || true
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install prometheus prometheus-community/kube-prometheus-stack -n monitoring

echo "Telemetry stack deployed!"
