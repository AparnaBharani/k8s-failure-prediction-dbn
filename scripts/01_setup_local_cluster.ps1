# PowerShell script for setting up local Minikube cluster with 10GB RAM allocation
Write-Host "Starting Minikube local Kubernetes cluster for TrainTicket (10GB RAM, 4 CPUs)..." -ForegroundColor Green

minikube start --cpus=4 --memory=10240 --driver=docker --addons=ingress,metrics-server

Write-Host "Cluster started successfully! Verifying cluster info..." -ForegroundColor Green
kubectl cluster-info
kubectl get nodes
