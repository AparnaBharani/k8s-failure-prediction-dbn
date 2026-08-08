# PREFACE-DBN — Phase-by-Phase Implementation & Roadmap

**Document 4 of 4:** Chronological Engineering Execution Plan
**Project:** Intelligent Kubernetes Pod Failure Prediction using Dynamic Bayesian Networks

---

## How to read this roadmap

Five sequential phases, each broken into concrete sprints with deliverables, a tech stack, and an exit check. Phases 1–2 build the data plane, Phase 3 is the intellectual core, Phase 4 closes the automation loop, Phase 5 produces the results. A phase is "done" only when its exit check passes — later phases assume the earlier plumbing is solid.

---

## Phase 1 — Testbed & Data Ingestion Setup

**Objective:** a real autoscaling cluster running a real microservice app, generating realistic traffic, with the ability to inject controlled faults and capture clean metric streams.

**Sprint 1.1 — Cluster & demo app.**
- Provision a local Kubernetes cluster on a 16GB RAM machine (Minikube or Kind with per-container memory caps `128MB–256MB`) or managed cluster (GKE).
- Deploy the **TrainTicket Core Suite** (20 to 30 core microservices: User, Train, Station, Order, Payment, Route, Travel, Price, Ticket-Office) via Helm.
- Turn on **Horizontal Pod Autoscaler (HPA)** for the core services, so pod and KPI counts genuinely vary over time. Confirm single-replica DB pods.

**Sprint 1.2 — Monitoring stack.**
- Install **Prometheus** + `kube-state-metrics` + `node-exporter` for pod/node metrics.
- Install **Istio** (minimal profile) to get the service-to-service call graph telemetry needed in Phase 3.
- Stand up Grafana for eyeballing.

**Sprint 1.3 — Traffic generation.**
- Build a **Locust** workload generator that mimics realistic, time-varying user load (diurnal/weekday patterns), so autoscaling actually kicks in and steady-state looks like production.

**Sprint 1.4 — Fault injection.**
- Install **Chaos Mesh** and script the three fault classes: **CPU stress**, **memory stress** (leak-like), and **network delay**, targeted at core services (`ts-train-service`, `ts-user-service`, `ts-order-service`, `ts-station-service`).
- Standardize the experiment shape: 30 minutes of stable healthy run → inject fault → continue until a disruptive failure is observable.

**Sprint 1.5 — Data capture.**
- Write the collector that snapshots all KPIs at a 1-minute tick into time-series files, tagged healthy vs. injected-fault with the exact injection timestamps (these tags become the training labels in Phase 3).
- Capture healthy baseline data for the autoencoder, plus a library of fault-injection runs.

**Tech stack:** Minikube/Kind (local 16GB) or GKE, Helm, Prometheus, kube-state-metrics, node-exporter, Istio, Grafana, Locust, Chaos Mesh.

**Exit check:** you can reproduce the baseline's headline observation — the number of collected KPIs visibly rises and falls over time as pods scale — and every fault-injection run reliably reaches an observable disruptive failure (measured by response-time percentile and HTTP error-rate shift).

---

## Phase 2 — RECTIFIER & Autoencoder Development

**Objective:** the fixed-vector feature pipeline — variable pod metrics in, standardized anomaly signal out.

**Sprint 2.1 — RECTIFIER preprocessor.**
- Write the Python service that: reads the live pod→microservice label map from the Kubernetes API; filters to ready/running pods; groups by microservice; and reduces each KPI to the seven statistics (mean, min, Q1, median, Q3, max, count).
- Implement the edge-case handling: exclude pending/terminating pods, impute zero-pod services from a trailing average, and keep the output vector length invariant.
- Unit-test against synthetic pod-scaling traces (simulate scale-out/scale-in and assert the output length never changes).

**Sprint 2.2 — Normalization & data prep.**
- Fit per-position standardization on the healthy dataset; persist it.
- Assemble the training matrix from the two weeks of healthy rectified vectors.

