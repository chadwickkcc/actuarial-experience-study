# Designing a Product-Like AI-Powered Actuarial Experience Study Tool for Life Insurance: Best Practices and Design Patterns

## TL;DR

- **Architecture**: Build the platform on a medallion lakehouse (Bronze/Silver/Gold) with an ACORD-aligned canonical life-insurance schema, a thin tenant-specific connector/mapping layer, and a configurable rules-driven calculation engine that separates immutable seriatim exposure construction from pluggable assumption setting, AI augmentation, and aggregation. Treat "configuration over customization" as the central design principle so a single codebase serves multiple insurers.
- **AI integration**: Use AI in three controlled layers — (1) anomaly detection (isolation forests, autoencoders) to surface data quality issues a junior actuary would otherwise miss, (2) GLMs/GAMs/GBMs/survival models to suggest forward-looking assumptions with SHAP, partial-dependence, and uncertainty bands so senior actuaries can defend choices to regulators, and (3) a RAG/text-to-SQL chatbot tightly grounded in a curated semantic layer to answer natural-language questions and draft commentary. AI never sets assumptions; it proposes, explains, and audits.
- **Governance**: Wrap everything in ASOP 23/41/56-aligned governance — versioned data, versioned assumptions, peer review gates, immutable audit trail, role-based approval workflow (working actuary → reviewer → chief actuary), and audience-tiered reporting (detailed diagnostics → assumption recommendations → plain-English regulator/board narrative). Every AI suggestion must be reviewable, reproducible, and overrideable.

---

## Key Findings

1. **The medallion (Bronze/Silver/Gold) lakehouse pattern is the dominant blueprint** for multi-source insurance data platforms, and is directly transferable to actuarial experience studies. Bronze preserves immutable raw extracts from each policy admin, claims, and billing system; Silver conforms to a canonical life-insurance entity model (ideally ACORD-aligned); Gold contains study-ready seriatim exposure files and aggregated A/E cubes.
2. **The SOA's 2016 (revised 2023) "Experience Study Calculations" paper is the de facto standard reference** for the calculation engine — including direct/seriatim exposure, annual vs. distributed methods, Balducci vs. UDD assumptions, and the policy-year/calendar-year split errors documented in the 2017 supplement. The engine should make these methods first-class configuration choices, not hard-coded behaviors.
3. **Data quality is the single biggest blocker** in industry experience studies. LIMRA/SOA ("Experience Data Quality – How to Clean and Validate Your Data") catalogs the canonical checks: negative durations, claims after termination, duplicate policies, in-force reconciliation, age/duration plausibility, premium-vs-coverage consistency. ML anomaly detection (isolation forest, autoencoder, hybrid AE+IF) complements but does not replace these deterministic rules.
4. **Modern actuarial AI is moving from pure GLMs to a portfolio**: GLMs/GAMs for transparent base assumptions, GBMs (XGBoost, LightGBM, CatBoost) for predictive power, and survival/competing-risks models (Cox, RSF, gradient-boosted survival) for joint mortality/lapse modelling. SCOR's PLT GLM work and the SOA ILEC studies are concrete reference implementations.
5. **Regulators (NAIC Model Bulletin on AI, state DFS letters, ASOP 56) require explainability beyond a SHAP plot.** Carriers must reconcile machine-learning feature attributions back to filed rating factors and assumption rationales, with audit-grade decision logs.
6. **ASOP 56 (Modeling), ASOP 23 (Data Quality), and ASOP 41 (Communications)** define the governance scaffolding the software must enable: model intended-purpose statements, input/output validation, governance & controls, peer review, model logs, and qualified disclosures.
7. **Multi-tenancy in B2B SaaS** is best implemented as a single shared application with tenant-scoped configuration, RLS-enforced data isolation, and a hybrid option (dedicated DB or VPC for regulated tenants). "Placement is configuration; isolation is invariant" is the right north star.
8. **Text-to-SQL/RAG over actuarial data is feasible but high-risk** — production systems achieving 90%+ accuracy do so by combining schema-grounded prompts, few-shot example libraries, semantic-layer constraints, validation loops, and tabular-RAG hallucination scoring. Without these guardrails, accuracy on enterprise schemas is typically 50–70%.
9. **Assumption management has emerged as a discipline.** Industry surveys (Oliver Wyman, SOA) show 80%+ of insurers now have formal assumption governance frameworks, with a clear movement toward centralized assumption repositories with versioning, lineage, and approval workflow — exactly what the proposed tool should embed.
10. **Reporting must be audience-tiered**: working-actuary diagnostics (A/E tables by every dimension, residual plots, credibility detail), chief-actuary executive summary (key findings, recommended assumptions, sensitivities), and board/regulator plain-English narrative. Tools like Power BI/Tableau dominate today, but an integrated, parameter-driven report generator inside the platform reduces leakage and re-keying.

