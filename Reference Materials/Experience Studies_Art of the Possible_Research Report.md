# The Art of the Possible: AI-Powered Actuarial Experience Study Tools in Life Insurance

## TL;DR
- **The "art of the possible" today is a near-real-time, AI-augmented experience study platform** that ingests policy and claims data continuously, runs automated ETL/anomaly detection, produces credibility-weighted A/E results with predictive (GLM/GBM/survival) overlays, and lets actuaries interrogate results conversationally — with a human-in-the-loop governance layer that satisfies VM-20/PBR, ASOP 23/25/41/56 and the NAIC Model AI Bulletin.
- **Vendors and reinsurers are already shipping pieces of this stack**: Moody's AXIS Navigator (GenAI documentation copilot), WTW RiskAgility FM AI assistant, FIS Prophet's chatbot-style assistant, Milliman's Recon and AI-supported anomaly detection, SCOR's APEX experience-analysis platform and GenAI underwriting assistant, Munich Re HealthTech's SMAART text-to-SQL chatbot, RGA's AURA Next plus DigitalOwl, Montoux's experience-study automation, and Atidot's predictive in-force analytics.
- **What's still missing — and where the biggest wins lie** — is the integrated agentic workflow: an orchestration layer that autonomously performs data quality checks, segmentation, credibility-weighted assumption recommendations, scenario stress tests, dashboard generation, and draft assumption memos, while preserving full audit trails and explainable models (GLMs/GAMs and SHAP-explained GBMs) that actuarial reviewers and regulators can defend.

---

## Key Findings

**1. Today's experience-study workflow is bifurcated and fragile.** Large insurers run liability projections in heavyweight modeling platforms (Moody's AXIS, FIS Prophet, WTW RiskAgility FM, Milliman MG-ALFA/Integrate, Slope, Akur8 Life), but the *experience-study* layer that feeds those models still typically runs in SQL plus Excel — and Oliver Wyman survey data show "over half of participants spend more time running processes and preparing results than reviewing and analyzing." Open-source alternatives (the actxps R package — exposure calculations, A/E ratios, credibility, and a Shiny app) and low-code tools (Alteryx) have emerged precisely because the legacy workflow features brittle workbooks, recursive A/E calculation chains, and high human-error risk.

**2. AI/ML in experience analysis is moving from research to production.** Peer-reviewed and SOA-published work demonstrates concrete use cases: gradient boosting (XGBoost, CatBoost, LightGBM) and survival models (Cox, Random Survival Forest) for lapse and CLV modeling; ML-augmented Lee-Carter and GAM-based mortality forecasting; transfer-learning GBMs for data-scarce sub-populations; unsupervised anomaly detection (autoencoders, isolation forests) for data quality; and LLMs/RAG for documentation, regulatory-text summarization, and unstructured-data extraction.

**3. Regulation is converging on "explainable, governed, human-supervised AI."** The NAIC Model Bulletin on the Use of AI Systems (adopted Dec 2023, now adopted by ~24 states) requires a written AIS Program covering governance, risk management, validation, documentation and consumer notice. ASOP 56 (Modeling), ASOP 23 (Data Quality), ASOP 25 (Credibility) and the new ASOP on Setting Assumptions, plus the American Academy of Actuaries' 2024 *Actuarial Professionalism Considerations for Generative AI* and APS X2 v1.1 (effective January 2026), constrain how GenAI can support actuarial conclusions. NIST AI 600-1's GenAI Profile is increasingly cited as the de-facto control framework.

**4. The "great" experience study is principle-based, not just compliance-based.** Industry guidance from Oliver Wyman, Deloitte and the Academy of Actuaries emphasizes guardrails over rigid procedures: documented end-to-end assumption proposals, independent challenge, central assumption inventories, separation of production and development environments, and "biweekly working groups" that bridge data, modeling, valuation, tax and product teams. Quality is judged on credibility (Limited Fluctuation, Bühlmann/Bühlmann-Straub Empirical Bayesian), the granularity-vs-stability trade-off, internal consistency across pricing/valuation/forecasting, retrospective testing, and clarity of communication to non-actuarial stakeholders.

**5. Vendor solutions already implement many "art of the possible" components individually** — but no single product yet ties them into an end-to-end agentic experience-study workflow.

---

## Details

### 1. Current State of Experience-Study Tooling