**Sprint 2.3 — Autoencoder build & train.**
- Implement the `n → n/2 → n/4 → n/8 → n/4 → n/2 → n` autoencoder in **PyTorch** (TensorFlow is an acceptable alternative).
- Train on healthy data with MSE loss and early stopping; version the artifact with **MLflow**.

**Sprint 2.4 — Anomaly signal export.**
- Compute per-KPI reconstruction error and aggregate to a per-service, healthy-standardized anomaly signal.
- Schedule periodic retraining as a Kubernetes CronJob so the baseline tracks drift.

**Tech stack:** Python, Polars (or pandas), PyTorch, MLflow, Kubernetes CronJob.

**Exit check:** the RECTIFIER's output vector length is provably invariant across a full autoscaling cycle; the autoencoder reconstructs held-out healthy data with low, stable error; and on the Phase-1 fault runs, the injected service's anomaly signal measurably rises *before* the disruptive-failure timestamp (proof that signal precedes failure).

---

## Phase 3 — DBN Integration & Root-Cause Localizer

**Objective:** the temporal, topology-aware reasoning core.

**Sprint 3.1 — Service graph builder.**
- Extract the service call graph from Istio telemetry (and/or EndpointSlices) into a directed graph with **NetworkX**.
- Collapse mutually-calling clusters (cycles) into single nodes so the graph is acyclic; schedule periodic rebuilds.

**Sprint 3.2 — DDN model & decision node definition.**
- Define the two-time-slice model in **pgmpy**: per-service hidden health node (Normal/Degrading/Critical), per-service observation node fed by autoencoder signal, inter-slice health transitions, and intra-slice topology edges.
- Define **Decision Nodes** ($A_t \in \{\text{Do Nothing}, \text{Scale-Out}, \text{Restart}, \text{Reschedule}, \text{Traffic Shift}\}$) and **Utility Nodes** ($U(H_t, A_t)$) modeling operational action costs vs unmitigated outage costs.

**Sprint 3.3 — Learning the tables & utility parameters.**
- Fit the observation model and refine transition tables using Chaos-Mesh-labelled sequences from Phase 1.
- Calibrate the action cost parameters $C_{\text{action}}(A)$ and success likelihoods $P_{\text{success}}(A \mid H_t)$.

**Sprint 3.4 — Inference & utility engine.**
- Implement forward filtering to produce per-service $P(\text{Critical})$ and expected utility $EU(A \mid O_{1:t})$ each tick.
- Prototype exact inference in pgmpy; then implement a **custom vectorized particle filter (NumPy/JAX)** to compute Maximum Expected Utility (MEU) within the 5-second per-tick budget.

**Sprint 3.5 — Root-cause localizer & Intervention Utility Test.**
- Implement the topology-aware query: among services above the risk threshold, return the upstream-most likely culprit.
- Implement the **Intervention Utility Test** comparing $\Delta EU_{\text{intervene}} = EU(A_{\text{reschedule}}) - EU(A_{\text{restart}})$ based on node health signals and action costs.

**Sprint 3.6 — Wire it together.**
- Connect autoencoder output → DDN observation & decision nodes; run the full ingest→rectify→autoencode→infer→MEU chain on recorded fault runs.

**Tech stack:** pgmpy (prototyping), NumPy/JAX (production filter & utility computation), NetworkX.

> **Tooling note:** don't reach for `gudhi` here — it's a topological *data-analysis* (persistent-homology) library, not a probabilistic-graphical-model tool, and it's the wrong instrument for this job. Build the DDN on pgmpy + a custom filter.

**Exit check:** on held-out fault runs, the filtered $P(\text{Critical})$ crosses the threshold *earlier* than the baseline binary alarm; MEU correctly selects Reschedule over Restart during node host faults; the root-cause localizer names the actual injected service; and risk scores are calibrated.

---

## Phase 4 — Proactive Kubernetes Operator Setup

**Objective:** close the loop — risk score & expected utility to safe automatic action.

**Sprint 4.1 — Controller scaffold.**
- Build the operator with a `FailurePredictor` CRD (config: threshold τ, utility weightings, debounce `k`, cooldowns, per-action policy; status: live risk, root cause, expected utilities $EU(A)$, recent actions).
- Choose **Go + kubebuilder** for production or **Python + Kopf** for the research build.

