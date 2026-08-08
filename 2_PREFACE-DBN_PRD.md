# PREFACE-DBN — Product Requirements Document & Implementation Roadmap

### Intelligent Kubernetes Pod Failure Prediction and Root-Cause Localization using Hybrid Neuro-Probabilistic Reasoning

**Document class:** Engineering PRD + Phased Implementation Roadmap
**Baseline reference:** Denaro et al., *Predicting Failures of Autoscaling Distributed Applications* (Proc. ACM Softw. Eng., Vol. 1, FSE, Article 87, 2024)
**Status:** Design-complete, implementation-ready
**Audience:** Cloud-native platform engineers, ML systems engineers, reliability researchers

---

## 1. Executive Summary & Problem Statement

### 1.1 The operational gap

Kubernetes ships with reactive self-healing: it restarts crashed containers, reschedules pods when nodes die, and kills pods that fail liveness/readiness probes. Every one of these actions is **post-hoc** — it fires *after* a disruptive failure has already surfaced to users, and it targets the *symptom* (the dead pod) rather than the *root cause* (the microservice whose degradation propagated into that pod). The platform is, in the words of the source paper, "blind on which components are truly going to cause system failures to occur and when."

The interval between the moment a fault begins corrupting execution state and the moment users perceive a disruptive failure — the **error interval** — is the window in which proactive mitigation is possible. PREFACE demonstrated that this window is large in practice (earliness intervals of 13–102 minutes on real workloads). The gap PREFACE-DBN closes is that PREFACE, having *detected* an anomaly, still makes a **binary, memoryless** decision at each timestamp: reconstruction error above `m_e + 3·s_e` → alarm, else silence. It has no model of *how failure states evolve over time*, no calibrated probability of imminent disruption, and its localizer is a heuristic Z-score ranking that ignores the service call graph.

### 1.2 The autoscaling bottleneck (inherited problem) and why the hybrid solves it

Horizontal autoscaling makes the number of pods — and therefore the number of KPIs collected per timestamp — vary dramatically. In the paper's own measurements, TrainTicket's collected-KPI count ranged from 3,444 to 3,636 (steady) and could theoretically reach ~59,512 under maximum scale-out; Alemira ranged 892–1,628. A neural network has a **fixed** input width and cannot ingest a variable-length vector. This is the core reason classical predictors fail on Kubernetes.

PREFACE-DBN preserves the two components that already solve this — the **RECTIFIER** (variable-size KPI sets → fixed-size descriptive statistics per microservice) and the **Deep Autoencoder** (fixed vector → per-KPI reconstruction errors) — and replaces only the decision logic downstream:

```
Kubernetes Cluster
   → KPI Monitoring (Prometheus / GCM / Locust)
      → RECTIFIER  (variable KPI sets → fixed descriptive-statistic vector)
         → Deep Autoencoder  (reconstruction error per rKPI + global MSE)
            → Dynamic Bayesian Network  (temporal risk inference P(H_t | H_{t-1}, H_pa))
               → Proactive Self-Healing / Probabilistic Root-Cause Localization
```