---

## Details

### 1. Data ingestion architecture for a multi-insurer platform

A product-like platform must absorb heterogeneous policy administration (PAS), claims, and billing extracts from systems ranging from mainframe COBOL files to modern Oracle Insurance Policy Administration, Sapien, FINEOS, and bespoke spreadsheets. The recommended pattern is a **medallion lakehouse**:

- **Bronze (raw, append-only)** — Each tenant's source extracts land verbatim with metadata (`_source_system`, `_load_ts`, `_file_hash`, `_tenant_id`). Schema-on-read, all columns stored as STRING/VARIANT to absorb upstream changes without breaking the pipe (Databricks/Microsoft Fabric guidance). Bronze provides reprocessability, lineage, and the cold archive needed for ASOP 23 data-quality reproducibility.
- **Silver (conformed canonical model)** — Map every tenant's data to a single canonical life-insurance schema. ACORD's Reference Architecture (Information Model + L&A object hierarchy) is the natural starting point: `Policy`, `Coverage`, `Holding`, `Life`, `LifeParticipant`, `Claim`, `Transaction`. Required canonical fields for life experience studies include: policy number, plan code, issue date, gender, smoker status, underwriting class, issue age, face amount (initial and current), premium mode, premium amount, status, status reason code, status date, last paid-to date, reinstatement events, conversion events, rider list, and reinsurance flags. Silver also stores claim records keyed to policy, with cause-of-decrement, claim date, paid date, and amount.
- **Gold (study-ready)** — Seriatim exposure files split by study year, age, duration, and decrement; aggregated A/E cubes; assumption tables; results datasets keyed to a study run-id and assumption-version-id.

**Configurable connectors** should be expressed as declarative YAML/JSON mapping artifacts per tenant (source-table → canonical-field, with type casts, code-list translations, and validation predicates). Tools like dbt, Spark/Delta Live Tables, or Microsoft Fabric pipelines provide the runtime; the differentiator is the prebuilt mapping templates for common PAS systems (Oracle OIPA, Sapiens, LifeCAD, AS/400-era systems) plus a self-service mapping wizard for niche sources. Code-list normalization (e.g., termination reasons "DTH"/"01"/"DEATH" → canonical `DEATH_BENEFIT_CLAIM`) is the single highest-leverage configurable artifact and should be tenant-versioned.

ACORD XTbML standards should be the lingua franca for reference tables (mortality tables, lapse tables); SOA's MORT application already publishes 2,500+ tables in this format, easing onboarding.

### 2. Automated ETL for seriatim exposure construction

Building a seriatim exposure file is the engineering core of an experience study and is where most insurer studies fail or take months. The pattern that scales:

