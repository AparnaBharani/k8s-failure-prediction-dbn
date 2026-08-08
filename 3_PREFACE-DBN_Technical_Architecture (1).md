# PREFACE-DBN — Technical Architecture & Data Pipeline Specification

**Document 3 of 4:** System Design & Engineering Guide
**Project:** Intelligent Kubernetes Pod Failure Prediction using Dynamic Bayesian Networks

---

## 1. End-to-End Data Pipeline

```
┌─────────────────┐   ┌──────────────┐   ┌────────────┐   ┌─────────────┐   ┌───────────────┐   ┌──────────────────┐
│  Kubernetes     │──▶│  Metrics     │──▶│  RECTIFIER │──▶│ Autoencoder │──▶│ Dynamic       │──▶│ Action           │
│  Cluster        │   │  Scraper     │   │ Preprocess │   │ (anomaly)   │   │ Bayesian Net  │   │ Controller       │
│  (pods, nodes)  │   │ Prom / GCM   │   │            │   │             │   │ (risk+cause)  │   │ (webhook/API)    │
└─────────────────┘   └──────────────┘   └────────────┘   └─────────────┘   └───────────────┘   └──────────────────┘
     variable            variable            FIXED            FIXED             per-service          per-service
   pod metrics         KPI vector        stat vector      recon errors        risk scores          mitigation
```

**The single most important property of this pipeline:** cardinality shrinks and then stays fixed. Between the cluster and the RECTIFIER, the data is *variable-length* (it grows and shrinks with the pod count). From the RECTIFIER onward it is *fixed-length* and keyed on the stable set of logical microservices. Everything downstream — autoencoder, DBN, controller — reasons over a constant-size, service-indexed world, which is exactly what makes learning and inference tractable on an autoscaling cluster.

**Data contract between stages:**

| Boundary | Payload | Shape |
|---|---|---|
| Cluster → Scraper | Raw metric samples per pod/node | Variable |
| Scraper → RECTIFIER | Timestamped KPI values + pod→service label map | Variable |
| RECTIFIER → Autoencoder | Fixed statistical vector `x_t` | Fixed `n` |
| Autoencoder → DBN | Per-service aggregated anomaly signals | Fixed `|services|` |
| DBN → Controller | Per-service `P(Normal/Degrading/Critical)` + root-cause rank | `|services| × 3` |

Everything runs on a 1-minute tick. The scraper triggers the pipeline; each stage is a pure function of the current tick plus (for the DBN) the previous tick's belief state.

## 2. Module 1 — The RECTIFIER

**Job:** turn a variable number of pods per microservice into a fixed-size numeric fingerprint per microservice.

**Input.** At each tick, a batch of raw metric samples, one row per (pod, KPI): CPU usage, memory working-set bytes, network RX/TX bytes, disk, process/system counters — whatever the monitoring stack exposes. Plus a live map telling us which microservice each pod currently belongs to (read from the Kubernetes label `app.kubernetes.io/name` or equivalent, kept fresh by a pod informer).

**Core logic.** For each microservice `S` and each KPI `κ`:
1. Gather the values of `κ` across all *ready, running* pods currently serving `S`.
2. Reduce that set to a fixed vector of descriptive statistics: **mean, min, Q1, median, Q3, max, and count**.
3. Emit those seven numbers in fixed vector positions.

Concretely: if `userapi` runs on 4 pods this minute, `memory.userapi.mean` is the average working-set across those 4 pods, `memory.userapi.max` is the worst one, and `memory.userapi.count` is 4. Next minute the autoscaler drops it to 2 pods — the *values* change but the *vector length and positions do not*. Node-level KPIs are aggregated the same way across the node pool; platform-level KPIs pass through directly.

**Output shape.**
```
n = (pod-level KPIs) × (7 statistics) × (number of microservices)
  + (node-level KPIs) × (7 statistics)
  + (platform-level KPIs)
```
This number is a constant for a given application. That constant is the autoencoder's input width.

**Why `count` is retained deliberately.** It's the only channel that tells downstream models how heavily a service is currently autoscaled. Aggressive, sustained scale-out is itself a weak leading indicator of resource-exhaustion failures, so we keep it as a feature rather than discarding it.

**Engineering notes.**
- Implement as a streaming group-by (Polars or pandas) keyed on the label map. Polars is preferred for throughput at high pod counts.
- Filter aggressively *before* aggregating: only pods with phase `Running` **and** container `ready==true`. This single filter eliminates the pending/terminating-pod outliers that would otherwise poison the statistics.
- For a service momentarily at zero pods, keep the vector length fixed by emitting `count=0` and imputing the stats from a trailing moving average, and flag the tick so the DBN can widen that service's uncertainty.
- Persist the fitted normalization (per-position mean/std from healthy data) so the autoencoder always sees standardized inputs.

