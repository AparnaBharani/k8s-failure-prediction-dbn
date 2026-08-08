"""
PREFACE-DDN End-to-End Pipeline Demonstration Script
Loads/generates metric snapshots, runs RECTIFIER -> Autoencoder -> DDN Core -> K8s Controller.
Demonstrates the Intervention Utility Test (Reschedule vs Restart) decision loop.
"""

import sys
import os
import numpy as np
import pandas as pd
import networkx as nx

# Add src to Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.rectifier import Rectifier
from src.autoencoder import AnomalyScorePipeline
from src.ddn_core import DynamicDecisionNetwork
from src.controller import KubernetesActionController

SERVICES = [
    "ts-train-service", "ts-user-service", "ts-order-service",
    "ts-station-service", "ts-travel-service", "ts-payment-service"
]

POD_KPIS = ["cpu_usage", "memory_bytes"]
NODE_KPIS = ["node_cpu"]

def build_sample_service_graph() -> nx.DiGraph:
    """Builds TrainTicket core service call graph (DAG)."""
    G = nx.DiGraph()
    G.add_edge("ts-user-service", "ts-order-service")
    G.add_edge("ts-order-service", "ts-station-service")
    G.add_edge("ts-order-service", "ts-train-service")
    G.add_edge("ts-train-service", "ts-travel-service")
    G.add_edge("ts-order-service", "ts-payment-service")
    return G

def main():
    print("================================================================")
    print(" PREFACE-DDN: End-to-End Pipeline Execution Demonstration")
    print("================================================================")

    # 1. Initialize Pipeline Modules
    rectifier = Rectifier(SERVICES, POD_KPIS, NODE_KPIS)
    autoencoder_pipe = AnomalyScorePipeline(rectifier.feature_names, SERVICES)
    service_graph = build_sample_service_graph()
    ddn_engine = DynamicDecisionNetwork(service_graph)
    controller = KubernetesActionController(shadow_mode=True, cooldown_seconds=60)

    # 2. Generate Synthetic Baseline Data & Train Autoencoder
    print(f"\n[Stage 1 & 2] Training Autoencoder on healthy baseline features (Dim = {rectifier.input_width})...")
    healthy_data = np.random.normal(loc=1.0, scale=0.1, size=(500, rectifier.input_width)).astype(np.float32)
    autoencoder_pipe.train_autoencoder(healthy_data, epochs=10)
    print("Autoencoder trained successfully!")

    # 3. Simulate Telemetry Ticks (Healthy -> Degrading -> Fault Injected)
    print("\n[Stage 3 & 4] Running 1-Minute Monitoring Ticks with DDN & Intervention Utility Test...")

    for tick in range(1, 6):
        print(f"\n--- Monitoring Tick {tick} ---")
        
        # Create synthetic pod raw metric sample dataframe
        records = []
        is_fault_tick = (tick >= 3)
        for s in SERVICES:
            for p in range(3): # 3 pods per service
                base_cpu = 0.2 if not (is_fault_tick and s == "ts-order-service") else 0.85
                base_mem = 100.0 if not (is_fault_tick and s == "ts-order-service") else 450.0
                records.append({
                    "pod_name": f"{s}-pod-{p}",
                    "service_name": s,
                    "pod_phase": "Running",
                    "is_ready": True,
                    "kpi_name": "cpu_usage",
                    "value": base_cpu + np.random.uniform(-0.02, 0.02)
                })
                records.append({
                    "pod_name": f"{s}-pod-{p}",
                    "service_name": s,
                    "pod_phase": "Running",
                    "is_ready": True,
                    "kpi_name": "memory_bytes",
                    "value": base_mem + np.random.uniform(-5.0, 5.0)
                })

        raw_df = pd.DataFrame(records)

        # Step 1: RECTIFIER
        x_t, imputed = rectifier.process_tick(raw_df)

        # Step 2: Autoencoder Anomaly Signals
        anomaly_signals = autoencoder_pipe.compute_anomaly_signals(x_t)

        # Step 3: DDN Particle Filter State Update & Utility Calculation
        node_pressure_flag = (tick >= 4) # Simulate physical node pressure on tick 4+
        ddn_output = ddn_engine.step(anomaly_signals, node_pressure_flag=node_pressure_flag)

        # Step 4: K8s Action Controller Reconciliation & Intervention Utility Test
        controller.reconcile_tick(ddn_output, node_pressure_flag=node_pressure_flag)

    print("\n================================================================")
    print(" Pipeline Demonstration Completed Successfully!")
    print("================================================================")

if __name__ == "__main__":
    main()