**Sprint 4.2 — Utility-driven decision & mitigation logic.**
- Implement MEU decision logic ($A^* = \operatorname{arg\,max} EU(A)$) incorporating the **Intervention Utility Test** ($\Delta EU = EU(A_{\text{reschedule}}) - EU(A_{\text{restart}})$): execute pod rescheduling when node host degradation dominates, otherwise execute targeted container restart or scale-out.
- Give the controller a least-privilege RBAC service account.

**Sprint 4.3 — Safety rails.**
- Implement **shadow/dry-run mode** (default on), cooldowns, rate limits, **leader election**, and **belief-state checkpointing** so a controller restart resumes mid-incident.
- Emit a full audit log of executed and would-be actions.

**Sprint 4.4 — Observability.**
- Publish risk scores, root-cause rankings, and actions as Prometheus metrics and in the CRD status; add a Grafana panel.

**Tech stack:** Go + kubebuilder/controller-runtime (or Python + Kopf), Kubernetes API, Istio API, Prometheus, Grafana.

**Exit check:** in a live fault run with mitigation **enabled**, the controller acts inside the earliness window and the injected fault either never reaches the disruptive-failure point or reaches it materially later than an un-mitigated control run — with zero incorrect destructive actions in the preceding shadow validation.

---

## Phase 5 — Benchmarking, Testing & Final Polish

**Objective:** produce the evidence — product dashboard or paper results.

**Sprint 5.1 — Full experiment matrix.**
- Run the factorial: {CPU, memory, network-delay} × {single-service, two-simultaneous-services} across the chosen services, mirroring the baseline's protocol.

**Sprint 5.2 — Metrics computation.**
- Compute **reaction interval**, **earliness interval** (absolute and as % position in the error window), and **strong/weak/overall localization rate**, using the baseline's exact definitions so numbers are directly comparable.
- Add the DBN-only metrics the baseline can't produce: **risk calibration** and **lead-time distribution** at a fixed threshold.

**Sprint 5.3 — Head-to-head vs. baseline.**
- Run the original PREFACE reasoning (threshold + Z-score localizer) on the *same* rectified/autoencoder features, and compare against PREFACE-DBN. Same features, swapped reasoner — this isolates the DBN's contribution.
- Focus reporting on the network-delay class, where the baseline is weakest and we expect the clearest win.

**Sprint 5.4 — Deliverable polish.**
- *Product track:* finalize the Grafana dashboard, the Helm install, docs, and the shadow→live enablement guide.
- *Research track:* generate the comparison figures (per-run timeline strips, earliness/localization bar charts, calibration plots) and write up the results.

**Tech stack:** the full stack above, plus matplotlib/plotly for figures and the existing MLflow for run tracking.

**Exit check (success criteria):** PREFACE-DBN matches or beats the baseline on CPU/memory localization, shows a clear improvement on network-delay, predicts with positive lead time in the large majority of runs, and exposes a single interpretable risk threshold that behaves consistently across failure types.

---

## Dependencies & critical path

```
Phase 1 (testbed + data) ──▶ Phase 2 (rectifier + autoencoder) ──▶ Phase 3 (DBN + localizer) ──▶ Phase 4 (operator) ──▶ Phase 5 (benchmark)
        │                                                                    ▲
        └──────────────── fault-injection labels feed CPT learning ─────────┘
```

The critical dependency to watch: Phase 3's table-learning needs the labelled fault runs from Phase 1, so make sure Phase 1's data capture tags injection windows precisely. Everything else is a clean linear chain.

## Team & effort shape (indicative)

| Phase | Primary skill | Relative effort |
|---|---|---|
| 1 | Platform / DevOps engineering | Medium |
| 2 | Data + ML engineering | Medium |
| 3 | ML / probabilistic modelling | **Heaviest** |
| 4 | Kubernetes / backend engineering | Medium |
| 5 | Evaluation + analysis | Medium |

Phase 3 is where the novel value concentrates and where to allocate the strongest modelling effort; Phases 1, 2, and 4 are well-trodden engineering with mature tooling.
