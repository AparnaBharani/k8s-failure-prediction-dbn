"""
PREFACE-DDN Module 1: The RECTIFIER
Transforms dynamic, variable-length Kubernetes pod telemetry into a fixed-width
statistical feature vector (x_t) per microservice per KPI tick.
"""

import numpy as np
import pandas as pd
import polars as pl
from typing import Dict, List, Tuple, Any

# Core statistics set emitted per (microservice, KPI)
STATS_LIST = ["mean", "min", "q1", "median", "q3", "max", "count"]

class Rectifier:
    def __init__(self, services: List[str], pod_kpis: List[str], node_kpis: List[str]):
        """
        :param services: List of 20-30 core TrainTicket microservice names.
        :param pod_kpis: Telemetry metric names (cpu_usage, memory_bytes, rx_bytes, tx_bytes).
        :param node_kpis: Node level metric names (node_cpu, node_memory_pressure).
        """
        self.services = sorted(services)
        self.pod_kpis = sorted(pod_kpis)
        self.node_kpis = sorted(node_kpis)
        
        # Calculate fixed input width 'n' for downstream Autoencoder
        self.feature_names = self._build_feature_schema()
        self.input_width = len(self.feature_names)
        
        # Trailing Exponential Moving Average (EMA) buffer for zero-pod imputation
        self.ema_history: Dict[str, float] = {feat: 0.0 for feat in self.feature_names}
        self.ema_alpha = 0.2

    def _build_feature_schema(self) -> List[str]:
        features = []
        # Per (service, pod_kpi, statistic) features
        for s in self.services:
            for kpi in self.pod_kpis:
                for stat in STATS_LIST:
                    features.append(f"{s}.{kpi}.{stat}")
        # Per (node_kpi, statistic) features across node pool
        for kpi in self.node_kpis:
            for stat in STATS_LIST:
                features.append(f"node_pool.{kpi}.{stat}")
        return features

    def process_tick(self, raw_samples_df: pd.DataFrame) -> Tuple[np.ndarray, bool]:
        """
        Processes 1-minute raw pod metric samples into a fixed-length numpy array.
        
        :param raw_samples_df: DataFrame with columns ['pod_name', 'service_name', 'pod_phase', 'is_ready', 'kpi_name', 'value']
        :return: (fixed_vector_x_t, was_imputed_flag)
        """
        if raw_samples_df.empty:
            # Full telemetry gap: return EMA history
            return np.array([self.ema_history[f] for f in self.feature_names]), True

        # Convert to Polars for fast streaming group-by
        df = pl.from_pandas(raw_samples_df)
        
        # Edge Case Filter: Only Ready and Running pods (eliminates pending/terminating outliers)
        df_filtered = df.filter(
            (pl.col("pod_phase") == "Running") & (pl.col("is_ready") == True)
        )
        
        current_features: Dict[str, float] = {}
        has_zero_pod_service = False

        # Group by microservice & KPI
        for s in self.services:
            service_df = df_filtered.filter(pl.col("service_name") == s)
            pod_count = service_df.select(pl.col("pod_name")).n_unique()

            if pod_count == 0:
                has_zero_pod_service = True
                for kpi in self.pod_kpis:
                    current_features[f"{s}.{kpi}.count"] = 0.0
                    for stat in STATS_LIST:
                        if stat != "count":
                            fname = f"{s}.{kpi}.{stat}"
                            # Impute from trailing EMA
                            current_features[fname] = self.ema_history.get(fname, 0.0)
            else:
                for kpi in self.pod_kpis:
                    kpi_df = service_df.filter(pl.col("kpi_name") == kpi)
                    vals = kpi_df["value"].to_numpy()
                    
                    if len(vals) == 0:
                        vals = np.array([0.0])
                        
                    current_features[f"{s}.{kpi}.mean"] = float(np.mean(vals))
                    current_features[f"{s}.{kpi}.min"] = float(np.min(vals))
                    current_features[f"{s}.{kpi}.q1"] = float(np.percentile(vals, 25))
                    current_features[f"{s}.{kpi}.median"] = float(np.median(vals))
                    current_features[f"{s}.{kpi}.q3"] = float(np.percentile(vals, 75))
                    current_features[f"{s}.{kpi}.max"] = float(np.max(vals))
                    current_features[f"{s}.{kpi}.count"] = float(pod_count)

        # Process Node Pool KPIs
        for kpi in self.node_kpis:
            node_df = df_filtered.filter(pl.col("kpi_name") == kpi)
            vals = node_df["value"].to_numpy()
            if len(vals) == 0:
                vals = np.array([0.0])
            current_features[f"node_pool.{kpi}.mean"] = float(np.mean(vals))
            current_features[f"node_pool.{kpi}.min"] = float(np.min(vals))
            current_features[f"node_pool.{kpi}.q1"] = float(np.percentile(vals, 25))
            current_features[f"node_pool.{kpi}.median"] = float(np.median(vals))
            current_features[f"node_pool.{kpi}.q3"] = float(np.percentile(vals, 75))
            current_features[f"node_pool.{kpi}.max"] = float(np.max(vals))
            current_features[f"node_pool.{kpi}.count"] = float(len(vals))

        # Assemble fixed vector
        x_t = np.zeros(self.input_width, dtype=np.float32)
        for idx, feat in enumerate(self.feature_names):
            val = current_features.get(feat, self.ema_history.get(feat, 0.0))
            x_t[idx] = val
            # Update EMA history for smooth trailing imputation
            self.ema_history[feat] = (self.ema_alpha * val) + ((1.0 - self.ema_alpha) * self.ema_history[feat])

        return x_t, has_zero_pod_service