## 3. Module 2 — The Autoencoder

**Job:** learn what "healthy" looks like and measure how far the current moment deviates from it.

**What an autoencoder is, operationally.** A neural network shaped like an hourglass: it compresses the input down to a small "bottleneck" and then tries to reconstruct the original from that compressed form. Trained only on healthy data, it gets very good at reconstructing healthy patterns and correspondingly *bad* at reconstructing patterns it never saw. The reconstruction error — how far the output is from the input — is the anomaly signal.

**Input / output schema.**
- **Input:** the fixed rectified vector `x_t` of width `n` (standardized).
- **Architecture:** encoder `n → n/2 → n/4`, bottleneck `n/8`, decoder `n/4 → n/2 → n`. ReLU on hidden layers, linear output.
- **Output:** a reconstructed vector `x̂_t` of the same width `n`.

**Training data requirements.** Metrics from a sustained window of **normal, non-failing** operation (the reference implementation used ~2 weeks at 1-minute granularity). No failure labels are needed — this stage is fully unsupervised, which is a deliberate robustness choice: it works even where labeled failure data is impossible to get. Training is offline and cheap (minutes), has zero runtime cost, and should be scheduled to repeat periodically so the baseline tracks the application as it evolves.

**How the signal is passed downstream.** For each rKPI position we compute the squared reconstruction error. We then aggregate those errors up to the *service* level (average the errors of the KPIs belonging to that service) and standardize against the errors seen during healthy training. The result is one anomaly number per microservice per tick.

## 4. Module 3 — The Dynamic Bayesian Network & Decision Network Extension

**Job:** convert per-service anomaly signals into a calibrated, time-aware failure probability, a root-cause diagnosis, and expected utility evaluations over candidate actions.

**Node structure.** For each microservice we model a **hidden health state** that can be `Normal`, `Degrading`, or `Critical`. We can't observe this state directly — we infer it. What we *can* observe is the autoencoder's anomaly signal for that service, which attaches to the health state as an **observation node**. So per service, per tick: one hidden health node, one observed anomaly node.

**Tracking change over time.** The "Dynamic" in DBN means the network is stitched across time: a service's health this minute depends on its health last minute. This is encoded as a small transition table with realistic dynamics — a healthy service almost always stays healthy, degradation tends to persist and can either recover or worsen, and Critical is "sticky" (services rarely bounce straight back from critical). This is what lets the system say "this has been quietly degrading for eight minutes and is now trending critical," which a memoryless threshold fundamentally cannot express. It also damps false alarms: a one-tick spike doesn't move the health state much; a sustained trend does.

**Using topology to pinpoint the culprit.** This is the root-cause mechanism and the biggest upgrade over the baseline's heuristic ranking. We wire the service *call graph* into the network: if the real dependency chain is `API Gateway → Service A → Database`, then Service A's health node is connected to the Gateway's, and the Database's to Service A's. The modelled behaviour is that an unhealthy upstream service raises the odds its downstream neighbours turn unhealthy too — i.e. failure propagates *along the call graph*. When a whole cluster of services lights up, the network's most probable explanation is the one where a single **upstream** service failed first and its neighbours' problems are *explained away* by that parent, rather than each service failing independently. That upstream-most service is the reported root cause.

- The graph is built automatically from Kubernetes service discovery and/or service-mesh (Istio) traffic telemetry, and rebuilt on a schedule so it survives topology changes.
- Mutually-calling services (cycles) are collapsed into a single node so the diagnostic graph stays acyclic and tractable.

**Dynamic Decision Network (DDN) Extension.** We convert the pure DBN into a **Dynamic Decision Network (DDN)** by appending:
1. **Decision Nodes ($A_t \in \mathcal{A}$):** Candidate actions at tick $t$: $\{ \text{Do Nothing } (A_0), \text{Pre-emptive Scale-Out } (A_{scale}), \text{Pod Restart } (A_{restart}), \text{Pod Reschedule } (A_{resched}), \text{Traffic Shift } (A_{shift}) \}$.
2. **Utility Nodes ($U(H_t^s, A_t)$):** Utility function quantifying the payoff of taking action $A_t$ when service $s$ is in state $H_t^s$, considering action disruption cost $C_{action}(A)$ vs. unmitigated failure cost $C_{outage}$.

