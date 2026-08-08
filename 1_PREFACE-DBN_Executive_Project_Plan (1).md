# PREFACE-DBN / PREFACE-DDN — Executive Project Plan

**Project:** Intelligent Kubernetes Pod Failure Prediction & Mitigation using Dynamic Decision Networks (DDN)
**Document 1 of 4:** Executive Project Plan & Strategic Vision
**Baseline reference:** Denaro et al., *Predicting Failures of Autoscaling Distributed Applications* (FSE 2024)

---

## 1. Executive Summary

Kubernetes runs most of the modern cloud, and its self-healing is fundamentally reactive: it restarts a container *after* it crashes, reschedules a pod *after* a node dies, and kills a pod *after* it fails a health check. By the time any of these fire, users have already felt the outage. There is almost always a warning period — minutes to over an hour — between the moment a service starts degrading and the moment it visibly fails. Today that warning period is wasted.

PREFACE-DDN turns that wasted window into an automated, mathematically optimized mitigation window. We take the proven data-preprocessing pipeline from the PREFACE research (a **RECTIFIER** that compresses the constantly-changing set of pod metrics into a fixed-size statistical fingerprint, and an **Autoencoder** that scores how abnormal each moment looks) and we replace its simple on/off anomaly alarm with a **Dynamic Decision Network (DDN)** (Decision Networks over time). The DDN does three things the original approach cannot:
1. **Temporal Belief Filtering:** It tracks how each microservice's health *changes over time* to produce a real, calibrated probability of imminent failure ($P(Normal \rightarrow Degrading \rightarrow Critical)$).
2. **Topological Root-Cause Localization:** It uses the service call graph to point at the **root-cause** microservice rather than just the symptomatic victim.
3. **Intervention Utility Optimization:** It evaluates explicit **Decision Nodes** ($A_t$) and **Utility Functions** ($U(H_t, A_t)$) to mathematically compare candidate actions. Specifically, it executes a real-time **Intervention Utility Test** comparing **Rescheduling** (evicting and moving a pod to another healthy node) versus **Restarting** (in-place container restart), balancing operational disruption costs against failure risks to choose the action that maximizes expected utility.

**Core value proposition:** move Kubernetes reliability from *react-after-crash* to *predict-and-prevent*, with a system that not only says "something is wrong" but "**this** service will likely fail, **this** is the root cause, and mathematically, **rescheduling / restarting** provides the optimal utility."

## 2. Problem Statement

### 2.1 Reactive self-healing is too late and aims at the wrong target

Kubernetes' healing mechanisms only activate once a container or node has already failed, and they target the failure itself regardless of what caused it. If a memory leak in an upstream service slowly starves a downstream API until the API's pod is OOM-killed, Kubernetes restarts the API pod — the victim — while the leaking service keeps running and the cycle repeats. There is no notion of "this is going to fail soon" and no notion of "the real culprit is over there."

### 2.2 Autoscaling breaks standard machine-learning predictors

The deeper technical blocker is that horizontal autoscaling constantly changes how many pods are running, and therefore how many metrics (KPIs) are collected each minute. In the PREFACE measurements, the number of collected metrics for a single application swung continuously across thousands of values as pods scaled from tens to over a thousand. A standard neural network has a **fixed** number of inputs and simply cannot ingest a metric vector whose length changes every minute. This is why classical, statically-configured failure predictors do not work on real autoscaling Kubernetes clusters — and why a preprocessing step that *stabilizes the dimensionality* is mandatory before any learning model can be applied.

## 3. Our Proposed Solution (PREFACE + DDN)

The system is a clean, four-stage upgrade of a proven pipeline:

**Stage 1 — RECTIFIER (dimensionality problem, solved).** For each microservice, take all the pods currently running it and collapse their raw metrics (CPU, memory, network RX/TX, etc.) into a fixed set of descriptive statistics — mean, min, max, quartiles, and a pod count. However many pods a service has this minute, it always produces the same number of numbers. The variable-length problem disappears, and the pod count itself becomes a useful signal (runaway scale-out often precedes resource failures).

**Stage 2 — Autoencoder (feature extraction, inherited).** A neural network trained only on normal, healthy operation learns what "normal" looks like. At runtime it measures how badly it fails to reconstruct the current metrics — a high reconstruction error means the current moment doesn't look like anything it saw in training. This gives a per-service anomaly signal.

**Stage 3 — Dynamic Bayesian Network (temporal & topological core).** Instead of thresholding the anomaly signal into a binary alarm, we feed it into a DBN that models each microservice as having a health state (Normal → Degrading → Critical) that evolves over time and is influenced by its upstream dependencies. The DBN continuously tracks the probability each service is heading toward failure and identifies the root cause.