The division of labour is the whole point: the **RECTIFIER + autoencoder** handle *dimensionality* (dynamic → fixed) and *feature extraction* (raw KPI → anomaly signal); the **DBN** handles *temporal stochastic reasoning* (`P(H_t^s | H_{t-1}^s)`) and *topological causality* (which service's degradation explains the others). The autoencoder answers "does this instant look abnormal?"; the DBN answers "given the last hour of evidence and the service graph, what is the probability each microservice is on a trajectory toward disruptive failure, and which one started it?"

### 1.3 Dual-track objective

**Track A — Industry-deployable tool.** A proactive Kubernetes operator (custom controller + CRD) that consumes DBN risk scores and triggers graded mitigation (pre-emptive scale-out, targeted restart, traffic shift, node cordon) when `P(H_t^s = Critical) > τ`, *before* disruptive failure. This is a genuine capability Kubernetes does not have.

**Track B — Research/publication benchmark.** A controlled evaluation replicating the paper's Chaos Mesh experimental protocol on TrainTicket (and, where licensable, a commercial analogue), reporting **reaction interval**, **earliness interval**, and **localization rate** for PREFACE-DBN vs. the original PREFACE baseline (autoencoder + `m_e + 3s_e` threshold + Z-score localizer). The publishable hypothesis: *temporal probabilistic filtering improves earliness and localization precision — especially for the low-signal Network-delay failure class where the paper's overall localization rate collapses to 41–88% — while adding calibrated, thresholdable risk that the binary detector cannot provide.*

---

## 2. System Architecture & Data Pipeline

### 2.1 End-to-end data flow (component contract)

| Stage | Input | Output | Cardinality behaviour |
|---|---|---|---|
| Monitoring | Cluster state | Raw KPI stream, 1-minute granularity | **Variable** (grows/shrinks with pod count) |
| RECTIFIER | Raw KPIs + pod→service label map | Fixed rKPI vector `x_t ∈ ℝ^n` | **Fixed** `n = |KPI| × |stats| × |Services|` |
| Autoencoder | `x_t` | Per-rKPI recon-error `e_t ∈ ℝ^n`, global MSE `E_t` | **Fixed** `n` |
| Service-aggregation | `e_t` + rKPI→service map | Per-service anomaly signal `a_t^s`, `s ∈ S` | **Fixed** `|S|` |
| DBN | `a_t^{1..|S|}` (discretized/continuous) | `P(H_t^s = k)` for `k ∈ {N,D,C}`, per service | `|S| × 3` |
| Controller | Risk marginals + service graph | Mitigation action + root-cause service | 1 decision/timestamp |

The critical architectural insight: **rectification collapses pod-level cardinality, but the DBN operates at the microservice level** — a *fixed* set. The service graph (18 services for Alemira, ~40 for TrainTicket) is topologically stable even as pod counts swing from 42 to 1,260. This is what makes a fixed-structure DBN tractable on an autoscaling substrate.

### 2.2 RECTIFIER Layer — exact schema

**Grouping key.** Every pod carries the Kubernetes label identifying its microservice (`app.kubernetes.io/name`, or the workload's `app`/`service` label). The RECTIFIER groups the raw sample at timestamp `t` by this label. Node-level KPIs group by node; platform-level KPIs pass through un-aggregated.

**Per-(service, KPI) reduction.** For microservice `s` running on pods `P_s(t) = {p_1, …, p_{m}}` at timestamp `t`, and for each monitored pod-level KPI `κ` (e.g. `cpu_usage`, `memory_working_set_bytes`, `network_rx_bytes`, `network_tx_bytes`), collect the homologous values `{κ(p_1), …, κ(p_m)}` and compute the fixed statistic set:

```
stats(κ, s, t) = { mean, min, Q1, median (Q2), Q3, max, count }
```

`count = |P_s(t)|` is retained deliberately — it is the RECTIFIER's *only* channel that tells the autoencoder how heavily autoscaled the service currently is, which is itself a weak failure signal (runaway scale-out precedes many resource-exhaustion failures).

**Output dimensionality.**

```
n = |KPI_pod| × |stats| × |Services|
  + |KPI_node| × |stats| × 1      (node KPIs → stats across the node pool)
  + |KPI_platform|                (platform KPIs, passthrough)
```

For TrainTicket-scale numbers from Table 2 of the paper (≈171 pod/node/platform metric slots), with 7 statistics and ~40 logical services, `n` lands in the low thousands — well within autoencoder input budgets and, crucially, **constant at every timestamp regardless of pod count**.

**Worked example (from the paper's own instance).** If `userapi` is replicated across 4 pods, then `used.bytes.userapi.mean = (used.bytes.p1 + used.bytes.p2 + used.bytes.p3 + used.bytes.p4) / 4`, and `used.bytes.userapi.count = 4`. Next minute the autoscaler drops it to 2 pods; the *value* of the mean changes but the *vector position* and the *vector length* do not.

**Edge cases — pending / terminating / zero-pod services (concrete handling).**
- **Pending / `ContainerCreating` pods** produce no metric sample yet. Exclude from the aggregation set (do not treat as zeros — a not-yet-started pod is not a zero-CPU pod). The paper explicitly flags these as the source of outlier KPI counts.
- **Terminating (`Terminating`/grace-period) pods** may still emit metrics that are physically meaningless. Filter on pod phase `Running` **and** container `ready==true` before aggregation.
- **Zero-pod service** (service scaled to 0, or briefly all-pending): emit `count=0` and impute the descriptive statistics with the trailing exponential moving average of that service's stats over the last `w` non-empty timestamps. This preserves vector length and avoids injecting a discontinuity the autoencoder would misread as an anomaly. The imputation is logged so the DBN emission for that service can be *widened* (higher observation variance) during imputed intervals.

Implementation: a streaming pandas/Polars group-by keyed on the label map, refreshed from the Kubernetes API `EndpointSlice`/pod-informer so the pod→service mapping is always current (a pod's service assignment can change across its lifetime, per the paper's challenge (ii)).

### 2.3 Deep Autoencoder — configuration

The autoencoder is retained **structurally identical to PREFACE** so that Track-B comparisons are clean (the only changed variable is the downstream reasoner).

- **Input / output width:** `n` (the rectified vector).
- **Encoder:** layers of size `n → n/2 → n/4`.
- **Latent space:** `n/8` nodes.
- **Decoder:** mirror, `n/4 → n/2 → n`.
- **Activations:** ReLU on hidden layers, linear output (regression on standardized rKPIs).
- **Standardization:** per-rKPI z-normalization using training-set mean/std; fitted **only** on normal-execution data.
- **Training data:** rKPI observations at 1-minute cadence over ≥2 weeks of *non-failing* execution (matches the paper's protocol; training took 10–20 min offline and has zero runtime cost).
- **Loss:** mean-squared reconstruction error.

**Reconstruction-error outputs consumed by the DBN.** Unlike PREFACE, we do **not** threshold here. We export two products per timestamp:

1. **Per-rKPI reconstruction error** `e_t[i] = (x_t[i] − x̂_t[i])²`.
2. **Global MSE** `E_t = mean_i e_t[i]`.

We then aggregate per-rKPI errors up to the **service** level. For service `s` with rKPI index set `I_s`:

```
a_t^s = standardized aggregate anomaly signal for service s
      = z_train( Σ_{i ∈ I_s} e_t[i] / |I_s| )
```

where `z_train` normalizes against the mean `m_e^s` and std `s_e^s` of that service's aggregated error observed during training. Note `a_t^s` is exactly the quantity PREFACE would have thresholded at `m_e^s + 3 s_e^s` — but here it becomes a **continuous observation feeding the DBN**, so a `2.5σ` blip that PREFACE discards is retained as soft evidence that accumulates over time.

### 2.4 Dynamic Bayesian Network — design

This is the novel core. The DBN is a **2-time-slice temporal Bayesian network (2-TBN)** unrolled over the monitoring horizon.

#### 2.4.1 Hidden nodes `H_t^s` — health state

One discrete hidden variable per microservice `s`, with three ordered states:

```
H_t^s ∈ { Normal (N), Degrading (D), Critical (C) }
```

- **Normal** — behaviour consistent with training distribution.
- **Degrading** — persistent low-grade anomaly; error state present but not yet user-visible. *This is the state PREFACE cannot name.*
- **Critical** — high probability of imminent disruptive failure; mitigation trigger candidate.

Three states (rather than binary) is the design choice that gives the operator a *graded* response and gives the model a natural "the fault is propagating" intermediate — the paper's whole premise is that errors worsen incrementally before disruption.

#### 2.4.2 Observation nodes `O_t^s`

Each `H_t^s` emits an observation node `O_t^s` driven by the autoencoder's per-service anomaly signal `a_t^s` from §2.3. Two supported parameterizations:

- **Gaussian emission (recommended):** `O_t^s = a_t^s` continuous, `P(a_t^s | H_t^s = k) = 𝒩(μ_k, σ_k²)` with `μ_N < μ_D < μ_C`. Keeps the autoencoder's signal resolution; avoids binning artefacts. Fitted by EM on labelled-by-injection data or by weak supervision (see §4 Phase 3).
- **Discretized emission (fallback for exact tabular inference):** bin `a_t^s` into `{low, mid, high}` by training quantiles; `P(O_t^s | H_t^s)` is a 3×3 CPT.

Optionally augment the observation with the raw `count` statistic (autoscaling velocity) as a second child of `H_t^s`, since aggressive scale-out is itself weakly predictive of resource-class failures.

#### 2.4.3 Transition model (inter-slice) — the temporal core

The transition CPT models how health evolves. Baseline single-service transition `P(H_t^s | H_{t-1}^s)` is a 3×3 stochastic matrix encoding realistic dynamics:

```
                H_t = N     D      C
H_{t-1} = N  [  0.95    0.045  0.005 ]   ← mostly stays healthy; rare onset
H_{t-1} = D  [  0.20    0.65   0.15  ]   ← degradation persists, can recover or worsen
H_{t-1} = C  [  0.02    0.18   0.80  ]   ← critical is sticky (absorbing-ish)
```

The near-absorbing Critical row and the low `N→C` direct-jump probability encode the paper's empirical observation that failures develop *gradually* — you almost always pass through Degrading. These priors are then refined by learning (§4 Phase 3); the matrix above is the informative initialization, not a placeholder.

#### 2.4.4 Topological dependency mapping — root-cause structure

The DBN's intra-slice edges encode the **service call graph** so that root-cause localization is *structural*, not heuristic. For TrainTicket the graph is explicit in the paper's Figure 1 (`Gateway → Advanced-travel → route-plan → …`, `UserAPI → verify-code/authorization`, services → their databases).

We add **causal edges** `H_t^{pa(s)} → H_t^s`: a downstream service's health depends on its upstream dependencies' health *within the same slice*. Concretely, if `Gateway → UserAPI → Database`:

```
P(H_t^{UserAPI} | H_{t-1}^{UserAPI}, H_t^{Gateway})
P(H_t^{Database} | H_{t-1}^{Database}, H_t^{UserAPI})
```

This makes the transition CPT of each service **conditioned on its parents' current state**. The modelled semantics: a Critical upstream service *raises the transition probability* of its downstream neighbours into Degrading/Critical — i.e. failure propagates *along the call graph*. Root-cause inference then falls out naturally: when a wave of Critical states appears, the DBN's posterior favours the explanation in which the **upstream-most** service went Critical first and its neighbours' Criticality is *explained away* by that parent, rather than each being independently faulty. This is the principled replacement for PREFACE's Z-score ranking, and it is exactly where we expect to beat the baseline on the "proxy-microservice" cases the paper had to define around.

**Building the graph automatically.** Derive edges from (a) Kubernetes service discovery / `EndpointSlice` + Istio traffic-management telemetry (the paper runs Istio on TrainTicket) or (b) observed request-flow from distributed traces. Store as a directed graph `G=(S, E)`; enforce acyclicity for the intra-slice DAG by collapsing strongly-connected components (mutually-calling services) into a single super-node, then reason at the SCC-condensed level.

#### 2.4.5 Inference

- **Forward filtering** each timestamp: compute `P(H_t^s | O_{1:t})` for all `s`. This is the online risk score.
- **Exact inference** when `|S|` is modest and CPTs are tabular: unroll the 2-TBN and run junction-tree / variable elimination per slice, carrying the forward message (belief state) between slices via the **interface algorithm** (only the persistent nodes `H_t` form the inter-slice interface).
- **Approximate inference** at scale (`|S| > ~30–50` with dense topology): **particle filtering** (Sequential Monte Carlo) over the joint health vector, or the **Boyen–Koller factored frontier** approximation that keeps the belief state factored per service and bounds error. Particle filtering also cleanly handles the Gaussian emission without discretization.
- **Root-cause query:** MAP explanation `argmax P(H_t^{1:|S|} | O_{1:t})` restricted to services with `P(H_t^s = C) > τ`, then select the topological ancestor among them.

#### 2.4.6 Dynamic Decision Network (DDN) Extension & Intervention Utility Test

To upgrade decision-making beyond static thresholding, the network is extended into a **Dynamic Decision Network (DDN)** by adding:
1. **Decision Nodes ($A_t \in \mathcal{A}$):** The candidate set of interventions:
   $$\mathcal{A} = \{ \text{Do Nothing } (A_0), \text{Pre-emptive Scale-Out } (A_{scale}), \text{Pod Restart } (A_{restart}), \text{Pod Reschedule } (A_{resched}), \text{Traffic Shift } (A_{shift}) \}$$
2. **Utility Nodes ($U(H_t^s, A_t)$):** Utility function quantifying the payoff of taking action $A_t$ when service $s$ is in health state $H_t^s$:
   $$U(H_t^s, A_t) = - C_{\text{action}}(A_t) - C_{\text{outage}} \cdot \mathbb{I}(H_t^s = C) \cdot (1 - P_{\text{success}}(A_t \mid H_t^s))$$
3. **Maximum Expected Utility (MEU):** The controller evaluates:
   $$EU(A \mid O_{1:t}) = \sum_{k \in \{N, D, C\}} P(H_t^s = k \mid O_{1:t}) \cdot U(H_t^s = k, A)$$
   $$A^* = \operatorname{arg\,max}_{A \in \mathcal{A}} EU(A \mid O_{1:t})$$

**The Intervention Utility Test (Reschedule vs. Restart):**
When a microservice degrades, the controller specifically performs an **Intervention Utility Test** comparing **Pod Rescheduling** ($A_{resched}$, moving the pod to a different healthy node) versus **Pod Restarting** ($A_{restart}$, in-place container restart):

$$\Delta EU_{\text{intervene}} = EU(A_{resched}) - EU(A_{restart})$$

- **Pod Restart ($A_{restart}$):** Fast execution ($<5$s), zero scheduling latency, low operational cost $C_{\text{action}}$. Ideal for software memory leaks or transient container deadlocks. However, if the underlying node host suffers hardware degradation or kernel disk/memory pressure, $P_{\text{success}}(A_{restart}) \approx 0$.
- **Pod Reschedule ($A_{resched}$):** Higher action cost $C_{\text{action}}$ (container image pull, scheduler queueing delay), but guarantees high success rate ($P_{\text{success}} \approx 1.0$) for clearing node-local host contamination, noisy-neighbor resource starvation, or hardware faults.

The controller automatically executes $A_{resched}$ when node anomaly telemetry indicates local host contamination, otherwise selecting $A_{restart}$ to minimize action overhead.

---

## 3. Functional & Non-Functional Requirements

### 3.1 Functional requirements

| ID | Requirement |
|---|---|
| **F1** | Scrape pod/node/platform KPIs at 1-minute granularity from Prometheus and/or GCM, and workload metrics from Locust, without gaps during autoscaling events. |
| **F2** | Maintain a live pod→microservice label map from the Kubernetes API (pod informer + EndpointSlice watch); reflect re-assignments within one scrape interval. |
| **F3** | RECTIFIER: reduce the variable KPI set to the fixed rKPI vector `x_t` every timestamp, applying the pending/terminating/zero-pod handling of §2.2. |
| **F4** | Autoencoder training pipeline: offline batch training on ≥2 weeks of normal-execution rKPIs; scheduled periodic retraining to absorb drift; versioned model artifacts. |
| **F5** | Autoencoder online inference: emit per-rKPI reconstruction error and global MSE, then service-level aggregate `a_t^s`, every timestamp. |
| **F6** | DDN online filtering & utility calculation: update `P(H_t^s | O_{1:t})` and compute expected utility $EU(A_t)$ for all actions each timestamp via exact or particle inference. |
| **F7** | Root-cause localization: return a ranked list of candidate failing microservices using the topological MAP query of §2.4.5. |
| **F8** | Proactive utility-driven mitigation: a Kubernetes controller executing Maximum Expected Utility ($A^* = \operatorname{arg\,max} EU(A)$) and running the **Intervention Utility Test** ($\Delta EU = EU(A_{resched}) - EU(A_{restart})$) to automate scale-out, restart, reschedule, or traffic shift decisions with shadow mode and audit logging. |
| **F9** | Expose all risk marginals, expected utility calculations, and localization results as Prometheus metrics + a CRD status subresource for observability. |

### 3.2 Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| **N1** | End-to-end inference latency (scrape-complete → risk update) | **< 5 s per timestamp** (negligible against 60 s cadence; matches paper's "few seconds"). |
| **N2** | DBN memory footprint | Belief state `O(|S|·3)` for factored/particle inference; junction-tree cliques bounded by SCC-condensed treewidth. Target < 500 MB for `|S| ≤ 50`. |
| **N3** | Scalability | Correct operation across **20 to 30 core microservices** (TrainTicket Core Suite) and dynamic pod scaling, with no per-pod state in the DBN (pod cardinality is fully absorbed by the RECTIFIER). |
| **N4** | Monitoring impact | **Zero-downtime**; scraping and inference run out-of-band; controller actions rate-limited and guarded by cooldowns to prevent thrashing the autoscaler. |
| **N5** | Availability of the predictor itself | Controller runs with leader-election (HA), survives its own pod rescheduling without losing the DBN belief state (persist belief to a checkpoint store each slice). |
| **N6** | Calibration | DBN risk scores must be probability-calibrated (Brier score / reliability diagram reported), so `τ` is an interpretable probability, not an arbitrary sigma multiple. |

---

## 4. End-to-End Phased Implementation Roadmap

### Phase 1 — Testbed Setup & Data Pipeline

**Goal:** a fully instrumented autoscaling cluster producing labelled normal + fault-injected KPI streams.

**Deliverables**
- TrainTicket Core Suite (20 to 30 core microservices) deployed on a local 16GB Kubernetes cluster (Minikube / Kind with memory caps `128MB–256MB` per pod) or managed cloud cluster (GKE) with **HPA configured for horizontal pod autoscaling** and single-replica DB containers.
- Prometheus (in-cluster scraping) for metrics; **Istio** (minimal profile) for the service graph telemetry.
- **Locust** workload generator reproducing realistic diurnal traffic (weighted task profile over core booking APIs).
- **Chaos Mesh** configured for the three fault classes: `StressChaos` (CPU), `StressChaos` (memory), `NetworkChaos` (latency/delay), targeting core services (`ts-train-service`, `ts-user-service`, `ts-order-service`, `ts-station-service`).
- A normal-execution KPI dataset for autoencoder training + fault-injection runs (30 min stable → inject → run to disruption).

**Stack:** Minikube/Kind (local 16GB) or GKE, Helm, Prometheus + kube-state-metrics + node-exporter, Istio, Locust, Chaos Mesh.

**Verification:** confirm collected-KPI count varies over time (reproduce the paper's RQ1 — Alemira 892–1,628 / TrainTicket 3,444–3,636 band); confirm each Chaos experiment reaches an observable disruptive failure (95th-percentile response time + HTTP failure-rate shift, Mann-Whitney U significant, Vargha-Delaney Â₁₂ effect size — the paper's exact disruption criterion).

### Phase 2 — RECTIFIER & Autoencoder Module

**Goal:** the fixed-vector feature pipeline.

**Deliverables**
- RECTIFIER as a streaming service: label-keyed group-by (Polars/pandas), the seven-statistic reduction, and the §2.2 edge-case handling. Unit-tested against synthetic pod-scaling traces.
- Standardization fitted on normal data; persisted normalizer.
- Autoencoder (`n → n/2 → n/4 → n/8 → n/4 → n/2 → n`) in **PyTorch**; training harness, early stopping, artifact versioning (MLflow).
- Service-level error aggregator producing `a_t^s`.

**Stack:** Python, Polars, PyTorch, MLflow, Kubernetes CronJob for retraining.

**Verification:** RECTIFIER output length is invariant across a full scale-out/scale-in cycle; autoencoder reconstruction error on held-out normal data is low and stable; on Phase-1 fault windows, `a_t^s` rises measurably for the injected service **before** the disruption timestamp (sanity that signal precedes failure).

### Phase 3 — Dynamic Bayesian Network Core

**Goal:** the temporal probabilistic reasoner.

**Deliverables**
- Service graph builder from Istio/EndpointSlice → directed `G`, SCC-condensed to a DAG.
- **2-TBN specification** in **pgmpy** (`DynamicBayesianNetwork`) for prototyping: hidden `H_t^s`, observation `O_t^s`, intra-slice topological edges, inter-slice transitions (§2.4).
- **CPT learning:**
  - *Emission* `P(O_t^s | H_t^s)`: fit Gaussian `(μ_k, σ_k)` per state. Labels come from the fault-injection ground truth — timestamps in a service's error interval labelled Degrading→Critical by proximity to disruption, normal windows labelled Normal — i.e. **weak supervision from the Chaos Mesh schedule**.
  - *Transition* `P(H_t^s | H_{t-1}^s, H_t^{pa(s)})`: initialize with the informed matrix of §2.4.3, refine by EM / maximum-likelihood on the labelled sequences.
- **Inference engine:** forward filtering with exact junction-tree for prototypes; a **custom vectorized particle filter (NumPy/JAX)** for the production/scale path (`|S|>30`), since pgmpy's DBN inference does not scale to per-second budgets at 50+ nodes.
- Root-cause MAP query with topological tie-breaking.

**Stack:** pgmpy (prototype), NumPy/JAX (production filter), NetworkX (graph).

> **Note on tooling.** The brief mentions `gudhi` — that library is for *topological data analysis* (persistent homology), which is a different tool for a different job than probabilistic graphical inference. If topology-aware *features* are later desired (e.g. persistence of anomaly clusters across the service graph), gudhi could feed additional observation channels, but the DBN itself should be built on pgmpy + a custom filter, not gudhi.

**Verification:** on held-out injection runs, the filtered `P(H_t^{injected} = C)` crosses a candidate `τ` earlier than PREFACE's binary alarm; the topological MAP correctly identifies the injected service as root cause (not merely a proxy) at a higher rate than Z-score ranking; calibration (reliability diagram) is acceptable.

### Phase 4 — Proactive Kubernetes Integration

**Goal:** close the loop from risk score to mitigation.

**Deliverables**
- A **Kubernetes Operator** exposing a `FailurePredictor` CRD (config: `τ`, cooldowns, per-action policy) with status carrying live risk marginals + current root-cause ranking.
- Controller reconcile loop: consume DBN marginals → when `P(H_t^s = C) > τ` sustained over `k` consecutive slices (debounce), execute the graded mitigation ladder:
  1. **Degrading, rising:** pre-emptive HPA scale-out of the suspect service.
  2. **Critical, upstream root cause:** targeted `rollout restart` of the root-cause deployment + Istio traffic shift / circuit-break away from it.
  3. **Node-correlated Critical:** cordon + drain the implicated node ahead of failure.
- **Dry-run / shadow mode** (log intended action, take none) for safe evaluation and for Track-B experiments that must *not* perturb the disruption timeline.
- Leader-election HA + belief-state checkpointing (N5).

**Stack:** Go + kubebuilder/controller-runtime (production operator) **or** Python + Kopf (faster to iterate, acceptable for the research build); Istio API for traffic control.

**Verification:** in a live Chaos run with mitigation *enabled*, the controller acts within the earliness window and the injected fault **does not reach the disruption criterion** (or reaches it materially later) versus an un-mitigated control run — the core industry-value proof.

### Phase 5 — Evaluation & Benchmarking

**Goal:** the publishable comparison and the tool's evidence base.

**Deliverables & metrics** (replicating the paper's definitions exactly so numbers are comparable):
- **Reaction interval:** timestamps from fault injection to first correct (strong or weak) localization.
- **Earliness interval:** timestamps from first correct localization to disruptive failure (both absolute minutes and % position in the error interval).
- **Strong / Weak / Overall localization rate** (weak = failing service ranked 2nd/3rd or a proxy ranked 1st).
- **False-alarm rate** (false-prediction before injection; false-localization after).
- **DBN-specific additions:** risk **calibration** (Brier score, reliability diagram) and **lead-time distribution** at fixed `τ` — quantities PREFACE structurally cannot report.
- Run the **full factorial**: {CPU, Memory, Network} × {single, paired-simultaneous} × selected services, mirroring the paper's Tables 4–5.
- **Head-to-head vs. PREFACE baseline:** same autoencoder, same RECTIFIER, swap only the reasoner — PREFACE's `m_e+3s_e` threshold + Z-score localizer vs. PREFACE-DBN. This isolates the DBN's contribution (the analogue of the paper's own RQ4 ablation).
- Comparative figures (per-experiment timeline strips like the paper's Figure 5; earliness/localization bar charts; calibration plots).

**Verification / success criteria for publication:** PREFACE-DBN shows (i) equal-or-better overall localization rate on CPU/Memory (paper baseline 72–99%) and a **meaningful lift on Network-delay** (paper baseline 41–88%, the weak spot), (ii) equal-or-earlier reaction interval, and (iii) calibrated risk enabling a single interpretable `τ` across failure types.

---

## 5. Risk Analysis & Mitigation

| # | Risk / bottleneck | Why it bites | Concrete mitigation |
|---|---|---|---|
| **R1** | **DBN inference cost scales exponentially with joint state.** Exact inference over `3^{|S|}` joint health configs is intractable past ~20 densely-coupled services. | Junction-tree clique size blows up with graph treewidth; a dense TrainTicket graph could exceed the < 5 s budget. | Reason on the **SCC-condensed DAG** (fewer effective nodes); use the **interface/BK factored-frontier** approximation or a **particle filter** whose cost is `O(#particles × |S|)`, linear in services. Cap treewidth by pruning weak call-graph edges below a traffic threshold. |
| **R2** | **Pending / dying pods corrupt rectification.** Metrics from not-ready or terminating pods produce outliers (the paper's documented outlier source). | A single pending pod can skew `max`/`mean`, injecting a false anomaly the DBN then propagates. | Filter on pod phase `Running` **and** `ready==true` before aggregation (§2.2); impute zero-pod services from trailing EMA and **widen the DBN emission variance** during imputed windows so the reasoner down-weights uncertain evidence. |
| **R3** | **CPT learning starved of failure labels.** Degrading/Critical states need labelled degradation sequences, which are rare in production. | Emission/transition CPTs mis-fit if only normal data is available (the same labeled-data scarcity that motivates unsupervised PREFACE). | Weak-supervise from **Chaos Mesh injection schedules** (known error intervals → state labels); keep the autoencoder itself fully **unsupervised** so the pipeline degrades gracefully to PREFACE behaviour if DBN labels are unavailable; periodically re-estimate CPTs as real incidents accrue. |
| **R4** | **Service graph is dynamic / cyclic.** Autoscaling and mesh routing change edges; mutual calls create cycles that break the intra-slice DAG requirement. | An incorrect or cyclic graph mislocates root cause. | Rebuild `G` from live Istio/trace telemetry on a schedule; **condense SCCs** to super-nodes to guarantee acyclicity; treat graph edges as priors, not hard constraints, so mis-specification degrades ranking gracefully rather than failing inference. |
| **R5** | **Mitigation thrashing / feedback with the autoscaler.** The controller's scale-out could fight Kubernetes HPA, or a false Critical could trigger an unnecessary drain. | Oscillation harms availability — the opposite of the goal. | **Debounce** (require `k` sustained Critical slices), per-action **cooldowns**, **rate limits**, and a **shadow/dry-run** default. Prefer least-disruptive actions first (scale-out, traffic shift) before destructive ones (restart, drain). Leader-elected single actor prevents duplicate actions. |
| **R6** | **Autoencoder / KPI drift.** Application behaviour drifts; reconstruction error baseline shifts, moving `a_t^s`. | Emission distributions `𝒩(μ_k,σ_k)` become stale → miscalibrated risk. | Scheduled **offline retraining** (paper notes this is free at runtime) with model versioning; monitor the normal-window `a_t^s` distribution and **recalibrate emissions** when it drifts; the DBN's own reliability diagram (N6) is the drift alarm. |
| **R7** | **Belief-state loss on controller restart.** The predictor pod can itself be rescheduled, losing the forward-filter state. | A cold-started filter re-enters with a flat prior and briefly loses temporal context. | **Checkpoint the belief state** each slice to a durable store; on restart, resume from the last checkpoint. Because inference is a forward filter, a few slices of re-warm-up fully recover the posterior. |

---

### Appendix A — Symbol reference

| Symbol | Meaning |
|---|---|
| `x_t ∈ ℝ^n` | Rectified KPI (rKPI) vector at timestamp `t` |
| `n` | Fixed rKPI dimension = `|KPI|×|stats|×|Services|` (+ node/platform terms) |
| `e_t[i]` | Per-rKPI reconstruction error; `E_t` global MSE |
| `a_t^s` | Service-level aggregated, train-standardized anomaly signal |
| `H_t^s` | Hidden health state of service `s` ∈ {Normal, Degrading, Critical} |
| `O_t^s` | Observation node for service `s` (driven by `a_t^s`) |
| `pa(s)` | Upstream parents of `s` in the service call graph |
| `τ` | Mitigation threshold on `P(H_t^s = Critical)` |
| `S`, `G=(S,E)` | Set of microservices; directed service dependency graph |

### Appendix B — What is inherited vs. novel

| Component | PREFACE (baseline) | PREFACE-DBN / PREFACE-DDN (this project) |
|---|---|---|
| RECTIFIER | ✅ inherited unchanged | ✅ inherited (+ explicit edge-case spec) |
| Deep Autoencoder | ✅ inherited unchanged | ✅ inherited (error exported, **not** thresholded) |
| Temporal Reasoning | Binary threshold `m_e + 3s_e` | **DBN temporal filtering `P(H_t \mid H_{t-1})`** |
| Decision Framework | Static rule ladder | **Dynamic Decision Network (DDN) Maximum Expected Utility (MEU)** |
| Action Selection | Fixed escalation | **Intervention Utility Test ($\Delta EU = EU(A_{\text{resched}}) - EU(A_{\text{restart}})$)** |
| Localization | Z-score ranking (heuristic) | **Topological MAP over service graph** |
| Output | Alarm / no-alarm + ranked service | **Calibrated risk + Expected Utilities $EU(A)$ + root cause** |
| Proactive action | Hands off to k8s self-healing | **Custom operator executes optimal utility intervention before disruption** |