- **Policy timeline reconstruction.** For each policy, ingest the full event stream (issue, premium payments, anniversary, paid-to-date changes, reinstatement, conversion, partial surrender, face changes, rider adds/drops, lapse, death, surrender, maturity, expiry, recapture). Materialize as a SCD-Type-2 timeline (`policy_id`, `version_start_ts`, `version_end_ts`, all attributes). Reinstatements collapse a prior lapse into a contiguous in-force segment with a flag; coverage changes generate a new segment.
- **Exposure splitting.** Apply the SOA Experience Study Calculations framework: choose direct/seriatim exposure, then policy-year vs. calendar-year vs. age-year splits. Each policy contributes one or more exposure segments per study cell. The engine must support the **annual exposure method** (full-year exposure for deaths in the rate year, Balducci hypothesis) and the **distributed/exact exposure method** (UDD), with the choice configurable per study and per decrement (typical: actuarial/annual for mortality, exact for lapse).
- **Decrement handling.** Mortality: full credit on death within the rate year. Lapse: only the elapsed fraction. Conversions, expiries, maturities: not the decrement under study, so contribute partial exposure but no event. Partial surrenders: continue exposure, but track NAR/face-amount changes for amount-weighted exposures.
- **Splits.** When studying by attained age in a calendar-year framework, each policy-year crosses two age-years; the SOA's 2017 "Experience Study Rate Errors" supplement quantifies the systematic bias and recommends the distributed approach. The engine should produce both views and surface the rate-error magnitude as a diagnostic.

A robust pipeline uses Spark/Delta with idempotent task IDs per study run, deterministic partitioning by issue cohort, and produces three Gold artifacts: (a) the exposure fact table (millions of segment rows), (b) a study-run manifest (input data hash, code version, parameters), and (c) reconciliation deltas showing in-force counts at study start, exposures, decrements, and in-force at study end — the classic actuarial "in-force reconciliation" that is the strongest test for missing or duplicated records.

### 3. AI-powered data quality

The LIMRA/SOA "Experience Data Quality" report and Actuarial Ninja's experience-analysis guide together define the must-have **deterministic checks**:

- Issue date ≤ status date ≤ study end; death date ≥ issue date; paid-to date plausible.
- Issue age within product limits; attained age = issue age + duration.
- Face amount > 0 for in-force; non-negative durations; no negative exposure.
- Claims with no matching policy; claims after a recorded termination; duplicate policy IDs across systems.
- Premium amount consistent with face amount × rate per thousand within a tolerance band by plan.
- In-force reconciliation: `Beginning IF + New Issues − Decrements = Ending IF`, both by count and by face amount, by plan and by tenant.
- Reference-table coverage: every (sex, smoker, issue age, duration) combination present in the company data must have a matching expected-rate cell in the chosen reference table (VBT, company table).

Implement these as **declarative tests in dbt and/or Great Expectations**. dbt's native `unique`, `not_null`, `accepted_values`, and `relationships` tests cover the basics; the dbt-expectations and Great Expectations packages provide distributional checks (row counts within ±X% of prior period, value ranges, null rates). Each test gets a severity (`warn`/`error`) so trivial issues quarantine to a side table, while reconciliation breaks halt the run.

**ML anomaly detection** complements these rules, surfacing patterns that no rule catches:

- **Isolation Forest** on policy-level feature vectors (face amount, issue age, premium frequency, plan, state, etc.) flags multivariate outliers — often data-entry errors, untagged test policies, or unusual but real cohorts.
- **Autoencoders** trained on clean prior-period data flag records with high reconstruction error — useful for detecting drift after a PAS upgrade.
- **Hybrid AE+IF** approaches (well-documented in IoT and healthcare literature; transferable to insurance) achieve 0.98+ accuracy on benchmarked anomaly tasks and provide a richer signal than either alone.

The data quality interface should give the junior actuary a triage dashboard: overall data quality score, breakdown by check category (completeness, consistency, validity, anomaly), the worst N records with explanations, a one-click "quarantine and re-run" action, and an audit log capturing every override with a free-text justification (an ASOP 23 documentation requirement). The senior actuary should see a one-page DQ summary attached to every study output.

### 4. Experience analysis calculation engine design

The engine is the heart of the product and must be **configuration-driven, not code-driven**. A clean architecture:

