# PREFACE-DDN — Execution Guide & Handbook for Project Setup

Welcome to the **PREFACE-DDN** repository! This handbook provides step-by-step instructions to run the entire project—from local Kubernetes cluster setup and TrainTicket benchmark deployment to data collection, AI training, and the **Intervention Utility Test (Reschedule vs. Restart)** controller.

---

## 📁 Repository Structure Overview

```
d:\S5\PR\
├── 1_PREFACE-DBN_Executive_Project_Plan (1).md   # Executive vision & goals
├── 2_PREFACE-DBN_PRD.md                           # Product requirements & specs
├── 3_PREFACE-DBN_Technical_Architecture (1).md    # Full technical design & math
├── 4_PREFACE-DBN_Implementation_Roadmap (1).md   # Phased execution plan
├── README.md                                      # Execution guide (This file)
├── requirements.txt                               # Python dependencies
├── scripts/                                       # Execution scripts
│   ├── 01_setup_local_cluster.ps1                 # Powershell script to start Minikube (10GB RAM)
│   ├── 02_deploy_trainticket_core.sh              # Deploy 20-30 TrainTicket core microservices
│   ├── 03_deploy_telemetry.sh                     # Install minimal Istio & Prometheus
│   ├── 04_run_locust_load.py                      # Locust traffic generator script
│   ├── 05_inject_chaos_faults.sh                  # Chaos Mesh CPU/Memory/Network fault injector
│   ├── 06_collect_telemetry_dataset.py            # Prometheus metrics scraper
│   └── 07_run_pipeline.py                         # End-to-end pipeline runner
└── src/                                           # Python AI & Controller Core
    ├── __init__.py
    ├── rectifier.py                                # Module 1: RECTIFIER preprocessor (7 stats)
    ├── autoencoder.py                              # Module 2: PyTorch Autoencoder anomaly scorer
    ├── ddn_core.py                                 # Module 3: Dynamic Decision Network & JAX Filter
    └── controller.py                               # Module 4: K8s Action Controller & Utility Test
```

---

## 🛠️ Step 1: System Requirements & Tool Installation

Make sure your machine has:
- **RAM:** 16 GB RAM (allocated ~10 GB to Kubernetes)
- **OS:** Windows 10/11, macOS, or Linux

### Install Free Tools:
1. **Docker Desktop:** Download & install from [docker.com](https://www.docker.com/). Ensure Docker service is running.
2. **Minikube:** Install via Powershell `winget install Kubernetes.minikube` or download from [minikube.sigs.k8s.io](https://minikube.sigs.k8s.io/docs/start/).
3. **kubectl & Helm:** Install via Powershell `winget install Kubernetes.kubectl` and `winget install Helm.Helm`.
4. **Python 3.10+:** Ensure Python is installed.

---

## 🚀 Step 2: Setting Up Python Environment & Local Cluster

### 2.1 Install Python Dependencies
Open PowerShell or Command Prompt in the unzipped project folder:
```powershell
pip install -r requirements.txt
```

### 2.2 Launch Local Kubernetes Cluster (10GB RAM Allocation)
Run the automated setup script:
```powershell
# In PowerShell:
.\scripts\01_setup_local_cluster.ps1
```
*(Or manually run: `minikube start --cpus=4 --memory=10240 --driver=docker`)*

---

## 🚆 Step 3: Deploying TrainTicket Core Benchmark & Telemetry

### 3.1 Deploy TrainTicket Core Microservices (20–30 Core Services)
```bash
bash ./scripts/02_deploy_trainticket_core.sh
```

### 3.2 Deploy Istio & Prometheus Telemetry
```bash
bash ./scripts/03_deploy_telemetry.sh
```

---

## ⚡ Step 4: Generating Traffic & Injecting Faults

### 4.1 Run Locust Traffic Generator
In a new terminal window:
```powershell
locust -f ./scripts/04_run_locust_load.py --host=http://localhost:8080 --users 50 --spawn-rate 5
```

### 4.2 Inject Chaos Mesh Faults (CPU Stress, Memory Leaks, Network Delays)
In another terminal window:
```bash
bash ./scripts/05_inject_chaos_faults.sh
```

---

## 📊 Step 5: Collecting Telemetry & Running the AI Pipeline

### 5.1 Collect 1-Minute Prometheus Telemetry Dataset
```powershell
python ./scripts/06_collect_telemetry_dataset.py
```
This saves `trainticket_telemetry_dataset.csv` containing healthy and fault-injected metric ticks.

### 5.2 Run End-to-End PREFACE-DDN AI & Intervention Utility Test Pipeline
```powershell
python ./scripts/07_run_pipeline.py
```

### What You Will See:
- **RECTIFIER:** Collapses variable pod metrics into a fixed-width vector $x_t$.
- **Autoencoder:** Computes continuous anomaly signals $a_t^s$.
- **DDN Particle Filter:** Updates belief probabilities ($P(Normal), P(Degrading), P(Critical)$) and localizes the upstream root cause.
- **Intervention Utility Test:** Computes $\Delta EU = EU(\text{Reschedule}) - EU(\text{Restart})$ and outputs the Maximum Expected Utility (MEU) decision rule!

---

## 💡 Support & Notes
- **Shadow Mode:** The controller runs in `Shadow Mode` by default, logging expected utility decisions without modifying cluster state.
- **100% Free:** Everything runs locally on your machine for $0 cost.