**Stage 4 — Dynamic Decision Network & Intervention Utility Test (the decision upgrade).** We extend the DBN with **Decision Nodes** ($A_t$) and **Utility Functions** ($U(H_t, A_t)$). When a service degrades, the controller executes an **Intervention Utility Test** comparing candidate actions:
$$\Delta EU = EU(A_{\text{reschedule}}) - EU(A_{\text{restart}})$$
- **Pod Restart ($A_{\text{restart}}$):** Low execution latency and cost, but low utility if the underlying node host has hardware degradation, noisy neighbors, or kernel faults.
- **Pod Reschedule ($A_{\text{reschedule}}$):** Higher action/rescheduling cost (container pull, scheduler queueing), but high utility for clearing node-local host issues and bad placement topologies.
The system automatically executes the action $A^*$ that maximizes Expected Utility: $A^* = \operatorname{arg\,max}_A EU(A \mid O_{1:t})$.

Why this combination is clean: each piece does exactly one job. The RECTIFIER handles *shape* (variable → fixed), the autoencoder handles *signal* (raw metrics → anomaly score), the DBN handles *probabilistic reasoning* (anomaly score → calibrated risk + root cause), and the Decision Network handles *optimal action selection* (risk + utility matrices → mathematically optimal intervention).

## 4. Target Outcomes — Dual Pathway

### 4.1 Product Track — a Kubernetes Operator for enterprise DevOps

Packaged as a standard Kubernetes Operator: install it with Helm, point it at your Prometheus, and it runs as a control loop alongside your workloads. It publishes a live per-service failure-risk score, and when a service crosses a configurable risk threshold it takes graded, policy-driven action — pre-emptive scale-out, targeted rollout restart, traffic shift away from the suspect service, or node cordon — all with a dry-run/shadow mode, cooldowns, and a full audit trail. For an enterprise DevOps or SRE team, the pitch is: *fewer user-visible incidents, faster root-cause identification, and automated prevention that plugs into the tooling you already run.*

### 4.2 Research Track — a publication-ready benchmark

Packaged as a controlled experiment that reproduces the PREFACE evaluation protocol (Chaos Mesh fault injection on the TrainTicket microservice benchmark) and compares PREFACE-DBN head-to-head against the original PREFACE baseline. Because we keep the RECTIFIER and autoencoder identical and swap only the reasoning core, the comparison cleanly isolates the DBN's contribution. The headline metrics are **Reaction Time Interval** (how quickly we catch the failure after it's injected) and **Earliness Interval** (how far ahead of the actual crash we catch it), plus localization accuracy — with a specific hypothesis that the DBN improves the low-signal Network-delay failure class where the baseline's accuracy is weakest.

Both tracks share the same codebase; the difference is packaging and whether the controller acts (Product) or runs in shadow to preserve the measured failure timeline (Research).

## 5. Success Metrics

| Category | Metric | Target |
|---|---|---|
| **Prediction accuracy** | Overall localization rate (correct root-cause service identified during the error interval) | ≥ baseline on CPU/Memory failures; **measurable improvement on Network-delay** (baseline's weak class) |
| **Earliness** | Earliness interval (lead time from first correct prediction to disruptive failure) | Positive lead time in the large majority of runs; report full distribution, not just averages |
| **Responsiveness** | Reaction interval (time from fault onset to first correct prediction) | ≤ baseline reaction time |
| **False alarms** | False-prediction rate during healthy operation | Low single-digit percentage; ≤ baseline |
| **Risk quality** | Calibration of risk scores (reliability/Brier) | Risk threshold behaves as a real probability, consistent across failure types |
| **Performance** | End-to-end inference latency per monitoring tick | **< 5 seconds** (negligible against 60s scrape cadence) |
| **Overhead** | Additional CPU/Memory footprint of the predictor | Bounded and constant regardless of monitored pod count (no per-pod state in the reasoner) |
| **Scale** | Microservices supported | **20 to 30 core microservices** (TrainTicket Core Suite) scaling dynamically without degradation |
| **Product safety** | Mitigation-induced incidents (thrashing, bad evictions) | Zero in shadow validation before any live action is enabled |

## 6. Scope, Assumptions & Non-Goals

**In scope:** metric ingestion, rectification, autoencoder anomaly scoring, DBN risk + root-cause inference, and a Kubernetes action controller, validated on a demo microservice benchmark under injected faults.

**Assumptions:** a Prometheus-instrumented cluster with horizontal autoscaling enabled; a stable set of *logical* microservices even as pod counts vary; access to a fault-injection tool (Chaos Mesh) for training-label generation and evaluation.

**Non-goals (this phase):** predicting failures that leave no metric footprint at all; replacing Kubernetes' existing self-healing (we complement it); multi-cluster federation; and pinpointing the exact failing *pod instance* (we localize to the *service* level, consistent with the baseline).

## 7. High-Level Timeline

Five sequential phases (detailed in Document 4): **(1)** testbed + data ingestion, **(2)** RECTIFIER + autoencoder, **(3)** DBN + root-cause localizer, **(4)** Kubernetes action controller, **(5)** benchmarking and final polish. Phases 1–2 stand up the data plane, Phase 3 delivers the intellectual core, Phase 4 closes the automation loop, and Phase 5 produces either the product dashboard or the paper results depending on the chosen track.