**Heavyweight commercial platforms** dominate liability modeling and increasingly market experience-analysis modules:
- **Moody's AXIS** (formerly GGY AXIS): the "Experience Analysis" function compares actual vs. expected on chosen assumptions; Moody's launched **AXIS Navigator** in September 2025, a GenAI assistant indexing 26,000+ help texts and KB articles, with cited responses and conversational memory.
- **FIS Insurance Risk Suite (Prophet)** with Experience and Rating Manager (formerly Glean) and a chatbot-style AI assistant for model navigation and maintenance; Prophet markets predictive analytics for pricing, reserves and claims.
- **WTW RiskAgility FM** includes an AI assistant for model design, coding, debugging and documentation; data-assumption linkage is segregated from models for auditability. ResQ (P&C reserving) added rapid data-review workflows that compress 5-day reviews to hours.
- **Milliman MG-ALFA / Integrate** for first-principles modeling; Milliman Recon and the Life & Annuity Experience Studies offering use predictive (GLM-style) industry surrender models with documented A/E calibration (e.g., 99.2% A/E on out-of-sample VA GLWB data).
- **Slope (now Akur8 Life)** emphasizes a transparent, cloud-native experience-study and projection engine.
- **Addactis, Sapiens** and similar SaaS suites bundle experience-study modules with IFRS 17 / RBC / Solvency II reporting.

**Open-source and lightweight ecosystems**:
- **actxps (R, on CRAN, v1.6.1 as of Sept 2025)** is the leading open-source experience-study package — `expose()` builds policy- or calendar-year exposure records from census data; `exp_stats()` and `trx_stats()` compute observed termination rates, A/E with multiple expected bases, and Limited Fluctuation credibility; `step_expose()` plugs into tidymodels; `exp_shiny()` launches an interactive exploration app. The package's recent additions include confidence intervals, calendar-year split exposures and assumption-development helpers.
- Python and R ecosystems built around pandas, lifelines, scikit-survival, statsmodels, xgboost/lightgbm, and visualization in plotly/Tableau/Power BI.
- Low/no-code: Alteryx workflows for recursive A/E, optimized rate-table generation, and assumption-repository sourcing.

**Documented pain points** (Oliver Wyman, SOA, The Actuary Magazine, Montoux):
- Multiple administrative source systems and heavy ETL effort.
- Recursive A/E calculations done in interlocking spreadsheets with version-control issues.
- Annual cadence — too slow to spot emerging trends; Montoux case study shows process compression from ~3 months to a near-real-time monthly refresh.
- Information silos: pricing, valuation, claims and distribution all rebuild similar studies independently.
- Documentation under ASOP 41 / ASOP 56 is often retrofitted at the end rather than continuously generated.
- Difficulty translating granular results to non-actuarial audiences.

### 2. AI/ML Applications in Experience Studies

**Automated ETL and data-quality checking.** Milliman's December 2025 paper *AI-Supported Anomaly Detection in Insurance* and Groll/Khanna/Zeldin (TU Dortmund 2024) document unsupervised methods — k-NN distance, Isolation Forests, autoencoders, variational autoencoders, silhouette scoring — that flag policy-record outliers, mismatched effective/expiration dates, claim-amount inconsistencies and duplicate insureds without labeled training data. Continuous monitoring replaces periodic checks. ML-driven cleansing handles address standardization, vehicle/property-attribute normalization and probabilistic deduplication.