**How risk and utility are computed each tick (inference).** The system runs **forward filtering** to update health state belief $P(H_t^s \mid O_{1:t})$ and then computes Expected Utility for each candidate action $A \in \mathcal{A}$:
$$EU(A \mid O_{1:t}) = \sum_{k \in \{N, D, C\}} P(H_t^s = k \mid O_{1:t}) \cdot U(H_t^s = k, A)$$
For complex or densely-connected graphs (20 to 30 core microservices), a **custom vectorized particle filter (NumPy/JAX)** evaluates belief states and expected utilities within the 5-second per-tick budget.

**Learning the tables.** The transition, observation, and utility tables start from sensible hand-set values and are then refined from data. Failure-state labels for training come cheaply from the Chaos Mesh injection schedule — the ticks inside a known injected-fault window are labelled degrading/critical, healthy windows are labelled normal — so the model is calibrated against ground truth without needing hand-labeling.

## 5. Module 4 — Kubernetes Action Controller & Intervention Utility Test

**Job:** turn expected utility calculations into safe, automatic, mathematically optimal preventative action.

**Shape.** A Kubernetes **Operator**: a control loop plus a custom resource (`FailurePredictor` CRD) that holds configuration (risk threshold τ, utility parameters, cooldowns, per-action policy) and surfaces live status (current risk per service, current root-cause ranking, expected utilities $EU(A)$, last actions taken).

**Decision logic & Maximum Expected Utility (MEU).** Each tick the controller reads the DDN's per-service belief states and expected utilities. It executes Maximum Expected Utility optimization ($A^* = \operatorname{arg\,max}_{A} EU(A \mid O_{1:t})$).

**The Intervention Utility Test (Reschedule vs. Restart):**
When a microservice enters a `Degrading` or `Critical` state, the controller performs an explicit mathematical **Intervention Utility Test** comparing pod rescheduling versus restarting:
$$\Delta EU_{\text{intervene}} = EU(A_{\text{reschedule}}) - EU(A_{\text{restart}})$$

| Candidate Action | Operational Cost $C_{\text{action}}$ | Success Probability $P_{\text{success}}$ | Ideal Use Case Context |
|---|---|---|---|
| **Pod Restart ($A_{\text{restart}}$)** | Low (Fast $<5$s, zero scheduling delay) | High for isolated app memory leaks; **Low** if node host is degraded | Software memory leak, transient container deadlock, application state corruption |
| **Pod Reschedule ($A_{\text{reschedule}}$)** | Higher (Image pull, K8s queueing, capacity shift) | **High** ($\approx 1.0$) for clearing node-level host faults | Bad node hardware, noisy neighbor host contention, node kernel/disk I/O degradation |

If node-level metric anomalies indicate host-level contamination (e.g., node disk/memory pressure), $EU(A_{\text{reschedule}})$ dominates $EU(A_{\text{restart}})$, and the controller automatically evicts and reschedules the pod to a healthy node. Otherwise, it chooses $A_{\text{restart}}$ to minimize action overhead.

**Talking to Kubernetes.** The controller calls the Kubernetes API (scale subresource, deployment rollout, pod eviction, node cordon/drain) and the service-mesh API (traffic shifting) using a **least-privilege service account** — only the specific verbs on the specific workloads it manages, never cluster-admin.

**Safety rails (non-negotiable).**
- **Shadow/dry-run mode**, on by default: log the intended action and its expected utility calculation, take none. This is how a platform owner validates the system before granting write access.
- **Cooldowns and rate limits** on every action so the controller can't oscillate or fight the autoscaler.
- **Leader election** so exactly one controller instance ever acts.
- **Belief-state checkpointing** each tick so a controller restart resumes mid-incident instead of going blind.
- Full **audit log** of every action (or would-be action, in shadow mode).

**Implementation options.** Go with **kubebuilder / controller-runtime** for a production-grade operator, or **Python + Kopf** for faster iteration on the research build (acceptable at benchmark scale). Both consume the same DDN risk and utility stream.

## 6. Deployment Topology

All components run in-cluster as their own Deployment: the scraper sidecars/collectors feed a pipeline pod (RECTIFIER + autoencoder + DBN) that publishes risk to the controller pod. Models are stored as versioned artifacts (MLflow or an object store) and loaded at startup. The pipeline and controller are stateless except for the DBN belief state, which is checkpointed to a durable store. Nothing in the hot path holds per-pod state, which is why footprint stays flat as the fleet scales.