- **Inputs**: Gold seriatim exposure file + an `assumption_set` artifact (the chosen reference table, e.g., 2015 VBT, plus mortality improvement scale, plus any company table overlays) + a `study_config` (decrement, time basis, exposure method, splits, credibility approach, segmentation dimensions).
- **Core calculations**: actual events; expected events = exposure × expected rate from the reference table; A/E ratio by count and by amount; standard error and confidence intervals; credibility factor.
- **Credibility**: support both Limited Fluctuation (the actuarial "full credibility" standard, e.g., 1,082 expected claims for 5%/90% in mortality) and Bühlmann/Bühlmann-Straub empirical Bayes credibility. The American Academy of Actuaries' Credibility Practice Note and the SOA Credibility Theory Practices report provide both formulas and worked examples (UL lapses, term mortality). Bayesian credibility (Beta-Binomial, Gamma-Poisson) is a natural extension and slots into the same interface.
- **Reference tables**: support VBT (2015, 2017 CSO, future updates), GAM-94/GAM-2014 for annuities, SOA LTC tables, group life tables, and tenant-specific company tables. Every table is loaded as a typed, dimensioned artifact (issue age × duration × sex × smoker × underwriting class) using the ACORD XTbML format SOA already publishes. A "table service" abstracts lookup so the calculation engine is agnostic.
- **Method configurability**: exposure method (annual/distributed/exact/central), Balducci vs. UDD, calendar-year vs. policy-year vs. age-year, count-weighted vs. amount-weighted, gross vs. net of reinsurance. These are not branches in code — they are parameters consumed by a single set of vectorized SQL/Spark/Polars routines.

The engine should run as an idempotent batch job on Delta tables with each run producing an immutable result set tagged with `(study_id, assumption_set_version, code_version, data_snapshot_hash, parameters_hash)` — the basis of ASOP 56-compliant reproducibility.

### 5. Handling actuarial intricacies as configuration

Modern term and UL portfolios have non-trivial features that cannot be hard-coded if the tool is to serve multiple insurers:

- **Post-Level-Term shock lapses and selection effects.** SCOR's PLT studies (2021) show that shock-lapse rates at the end of level term depend on attained age, premium-jump ratio, face band, and product (term-to-ART vs. graded). Mortality deterioration in PLT durations follows a non-linear function of shock lapse and post-level duration. The tool should provide a configurable PLT module with: (a) automatic identification of the level-period boundary from product configuration, (b) shock-lapse measurement with the SOA-style premium-jump groupings, (c) optional GLM/logistic regression model for projecting shock lapse on new business, (d) anti-selection mortality multiplier as an explicit, configurable function rather than a baked-in adjustment.
- **Cohort and selection effects.** Selection wears off over duration; this is captured naturally in select-and-ultimate tables (the 25-year select period in the 2015 VBT), but the tool must let analysts study early-duration A/E separately and detect when underwriting changes have invalidated prior selection assumptions.
- **Reinsurance.** Configure each treaty with type (YRT, coinsurance, mod-co, combination), retention, share, allowance schedule, and recapture provisions. The engine should compute experience on a gross-of-reinsurance, ceded, and net-of-reinsurance basis. NAIC's recent guidance on combination YRT/coinsurance contracts (interdependent risk transfer) means treaty interactions matter and must be modelled explicitly.
- **Riders.** Each rider (waiver of premium, ADB, term riders, LTC riders) gets its own decrement study; the tool should let users configure whether a rider's experience is studied separately or merged with the base policy, and whether rider terminations end the base coverage.
- **Partial surrenders, face changes, premium holidays.** Each generates a new segment; amount-weighted exposures must use the time-weighted average face within the segment, not point-in-time face.
- **Universal life premium persistency** (LIMRA's first-of-kind UL premium persistency study) is a different decrement than policy lapse; the tool needs first-class support for "premium-persistency" as a configurable decrement orthogonal to coverage termination.

The architectural pattern is a **plugin/strategy registry**: each intricacy is a named strategy with a stable interface; the study config selects which strategies apply. This is the same approach SCOR's GLM PLT models, and the FIS Prophet/MG-ALFA component libraries, use successfully.

### 6. Results aggregation and interactive analysis

The output of the calculation engine is a high-dimensional fact table: rows keyed by (study, assumption set, segment dimensions), with measures actual count, actual amount, exposure count, exposure amount, expected count, expected amount, A/E by count, A/E by amount, standard error, credibility weight, credibility-weighted A/E, complement-of-credibility source.

The aggregation layer should be a **semantic OLAP cube** over this fact table with the canonical actuarial dimensions: issue age band, attained age band, duration, gender, smoker status, underwriting class, plan, issue year cohort, study year, face amount band, distribution channel, state/region, premium mode. Slice/dice/drill operations let users move from "all business" to "Term, super-preferred non-smoker, attained 50–59, duration 11–15" in seconds. Actuarial-specific dimensions like premium-jump-ratio band (for PLT) and policy-year-in-PLT are configurable.

The interactive UI should expose:

- A pivot interface (rows, columns, filters, measures) with one-click switches between count A/E and amount A/E, between gross and net of reinsurance, and between alternative reference tables.
- Heat-map visualizations of A/E across age × duration with credibility-weighted overlays — junior actuaries navigate visually, seniors see the credibility transparency.
- Drill-through to the underlying seriatim records (with PII masking governed by RBAC).
- Confidence intervals shown as bands on every chart; never expose a point estimate without its uncertainty.
- Saved views, parameterized "studies" that re-run automatically each quarter, and shareable URLs that encode the slice for review meetings.

This is achievable with Tableau/Power BI on a Gold-layer star schema, but a product-grade solution embeds a custom React/Vega-Lite UI directly so the experience is consistent across tenants and the audit trail captures every drill action. PwC's analysis of actuarial transformation finds that fragmentation of "pre-model" and "post-model" tools across 4–7 separate platforms is the single biggest contributor to actuaries spending up to 50% of time on manual work — an integrated aggregation layer directly addresses this.

### 7. AI-assisted assumption setting

The right approach is a **portfolio of models** with clear roles:

- **GLMs and GAMs** (logistic for shock lapse and binary decrements; Poisson/negative binomial for counts) remain the regulator-friendly baseline. SCOR's PLT shock-lapse model is an exemplar: a logistic GLM with attained age, premium-jump ratio, face band, smoker, and product structure as covariates produces both the central estimate and the confidence band, and is fully defensible in a rate filing.
- **Gradient boosted models** (XGBoost, LightGBM, CatBoost) for predictive power, especially with high-cardinality categorical variables typical in life portfolios. Recent literature shows GBMs match or exceed GLMs on out-of-sample claim frequency/severity prediction while preserving interpretability via SHAP. CatBoost's native handling of categorical columns is particularly useful for plan/state/distribution-channel features.
- **Survival and competing-risk models** — Cox proportional hazards, parametric (Gompertz-Makeham), Random Survival Forests, gradient-boosted survival — for joint mortality/lapse modelling. The competing-risk framing is essential for life: a policyholder can die or lapse, and treating one as censoring for the other (the standard naive approach) biases lapse rates downward at older ages.
- **Two-step / hybrid frameworks** like the SARIMA-Copula approach for mortality with climate covariates, or stacked models that use a GLM as the baseline and a gradient-boosted model on residuals — preserve interpretability while capturing non-linearity.

**Uncertainty quantification** is non-negotiable: bootstrap confidence intervals, Bayesian credible intervals, or quantile regression / probabilistic GBDT (NGBoost, LightGBM with quantile loss). Outputs are presented as central estimate ± 95% CI, never as point estimates.

**Explainability for regulators** must go beyond a SHAP plot. The Smallest.ai and Swept AI commentary on regulator expectations — and the NAIC AI Model Bulletin and state DFS letters (NY DFS Circular 2024-7, Colorado, California) — make clear that examiners want: (a) a mapping from every model feature to a filed rating factor or assumption rationale, (b) tested absence of proxies for protected classes, (c) immutable decision logs, (d) sensitivity analysis showing which features drive each segment's prediction. The tool should generate, for each AI-suggested assumption, a **regulator-ready explanation packet**: SHAP summary plus PDP plus interaction plots plus a structured table mapping features → rating factors → ASOP 23/56 disclosures.

The interaction model with the actuary is critical: AI **proposes** the new assumption with confidence bands and explanations; the actuary **adjusts** with a justification field; the tool **records** both the AI suggestion and the human override in the assumption-versioning system. This satisfies the AAA's principle that "actuaries should not abdicate to a model" and matches industry assumption-governance practice (Oliver Wyman survey: 80%+ of insurers have formal frameworks with proposer/reviewer/approver roles).

### 8. Conversational AI / chatbot interface

A natural-language interface over the actuarial dataset is high-value but high-risk. The literature on enterprise text-to-SQL (e.g., the ERATTA framework) and RAG (Béchard & Marquez Ayala on structured-output hallucination reduction) converges on a multi-layer pattern:

1. **Intent router.** A small LLM classifies the user's question: factual lookup ("What was 2024 mortality A/E for term super-preferred?"), exploratory ("Why did term lapses spike in Q3?"), or generative ("Draft commentary for the chief actuary on the term mortality study").
2. **Schema-grounded retrieval.** A FAISS/vector store of the canonical schema, business glossary (ACORD-style), and curated few-shot examples (~500 validated Q→SQL pairs is the production sweet spot per recent ERP-RAG literature, achieving ~92% query validity vs. 50–70% for ungrounded approaches).
3. **Constrained SQL generation.** The LLM emits SQL against the Gold semantic layer only (never against Silver/Bronze, never against PII-bearing columns without explicit RBAC). Validation: parse the SQL, check against the schema, run an EXPLAIN, reject anything that touches off-limits columns or returns more than N rows without aggregation.
4. **Result interpretation.** A second LLM call summarizes the table in natural language, anchored to the actual numbers, with explicit uncertainty caveats (credibility, exposure size).
5. **Hallucination scoring.** Five-metric scoring (faithfulness, answer relevance, context relevance, schema compliance, semantic accuracy) flags low-confidence answers for human review. Acurai's work shows that with input/output reformatting, hallucination rates can be driven essentially to zero for well-grounded contexts.

For commentary generation, RAG should retrieve prior approved commentary for similar studies as in-context examples; the model emits a draft, never publishes. Every commentary draft is tagged "AI-drafted, pending actuary review" until a human signs off. ASOP 41 disclosure requirements still apply: commentary that the responsible actuary signs becomes their work product.

Risks to mitigate: (a) the chatbot must never invent assumption values or A/E numbers — generation should be templated, with numerical slots filled from the structured query result; (b) it must refuse questions about data it cannot access; (c) the audit trail captures every prompt, retrieved context, generated SQL, and final answer; (d) regulators are increasingly skeptical of AI-generated narrative — disclose use prominently.

### 9. Reporting and communication layer

A single experience study has at least three distinct audiences. The reporting engine should generate three corresponding outputs from the same underlying result set:

- **Working actuary level (operational diagnostics).** The full A/E grid by every dimension, residual plots, credibility detail, exposure reconciliation, data quality summary, every methodology choice, full reference-table identification, every override with justification. Typically 30–80 pages, parameterizable, exported to Excel/Power BI for further investigation. This is the artifact that survives an external audit or a regulator data call.
- **Chief actuary level (assumption recommendation memo).** ~10 pages: executive summary of A/E vs. prior assumption, recommended new assumption with confidence bands, drivers of change, sensitivity to top-three drivers, peer-comparison context (vs. SOA ILEC), reinsurance implications, and an explicit recommendation with the actuary's rationale and any AI-model contribution. This is the document the chief actuary signs and presents to ALCO/ARC.
- **Board / regulator level (plain-English narrative).** 1–3 pages: what was studied, what was found, what is changing, what it means for reserves/pricing/capital, residual uncertainty, governance attestations. Avoid jargon; use one or two narrative charts. This satisfies the ASOP 41 communication standard for non-actuarial audiences.

Implementation: parameterized report templates (Quarto, Jinja, or a structured-document engine) consume the result set and produce all three outputs with shared numerical inputs, eliminating the drift between "Excel for actuaries, PowerPoint for executives, Word for the board" that plagues current practice. AI assistance is appropriate for prose drafting at all three levels, with human review and ASOP 41 disclosures.

### 10. Human-in-the-loop governance and audit trail

ASOP 56 (Modeling, effective Oct 1, 2020) is the explicit standard, supplemented by ASOP 23 (Data Quality), ASOP 41 (Communications), ASOP 25 (Credibility), and emerging guidance on AI governance from the NAIC and state DFSs. The American Academy of Actuaries' Model Governance Checklist enumerates the operational requirements. The tool must implement:

- **Assumption versioning.** Every assumption (mortality rate table, lapse curve, expense factor) is a first-class versioned artifact with a stable ID, semantic version, parent version, change rationale, supporting study reference, and effective date. The Oliver Wyman-style **centralized assumption repository** with proposer/reviewer/approver workflow is the design target. Every downstream model run pins exact assumption versions.
- **Data versioning.** Every study run pins the data snapshot via a Delta table version or Iceberg snapshot ID. Re-running yesterday's study tomorrow returns identical results unless the user explicitly chooses a new snapshot.
- **Code versioning.** Calculation engine and ML model artifacts are tagged with semantic versions and a build hash; results carry these tags.
- **Approval workflow.** Configurable per tenant: typical pattern is junior actuary runs the study → mid-level actuary peer-reviews data quality and methodology → senior actuary approves results and signs the assumption recommendation → chief actuary approves the recommendation memo → the assumption flows into pricing/valuation models. The system enforces gate transitions, captures e-signatures, and prevents post-hoc edits without a new version.
- **Immutable audit trail.** Append-only log of every action: data ingest, mapping change, study parameter set, run executed, result viewed, assumption proposed (by whom, on what data, with what model), override entered (with justification), approval granted, report generated, AI suggestion accepted or rejected. The log is queryable by regulators and is the basis for ASOP 56 §3.5.2 governance disclosures.
- **Model log.** Per the AAA ASOP 56 documentation guide and SOA model-governance frameworks: model identification, purpose, description, limitations, validation results, peer review, governance attestations, and reliance disclosures.
- **Separation of environments.** Dev (free experimentation), test (validation), prod (production results that flow into financial reporting). Promotion between environments is gated and logged.

This is not optional polish — it is the licensing condition. Insurers buying the tool will demand a SOC 2 Type 2 attestation and ISO 42001 (AI management) where AI is used; designs that meet ASOP 56 also meet most of these audit requirements.

### 11. Multi-tenancy and configurability

The product-like ambition — "works across multiple insurers with minor configuration tweaks" — sets the bar for the architecture:

- **Tenant model.** Single application, single set of code, isolated data per tenant. Default to **shared database, shared schema with mandatory `tenant_id` and Row-Level Security**; offer **dedicated database** for regulated/large tenants as a premium option (the WorkOS / Clerk / Frontegg pattern). Every query is tenant-scoped at the framework level so no application code can leak.
- **Configuration vs. customization.** The deliberate principle is **configuration over customization** (Bytebase, Frontegg, ScienceSoft). Configurable: source-system mappings, code-list translations, product-specific decrement rules, reference-table choices, credibility methods, segmentation dimensions, approval workflow steps, branding, regulatory regime (NAIC vs. CIA vs. PRA vs. APRA), report templates. Fixed: the canonical schema, the calculation engine code, the medallion layering, the audit trail format.
- **Configuration as code.** Every tenant configuration is a Git-managed YAML/JSON artifact with versioning, peer review, and CI tests. Changes are deployed, not hot-edited.
- **Regulatory regime abstraction.** Different jurisdictions require different reference tables (US: 2015 VBT, 2017 CSO; Canada: CIA tables; UK: CMI), different reserving bases (PBR/VM-20, IFRS 17, Solvency II), and different disclosure standards. Express each as a configuration profile that selects tables, methodology defaults, and report sections.
- **Feature flags and entitlements.** Tenant tier (basic / standard / enterprise) governs access to AI features, advanced credibility methods, custom connectors. Enterprise tier might add private-tenant LLM endpoints, dedicated worker pools, and regional data residency.
- **Authentication and SSO.** Each insurer wires its IdP (SAML/OIDC); tenant-scoped RBAC distinguishes data analyst, junior actuary, peer reviewer, senior actuary, chief actuary, auditor.
- **Data residency.** Region as a first-class field on the tenant; pipelines and storage routed to the tenant's region; control plane (billing, feature flags) shared globally.
- **Plugin extension points.** For genuinely tenant-specific logic that cannot be expressed as configuration (a unique product variant, a bespoke reinsurance treaty calculation), provide sandboxed Python/SQL extension points reviewed and signed off by the platform team — not core code forks.

The architecture diagram is therefore: per-tenant connectors → shared Bronze (partitioned by tenant) → shared Silver (canonical schema, partitioned by tenant) → shared Gold (study results, partitioned by tenant) → shared calculation engine, AI models, chatbot, reporting layer, all tenant-aware. Dedicated infrastructure exists only at the storage layer for tenants who require it.

---

## Caveats

- **Source quality.** Most sources cited above are reputable (SOA, AAA, NAIC, Casualty Actuarial Society, Databricks, Microsoft, Oliver Wyman, PwC, SCOR, RGA, peer-reviewed arXiv preprints). However, several vendor pages (Frontegg, Sapiens, ScienceSoft, IOMETE, smallest.ai, Sirion, KiTalent, Akur8, Aptitude) are marketing material; their architectural claims are directionally useful but should not be taken as benchmarks. Several "top 10 actuarial software" listings (gurukulgalaxy, scmGalaxy) are unverified third-party content.
- **AI accuracy claims.** Reported text-to-SQL accuracies (90–95%, 99% AE+IF anomaly accuracy) come from specific datasets and prompts; production performance on actuarial data with sparse cohorts and complex hierarchies will likely be lower without significant tuning. The "100% hallucination elimination" claim from Acurai applies to a specific benchmark with rewritten inputs — treat with skepticism. Plan for accuracy floors of 80–85% on first deployment with continuous improvement.
- **Regulatory landscape is moving.** ASOP 56 has been in force since Oct 2020; the NAIC Model Bulletin on AI was adopted in 2023 with rolling state-level adoption; New York DFS Circular 2024-7, Colorado SB-21-169, and California regulations create state-by-state divergence. The tool's regulatory configuration must be designed for evolution, not a snapshot of mid-2025 rules.
- **PLT methodology is empirical.** SCOR's PLT shock-lapse and mortality-deterioration models are based on industry data through approximately 2018–2019; emerging COVID-era and post-COVID experience may shift parameters materially. The tool should make it easy to refit these models on a tenant's own data rather than treating SCOR's parameters as universal.
- **Credibility methods conflict at the edges.** Limited Fluctuation and Bühlmann/Bayesian methods can produce materially different weights on small blocks; the AAA Credibility Practice Note explicitly recommends judgment overlay. The tool should expose both, not pick one.
- **Reinsurance accounting is in flux.** NAIC's 2024–2025 work on combination YRT/coinsurance contracts (Ref #2024-06) may change risk-transfer assessment requirements; the tool's reinsurance configuration should be designed to support both proportional and aggregate testing.
- **The "minor configuration tweaks across insurers" goal is genuinely difficult.** Industry surveys (Oliver Wyman 2022 modeling survey, the SOA Emerging Topics piece by Advani et al.) show that even within a single insurer, actuaries typically use 4–7 different tools for pre-model and post-model work, and 90% have not achieved end-to-end automation. A product that delivers it across multiple insurers will require sustained investment in mapping templates, regulatory profiles, and tenant onboarding tooling — likely 12–24 months of dedicated implementation effort per major tenant in early adoption, before maturing to the "tweaks-only" state. Set expectations accordingly.