**Predictive modeling for assumption setting.**
- **Lapse**: Loisel et al. (arXiv 2019) and follow-up work show XGBoost and SVM outperform logistic regression and CART, especially when the loss function is reframed as an economic/profit objective. Tree-based competing-risks survival models (Random Survival Forest, gradient-boosting survival) outperform parametric Cox approaches for individual CLV and lapse prediction.
- **Mortality**: Application of GBM/random forest/SVM to Lee-Carter-style problems (Levantesi & Pizzorusso, MDPI 2019); GAM + ML hybrids for COVID-shock periods (Nalmpatian et al. 2023); transfer learning for data-poor markets using CMI/HMD synthetic data (UK case study, 2024); Bayesian GAMs for old-age mortality; multi-population hierarchical models with global LightGBM + local correction (PMC 2025); covariate-driven stochastic models that incorporate economic, environmental and lifestyle variables to forecast improvement scales.
- **Expense / utilization**: Tweedie boosted trees (CatBoost) for zero-inflated insurance loss data.
- Best-in-class implementations pair **GLMs/GAMs** (Akur8's transparent core) with **SHAP-explained GBMs** so that the model output is both predictive and explainable to model-validation teams and regulators.

**LLMs / Generative AI for actuarial commentary, documentation and regulatory text.**
- The SOA Research Institute's *A Primer on Generative AI for Actuaries* (Carlin & Mathys, Feb 2024) and the follow-on *Operationalizing GenAI for Actuaries* RFP scope coding assistance, document/report drafting, regulatory-text comparison, prompt engineering and a risk/governance framework.
- Deloitte's *Advanced Applications of GenAI in Actuarial Science* (Hatzesberger & Nonneman, arXiv 2506.18942, 2025) describes four implemented case studies including LLM-derived features for claims-cost prediction, **RAG-based market comparison** across insurer annual reports, fine-tuned vision LLMs for image-evidence classification, and a **multi-agent system that autonomously analyses a dataset and writes the report**.
- *ActuaryGPT* (Cambridge British Actuarial Journal) and Globebyte's NIST GenAI-Profile guidance sketch how LLMs can draft assumption memos, summarize meeting notes, compare regulatory versions, proofread filings and explain technical results to non-actuarial readers.

**AI agents / agentic workflows.**
- **Kyndryl's Agentic AI Framework for actuaries** explicitly targets data ingestion, transformation, validation, model building and validation, what-if scenario simulation across economic and demographic variables, and traceable audit logs — integrating with FIS Prophet.
- McKinsey's 2026 paper on agentic AI in insurance highlights human-in-the-loop stage gates, traceability from requirements to test evidence, and treating agents as a production system with privileged-access controls.
- AWS published reference patterns (Strands Agents) with master-orchestrator/sequential-workflow architectures that map cleanly to the multi-step nature of an experience study (data prep → exposure calculation → A/E → credibility → assumption proposal → memo).
- Adversarial self-critique and Constitutional-AI patterns (Anthropic) are emerging for high-stakes financial workflows where one agent generates output and a critic agent challenges it.

### 3. Design Principles for AI in Regulated Actuarial Environments

**Human-in-the-loop is a risk control, not a limitation.** Modern HITL architectures route only edge cases, escalations and ethical gray zones to humans, while continuous-learning pipelines retrain on validated decisions. The MDPI 2026 systematic review and Singh's five-step banking governance framework both emphasize structured oversight at multiple stages.

**Explainability is increasingly non-negotiable.** The NAIC Model Bulletin (adopted ~24 states, plus state-specific variants in NY, CO, CA) requires insurers to assess the "transparency and explainability of outcomes," document validation and testing, and provide consumer notices. The EU AI Act, NIST AI 600-1 and the FCA's stance on credit/lending all reinforce that "the AI said so" is not defensible.

**Governance components that need to be designed in from day one** (synthesized from ASOP 56, AAA Model Governance Practice Note, Oliver Wyman's actuarial-governance framework, NAIC AIS Program and SCOR/Munich Re practice):
- Documented model inventory with risk classification.
- Separation of production and development environments.
- Independent input/calculation/output validation; assumption inventories with owners.
- Change management with peer review and senior approval gates.
- Version control for both code and assumptions.
- Reproducibility of model output upon rerun.
- Bias and fairness testing for any predictive model affecting consumers.
- Vendor/third-party model audit rights.
- Continuous monitoring and structured escalation.
- For GenAI specifically: scope determination (is the LLM doing actuarial work?), citation requirements for retrieved facts, prompt logs, output review records, hallucination-rate testing, and disclosure under ASOP 41.

**UX design for data-heavy professional tools.** Best-in-class tools (Tableau, Power BI, Akur8, Slope/Akur8 Life, Montoux, Atidot, AXIS Navigator, Munich Re HealthTech SMAART) share several patterns: drill-down from aggregate A/E to seriatim records; one-click sensitivity sliders; side-by-side prior-vs-current assumption comparisons; "single source of truth" data layer accessed by valuation, pricing and product teams; embedded chatbot for data interrogation; and exportable cited audit packs.

### 4. Specific Innovations Most Relevant to Life Actuarial Teams

**Conversational interfaces for actuarial data.** Munich Re HealthTech's **SMAART** platform — built on Oracle Autonomous Database 23ai with OCI Generative AI — lets actuaries, underwriters and executives query insurance-policy performance in natural language ("Show me policies with high claims in 2023" or "What are the worst-performing policies?"), with RAG-based document Q&A scanning company knowledgebases for cited answers in multiple languages. The project went live in four months and reportedly compressed a financial-reserve calculation cycle from 10–15 days to about 20 minutes for some reports. Parallel academic work (eSapiens, CoRAG, Self-RAG) shows that retrieval-augmented Text-to-SQL can hit ~78–79% execution accuracy on enterprise schemas, with chunk size and retrieval policy being the dominant levers.

**Automated dashboard generation.** Tools like Tableau's insurance accelerators, automated report builders and emerging GenAI dashboarding agents can convert experience-study output (q_obs, exposure, A/E, credibility) directly into role-appropriate views — actuary-level seriatim, executive-level KPIs, and regulator-ready documentation packs. Montoux's case study with a global L&H insurer reports a transition from annual to monthly experience studies with dashboards updated continuously.

**Forward-looking assumption setting.** AI is increasingly combining historical A/E with macro and external data: covariate-driven stochastic mortality models include economic, environmental and lifestyle variables; cohort-based forecasts using CPS data show signs of life-expectancy deceleration; ML-augmented mortality-improvement models capture turning points the Lee-Carter framework misses. SCOR's **VITAE** uses ML to capture correlations among biometric risk factors, automating ~50% of substandard-risk assessments traditionally done manually; SCOR's **Biological Age Model (BAM)** uses ML on wearables/step-count data correlating with population mortality.

**Agent-based multi-step actuarial analysis.** The pattern emerging across Kyndryl, McKinsey, AWS and Deloitte case studies is: an orchestrator agent decomposes a study into (1) ingest and validate data → (2) flag anomalies → (3) build exposure records → (4) compute A/E by segment → (5) test credibility and recommend granularity → (6) fit predictive overlay (GLM/GBM with SHAP) → (7) propose assumption updates with margins → (8) draft memo and dashboard → (9) route to reviewer. Each step is logged, cited and reproducible.

### 5. What "Great" Looks Like in Experience Studies

Drawing from the Academy's *Life PBR Assumptions Resource Manual*, SOA research (*Practical Analysis of PBR Mortality Credibility for Term Insurance*, 2019; *Experience Study Calculations Educational Tool*), Oliver Wyman's actuarial-governance work, and Deloitte's *End-to-End Assumption Documentation Practices*:

- **Statistical credibility**: Use either Limited Fluctuation Method or Bühlmann Empirical Bayesian; for VM-20 mortality, the minimum probability is 96% with error margin ≤5%; full credibility requires >3,000 deaths historically; credibility is calculated by amount, not just count, and may use mortality-segment aggregation when underwriting processes are similar.
- **Granularity vs. stability**: Avoid slicing data so finely that each slice is non-credible; build pricing-aligned segmentation (e.g., 360 mortality cells of sex × risk class × face band × product × underwriting) but credibility-weight to industry tables (e.g., 2017 CSO/VBT) at the cell level. The trend is *high granularity for management views* with *credibility-weighted blending for valuation*.
- **Margins**: VM-20 mortality margins are prescribed by credibility level, attained age, credibility method; lapse/expense margins use professional judgment with retrospective testing required by Section 9.C.2.c. Cumulative-margin reasonableness should be tested in aggregate.
- **Assumption governance**: Annual assumption-review calendar with frequency tied to materiality; centralized assumption inventory; written proposals reviewed by governance committee; separation of duties between recommender and reviewer; "guardrail" rather than "single-path" policies (in keeping with ASOP 56 §3.1).
- **Documentation**: ASOP 23 (data sources, limitations), ASOP 25 (credibility), ASOP 41 (communications), ASOP 56 (model governance, validation, weaknesses); Deloitte's eight-component framework (general standards, review planning, internal experience, external experience, assumption proposal, approved assumptions, communication, monitoring).
- **Regulatory compliance**: VM-20 PBR (deterministic, stochastic, and net premium reserves); IFRS 17 / LDTI; Solvency II / ORSA; NAIC AI Bulletin's AIS Program covering any AI used in the experience-study workflow.
- **Communication**: Visual dashboards layered for actuary, product manager, CFO and regulator; clear A/E with confidence intervals; explicit documentation of data exclusions (e.g., COVID periods often partially excluded but referenced); plain-English commentary on drivers.

### 6. Industry Examples and Vendor Solutions

- **Milliman**: Recon platform with explainable industry-data predictive models (e.g., 99.2% A/E VA GLWB surrender model); annual LTC industry guidelines built from 460,000 claims and 30 million life-years; *AI-Supported Anomaly Detection in Insurance* (Dec 2025) and *Data Quality in the Insurance Sector: How ML and AI Can Drive Improvement*.
- **WTW**: ResQ (P&C reserving), RiskAgility FM with AI design/debug/documentation assistant; vGrid SaaS compute; UNIFY workflow orchestration; actuarial-function outsourcing services.
- **Moody's AXIS**: AXIS Navigator GenAI documentation copilot (Sept 2025); experience-analysis modules; Enterprise Link governance environment.
- **FIS Prophet**: Insurance Risk Suite with experience analysis; Process Orchestrator for end-to-end workflow automation; built-in chatbot AI assistant; predictive-analytics module for pricing and reserves.
- **SCOR**: APEX global experience-analysis platform standardizing studies based on actuarial best practice; Data Analytics Solution Platform (DASP) hosting VITAE biometric calculator and BAM biological-age model; Velogica (automated underwriting); SCOR Digital Solutions GenAI underwriting assistant; ReMark partnership with Atidot for in-force performance.
- **Munich Re**: HealthTech's SMAART tool plus Oracle GenAI chatbot for natural-language analytics; alitheia underwriting AI platform leveraging anonymized market-wide data; Insure AI / aiSure offering performance insurance for AI models (a tangential but relevant AI-risk capability).
- **RGA**: Aura Next automated underwriting; AI-augmented underwriting research presented at SOA Predictive Analytics Symposium; investment in DigitalOwl (GenAI for medical-record summarization across underwriting evidence); 2024 GenAI study on EHR/Rx evidence and protective value.
- **Verisk + SCOR**: Joint analytics platform applying ML/NLP to electronic health records for accelerated underwriting.
- **Montoux**: Cloud-based actuarial automation specifically marketing experience-study automation; case study compressing experience-study cycles from ~3 months to two days; Montoux Model Copilot LLM assistant.
- **Atidot**: Cloud SaaS predictive-analytics platform for life insurers — automated ETL, lapse/persistency prediction, in-force value optimization, partnerships with Guardian Life, Sapiens, NTT DATA and SCOR/ReMark.
- **Akur8 / Akur8 Life (Slope)**: Transparent GLM/GAM auto-modeling, SaaS, embedded ML through the platform.
- **Hyperexponential, Zywave, Microsoft+Cognizant, AWS**: Agentic-AI patterns for insurance underwriting and operations applicable to experience studies.

---

## Caveats

- Most vendor claims (e.g., "compresses 10–15 days to 20 minutes," "near-real-time monthly experience studies," "99.2% A/E") are **vendor- or case-study-reported** and have not been independently audited; in proof-of-concept settings, actual gains depend heavily on data quality, integration scope, and the rigor of governance overlays.
- Several capabilities described are **in-development, in-pilot, or framework-level** rather than fully productized — particularly end-to-end agentic workflows, automated assumption-memo drafting, and full RAG over actuarial documentation. Phrasing in source material often uses conditional verbs ("could," "may," "promises to"); this report has tried to flag what is shipping today versus what is forward-looking.
- The **regulatory landscape is moving fast**: the NAIC AI Model Bulletin is principle-based and has been adopted unevenly across states; multiple states have additional or different requirements (NY, CO, CA); APS X2 v1.1 takes effect in January 2026; ASOP on Setting Assumptions was an exposure draft as of late research. A production AI experience-study tool must be designed for a regulatory target that is still moving.
- The **NAIC, SOA and Academy of Actuaries** explicitly note that GenAI does not relieve actuaries of professional responsibility under ASOPs — adoption of AI tools should not be confused with delegation of actuarial judgment.
- Predictive ML models (GBM, neural nets) for assumption setting raise **fairness/disparate-impact questions** that traditional GLMs and credibility-weighted tables do not — particularly when external data (wearables, social determinants of health, credit-style attributes) are layered in. Best practice combines XAI techniques (SHAP, LIME), bias testing, and reliance on transparent GLM/GAM cores wherever possible.
- Open-source tooling (actxps, lifelines, scikit-survival) is excellent for analysis but **does not provide the governance, audit, role-based access control, change management or model-inventory features** required for a regulated production environment; these must be added by the implementing firm or layered via enterprise platforms.
- Some of the most frequently cited "AI for experience studies" vendor pages emphasize **underwriting and claims** rather than experience-study assumption setting per se — readers should distinguish front-of-book underwriting AI from assumption-setting/in-force AI when evaluating tools.