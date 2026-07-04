# Designing a Simplified but Mechanically Correct Traditional Embedded Value (TEV) Model for a Multi-Product Life Actuarial Prototype

## TL;DR

- **Build the core TEV engine around the canonical identity `TEV = ANW + VIF`, where `VIF = PVFP − PVCoC`**, using a single risk discount rate (RDR), best-estimate assumptions, and statutory book profits projected on monthly or annual model points; this is exactly the "Traditional EV" (TEV / APM-style) approach defined in the American Academy of Actuaries' 2009 EV Practice Note and is well-suited to a prototype because it is deterministic, single-scenario, and does not require stochastic options-and-guarantees machinery.
- **Implement a product-specific cash-flow projection layer (Term, WL, UL/ULSG, VUL, Deferred Annuity) on top of a common decrement engine**, all sharing the same in-force-driven survivorship recursion (qx, lapse, surrender, maturity) and the same statutory-profit aggregator. The open-source `lifelib` (modelx-based) BasicTerm and CashValue/savings libraries plus `cashflower` provide working, MIT/LGPL-licensed Python reference implementations that you can adapt directly; lifelib's `cluster` library demonstrates k-means model-point compression at ~10:1 reduction with minimal accuracy loss.
- **Wire the experience-study (A/E) module to TEV through a versioned assumption set object** containing decrement tables (mortality, lapse, surrender, premium persistency, annuitization) tagged by product/duration/attained age, plus a small fixed set of CFO-Forum-style sensitivity scenarios (±10% lapse, ±5% mortality, ±10% maintenance expense, ±100 bp interest, ±1% expense inflation) so actuaries can: review A/E → propose assumptions → run baseline TEV → run sensitivities → approve/reject — all from a single workflow.

---

## Key Findings

1. **TEV is a thin layer of mechanics on top of a statutory cash-flow projection.** The arithmetic is well-codified (Academy of Actuaries 2009 Practice Note Q10–Q22; Tremblay SOA 2006; Frasca/LaSorella SOA 2009). A prototype only needs to implement: (i) ANW from a stat balance sheet snapshot, (ii) projected statutory book profits per period, (iii) projected required capital and the spread (RDR − after-tax earned rate) on it, and (iv) discounting at the RDR. Everything else — analysis of movement, value of new business, TVOG, stochastic O&G — can be deferred.

2. **A single, shared projection engine handles all five products** if you model: premium income, investment income on reserves, benefits (death/surrender/maturity/annuity), commissions, expenses, reserve change, and tax. The product differentiation lies in **how reserves and benefits are calculated**, not in the EV mechanics around them.

3. **Model-point compression to ~1,000–5,000 points** (typically a 10–100× reduction from seriatim) using either stratified grouping or k-means clustering on the policy attributes that drive profit (issue age, attained age, gender, risk class, duration, sum assured / account value, premium mode, product code) is standard practice and is sufficient for a prototype.

4. **CFO Forum's prescribed sensitivities provide the canonical sensitivity grid** for decrement testing: ±10% lapse (multiplicative), −5% mortality for life business / +5% for annuity business, ±10% maintenance expense, ±100 bp risk-free curve shift, and (optionally) ±1% expense inflation. These are exactly the tests that actuaries expect to see after an experience study.

5. **Open-source Python tooling is mature enough to build a prototype on**: `lifelib` (with `modelx`) for transparent, spreadsheet-like cash-flow modelling; `cashflower` for a more conventional def-based projection framework; `pyliferisk` / `lifeActuary` for life-contingencies primitives; `pymort` for SOA mortality tables; `actxps` (R, with a Python-callable analogue achievable) for the A/E layer. A pragmatic prototype is a thin Python package wrapping numpy/pandas vectorized projections per product, with a single assumption-set object passed in.

6. **The assumption-approval workflow is essentially a four-stage pipeline**: experience study → proposed assumption (with margin / credibility considerations per ASOP and the Academy's PBR Assumptions Resource Manual) → impact analysis (TEV baseline + sensitivities + ΔTEV vs. prior assumption set) → governance sign-off (independent review + documentation in an assumption repository).

---

## Details

### 1. TEV Mechanics for Each Product Type

The shared TEV identity (Academy 2009 Practice Note Q10–Q16) is:

```
TEV = ANW + VIF
VIF = PVFP − PVCoC
PVFP = Σ_t  BP_t / (1 + RDR)^t
PVCoC = Σ_t  RC_{t-1} × (RDR − i_t^after-tax) / (1 + RDR)^t
BP_t = (Premiums + Investment income + Fee income)_t
     − (Claims + Surrenders + Maturities + Commissions + Expenses + ΔReserve + Tax)_t
```

where BP is **statutory book profit** computed after resetting invested assets equal to the net statutory liability at each period start. This formulation is identical for all five products; only the cash-flow items and reserve definitions change.

**Minimum viable inputs per product (per model point):**

| Product | Minimum inputs | Key cash flows | Reasonable simplifications |
|---|---|---|---|
| **Term Life** | Issue age, sex, risk class, policy term, gross premium, sum assured, statutory reserve at t=0 | Premium in, COI/death claim out, commissions, maintenance expense, ΔCRVM reserve | Use a half-year-of-mortality convention; ignore conversion option; assume premium paid annually; use simplified CRVM reserve formula or load a pre-computed reserve curve per cell |
| **Whole Life** | Issue age, sex, plan code, gross premium, sum assured, current net level premium reserve, current cash value, dividend scale | Premium, death claim, surrender (CV), dividends, expenses, ΔNLP reserve | Treat dividends as a fixed % of reserve or use a single illustrated scale; reserve = pre-loaded NLP reserve table by attained age; ignore PUA/RPU options |
| **UL / ULSG** | Attained age, sex, risk class, current account value, current shadow account (if ULSG), current statutory reserve, COI scale, expense load schedule, credited rate, target premium | Premium → AV; COI, expense load, fund charges → AV deductions; interest credit → AV addition; death = max(DB, AV × corridor); surrender = AV − surrender charge; ΔAG38/CRVM reserve | Use deterministic single-scenario crediting (credited rate = earned rate − spread); approximate ULSG reserve as max(AG38 formula proxy, AV); ignore secondary-guarantee shadow account dynamics if no SG in scope; assume premium persistency rate by duration |
| **VUL** | Attained age, sex, current separate account value, fund mix, M&E charge, COI scale, fund expense, current statutory reserve | Premium → SA; M&E and COI deduct from SA; SA grows at assumed equity/bond return; death = max(DB, SA × corridor); surrender = SA − surrender charge | Use a single deterministic separate-account return (e.g., 6% net of fund fees) — TEV is deterministic, so no stochastic GMxB modelling required at prototype stage; ignore guaranteed-benefit riders or treat them as a static reserve add-on |
| **Deferred Annuity (FA/VA/FIA)** | Attained age, sex, current account value, surrender-charge schedule remaining, GMWB/GLWB rider indicator (if any), guaranteed minimum crediting rate, current statutory reserve | Premium (single or flexible) → AV; credited interest; partial withdrawals; surrender = AV − surrender charge; annuitization election; ΔCARVM reserve | Use spread-margin approach for FA: profit_t = AV_{t-1} × (earned_rate − credited_rate − expense bps); ignore living benefits in v1 or treat them with a static fee margin; treat GMDB as max(0, DB − AV) × qx |

In all five products, the **shared engine** computes for each model point and each time step `t`:

```
in_force_t = in_force_{t-1} × (1 − qx_t) × (1 − lapse_t)
reserve_t  = product_specific_reserve(t)
BP_t       = product_specific_BP(t)
RC_t       = required_capital(reserve_t, product_t)        # e.g., k × reserve or scaled NAIC C2/C3 factor
CoC_t      = RC_{t-1} × (RDR − i_after_tax)
```

then aggregates and discounts.

### 2. Model Point Approach

**Best-practice grouping criteria for a seriatim in-force file:**
- Product code / plan code (always)
- Issue year cohort (5-year bands typical)
- Issue age band (5-year bands)
- Gender
- Smoker / risk class
- Premium mode (annual / monthly)
- For UL/VUL/annuities: bands of account value and (for ULSG) presence/strength of secondary guarantee
- For deferred annuities: bands of remaining surrender-charge period

The classical approach is **stratified grouping** (define the multidimensional cell structure and aggregate `policy_count`, `sum_assured`, `premium`, `reserve`, `AV` by summing within each cell, taking weighted-average ages). This is what most pricing-system grouping tools do and what is implemented in lifelib's BasicTerm_M sample (10,000 model points starting from a much larger seriatim file).

**Cluster-analysis grouping** (k-means on a representation vector that mixes attributes and projected cash-flow features) is the modern alternative. lifelib's `cluster` library demonstrates this on 10,000 seriatim term policies reduced to 1,000 model points (10:1 reduction ratio) using scikit-learn KMeans; Milliman and Oliver Wyman both publish that 100:1 to 500:1 reductions are achievable in production with stochastic models while preserving accuracy within 10 bps on the targeted PV metric.

**Number of model points for a prototype:** the published evidence supports the following rules of thumb:
- 100–500 model points per product line is enough for a credible prototype demonstration on small in-force blocks.
- 1,000–5,000 per product is typical for production deterministic TEV.
- 10,000+ is only justified when stochastic O&G valuation is added (out of scope for prototype).

For a prototype tool the recommended starting point is **stratified grouping into ~500–1,000 model points per product**, with optional k-means clustering as a "v2" enhancement.

### 3. ANW Calculation in a Simplified TEV

ANW is the **realizable value of statutory capital and surplus**. The Academy practice note (Q11) gives the minimum viable approach:

```
ANW = Statutory C&S
    + Asset Valuation Reserve (AVR)               # add back: it's really surplus
    + Other surplus-like liabilities (IMR per company policy)
    − Non-admitted assets without realizable value
    − Goodwill / DAC and other intangibles
    + Mark-to-market adjustment on assets supporting free surplus (net of tax)
```

**Simplifications standard in approximate TEV models:**
1. Skip the mark-to-market step entirely (use book values) — common in internal management EV.
2. Treat AVR as the only material adjustment to surplus.
3. Apply a single blended tax rate (e.g., the company's effective stat tax rate, ~21% federal in the US).
4. Allocate ANW to product lines pro-rata to required capital (not to in-force-asset-value), which is sufficient for relative comparisons of decrement-sensitivity impacts.

For the prototype, **a one-line ANW input** ("statutory capital & surplus + AVR, post-tax, as of valuation date, optionally split by line of business") is fully adequate. The interesting actuarial work — and what decrement assumptions actually move — lives in VIF.

### 4. VIF / PVFP Projection

The standard deterministic recipe (Academy 2009 Q12–Q14; Frasca/LaSorella 2009 Sections 2–3):

1. Project, at each future period t (monthly or annual) and for each model point: premiums, investment income on reserves, benefits paid, expenses, commissions, reserve change, taxes. Assets are *notionally* reset to equal stat reserves at each period start.
2. Compute statutory book profit `BP_t` = post-tax net income on that reset basis.
3. Discount: `PVFP = Σ BP_t × v_t` where `v_t = (1 + RDR)^{-t}`.

**Standard RDR choices:**
- Cost of equity from CAPM: `RDR = Rf + β × ERP`. The 2009 Academy note observes that North American EEV publications used RDRs of **7.0%–9.0%** in the late 2000s; with current (2025–26) long Treasury yields of ~4.0–4.5%, a β of ~1.0, and an ERP of ~5%, an indicative TEV RDR is ~9.0–9.5%. For a prototype, an editable parameter defaulting to ~8.5–9.5% (or whatever the company's pricing hurdle rate is) is fine.
- A WACC alternative, which implicitly reflects debt, is also acceptable.
- For multi-product testing, use **one RDR for general-account products and a slightly higher one for variable products** (the published convention).

**Simplified projection conventions:**
- Annual time step (or quarterly) is fine for a prototype; monthly is the production norm.
- Use a flat earned rate by major segment (e.g., 5.0% for fixed GA, 6.0% gross for separate account).
- Use a single tax rate.
- Apply the half-year (or mid-period) convention to within-period decrements.
- Use a fixed expense per policy + % of premium loadings, indexed by an expense inflation assumption.

### 5. Cost of Capital

The Academy 2009 Practice Note (Q15–Q21) formula:

```
CoC_t = RC_{t-1} × (RDR − i_t^after-tax)
PVCoC = Σ_t CoC_t × v_t
```

In words: capital that is locked up earning the after-tax investment rate of return (rather than the RDR shareholders demand) creates a frictional drag equal to the spread, multiplied by the level of required capital, present-valued at the RDR.

**Simplified approaches when a full RBC model is not available** (standard prototype practice):
1. **Reserve-percent proxy:** `RC_t = k × Reserve_t`, with `k` calibrated per product to roughly reproduce the company's blended RBC ratio. Typical illustrative values: ~3% for term, ~4–5% for whole life, ~5–8% for UL/ULSG (higher for SG-heavy blocks), ~3–4% for VUL, ~3–5% for fixed annuities, ~5–7% for VAs with guarantees.
2. **Premium- or AV-percent proxy:** `RC_t = k × AV_t` for account-value products; `RC_t = k × NAR_t` for protection products (since C2 mortality risk scales with net amount at risk).
3. **NAIC RBC factor approximation:** use a stripped-down C1 (asset risk, ~0.4% of bonds) + C2 (insurance risk, ~$1.50 per $1,000 NAR for individual life) + C3 (interest rate risk, ~0.75–1.5% of reserves) per period and multiply by the target RBC ratio (e.g., 400% Company Action Level × 2 = 800% authorized control, or whatever the company targets).
4. **The release-pattern shortcut:** assume RC releases proportionally to reserves or in-force count over the projection horizon, so you only need the t=0 RC and a release pattern.

For a prototype, option (1) — a single editable percentage of reserve per product — is sufficient and is what published academic illustrations (e.g., Tremblay SOA 2006) use.

### 6. Sensitivity Analysis in TEV

The **CFO Forum EEV Principles (2016, Appendix 2)** prescribe the canonical sensitivity set, which is also what the Academy 2009 Practice Note recommends and what large reporters (Generali, Old Mutual, etc.) actually disclose:

| Sensitivity | Definition | Application |
|---|---|---|
| **Risk-free rate ±100 bp** | Parallel shift in the reference yield curve | Reinvestment + discounting + crediting |
| **Equity / property values −10%** | Reduction in starting market values | Mainly affects ANW & separate-account products |
| **Equity / property return −1%** | Reduction in assumed future return | VA / VUL & equity-backed products |
| **Swaption / equity volatility +25%** | Increases option costs | Stochastic — skip in prototype |
| **Maintenance expense −10%** | Multiplicative reduction in projected expense | All products |
| **Lapse rate −10% (multiplicative)** | e.g., 5.0% → 4.5% | All products with lapse-sensitive profits |
| **Lapse rate +10%** | symmetric counterpart commonly added | All products |
| **Base mortality −5%** *(life)* | Multiplicative reduction | Term, WL, UL, VUL |
| **Base mortality −5%** *(annuity)* | Disclosed separately (longevity test) | Deferred annuities |
| **Required capital = statutory minimum** | Compare RDR-based to minimum-capital basis | Diagnostic |

The CFO Forum explicitly states each sensitivity is performed **in isolation**, with reserving basis held constant unless misleading to do so, and using **proportional/multiplicative** shocks rather than absolute additive ones.

**For an experience-study-driven workflow**, the most useful subset is:
- Lapse: −10%, +10% (and "shock lapse +25% at end of level term" for Term/ULSG)
- Mortality: −5%, +5% (life); +5%, +10% (annuity longevity)
- Premium persistency: ±10% (UL/VUL)
- Maintenance expense: ±10%
- Expense inflation: ±1%

These five families × baseline = 11 scenarios is a tractable default sensitivity grid for the prototype.

### 7. Assumption Approval Workflow (A/E → TEV → Approval)

Drawing on the Academy's *PBR Assumptions Resource Manual* (2019), the SOA's *Assumption Governance* article (Rowley, 2021), and the *Actuary Magazine* Assumption Governance feature, the canonical four-stage workflow is:

**Stage 1 — Experience study (already in the existing tool):**
- Compute A/E ratios by product / attained age / duration / risk class.
- Apply credibility theory (limited fluctuation or Bühlmann) to determine how much weight actual experience deserves vs. the prior assumption or an industry table.
- Output: a "raw experience rate" and a credibility-weighted "indicated rate".

**Stage 2 — Proposed assumption:**
- Apply judgment overlays (mortality improvement, trend, anti-selection at end of level term, etc.).
- Apply margins where required (statutory / GAAP LDTI); for TEV, use **best-estimate without PAD** (Academy Q28).
- Output: a versioned, signed-off "proposed assumption set" file (table by attained age × duration × product).

**Stage 3 — TEV impact testing (the new module):**
- Load the prior assumption set → run TEV → store baseline.
- Load the proposed assumption set → run TEV → compute ΔANW, ΔVIF, ΔTEV by product line.
- Run the standard sensitivity grid on the proposed set.
- Produce a one-page "assumption change impact summary": (a) ΔTEV vs. prior; (b) sensitivity envelope; (c) decomposition by product.

**Stage 4 — Approval / governance:**
- Independent reviewer challenge (typically a senior actuary not involved in the study).
- Assumption change committee sign-off.
- Documentation lodged in a central assumption repository with version, effective date, scope, and impact.

The Lincoln Financial *Life Assumption Governance* role description and PwC's commercial *Experience Study & Assumption Management* platform both confirm this is the industry-standard pattern: experience study → development → impact analysis → review → approval → repository.

### 8. Published Simplified TEV Implementations

The most useful published references for a simplified, prototype-scale TEV are:

- **American Academy of Actuaries, "Embedded Value (EV) Reporting" Practice Note (May 2009)** — the single most useful document; the Q&A format covers exactly the mechanics, assumptions, and disclosures needed.
- **Frasca, R. and LaSorella, K., "Embedded Value: Practice and Theory" (SOA *Actuarial Practice Forum*, March 2009)** — companion paper with worked formulas including analysis-of-movement.
- **Tremblay, F., "Embedded Value Calculation for a Life Insurance Company" (SOA *Actuarial Practice Forum*, October 2006)** — fully worked numerical illustration of a deterministic EV calculation; ideal as a reference for unit-testing your prototype.
- **CFO Forum, "European Embedded Value Principles and Guidance" (April 2016)** — the source for the sensitivity grid.
- **Generali, Old Mutual, etc., annual EEV/MCEV supplementary statements** — useful concrete examples of the sensitivity tables and disclosures.
- **Profectus Academy and "Valuing a Life Insurer" (Actuaries India, 2022)** — simplified worked examples used in teaching.
- **Institut des Actuaires memoir on Prevoir Vietnam TEV (Vu Duc Nguyen)** — a graduate dissertation-level full TEV computation on a real (Vietnamese) company, including CAPM-based RDR derivation.

There is no open-source production-quality "TEV engine" library, but the lifelib **BasicTerm**, **CashValue_ME** (savings), and (forthcoming/related) IFRS17 libraries together implement essentially every projection primitive a TEV engine needs.

### 9. Technology Considerations: Building a Multi-Product Deterministic TEV in Python

**Recommended stack:**

| Layer | Recommended choice | Rationale |
|---|---|---|
| Cash-flow projection engine | Either (a) **lifelib + modelx** for transparent spreadsheet-like models, or (b) **cashflower** for a more conventional def-based framework, or (c) a pure pandas/numpy vectorized engine | All three are MIT/LGPL and production-credible at the prototype scale |
| Life-contingency primitives | **pyliferisk** or **lifeActuary** (single-file, no heavy deps) | Standard actuarial functions (Axn, äxn, qx, lx) for WL reserve calculations |
| Mortality tables | **pymort** (SOA tables) | Direct programmatic access to 2001 CSO, 2017 CSO, VBT, GAM tables |
| Model-point clustering | **scikit-learn KMeans** | The lifelib `cluster` library is a working example |
| Data layer | **pandas DataFrames** for model points; **parquet** for persistence | Idiomatic for actuarial data sizes (1k–100k rows) |
| Assumption store | **Versioned YAML / JSON** files + a small SQLite or DuckDB store | Auditability; ties into the A/E module's output |
| UI / workflow | **Streamlit** or **Dash** for the actuary-facing screens | Rapid prototyping; both integrate cleanly with pandas |
| Performance | **NumPy vectorization** over model points (the lifelib "ME" pattern); **modelx-cython** if speed-critical (~7–8× speed-up on BasicTerm_SC) | Avoid loops over model points |

**Architectural pattern (recommended):**

```
prototype_tev/
├── assumptions/          # versioned YAML, one set per A/E approval cycle
├── data/                 # model points, mortality tables, in-force snapshots
├── engine/
│   ├── projection.py     # vectorized period-by-period projection
│   ├── products/
│   │   ├── term.py       # product-specific BP and reserve logic
│   │   ├── whole_life.py
│   │   ├── ul.py
│   │   ├── vul.py
│   │   └── annuity.py
│   ├── tev.py            # ANW + VIF + PVCoC aggregation
│   └── sensitivities.py  # apply ±10% lapse, ±5% mort, etc.
├── workflow/
│   ├── load_ae_results.py     # consume experience-study outputs
│   ├── propose_assumptions.py # build a new assumption set
│   ├── run_tev.py             # baseline + sensitivity grid
│   └── approval_report.py     # one-page impact summary
└── ui/                    # Streamlit or Dash
```

The lifelib `BasicTerm_M` / `CashValue_ME` pattern (vectorized, all model points at once) gives the right performance profile: 10,000 model points × 100 projection years × 1 baseline + 10 sensitivities runs in seconds on a laptop.

### 10. Integration Between the Experience Study Module and TEV

The cleanest contract between the existing A/E module and the new TEV module is an **assumption-set artifact** — a versioned, validated object with the following shape (illustrative):

```yaml
assumption_set:
  id: 2026Q2_proposed
  effective_date: 2026-06-30
  author: <actuary>
  basis: best-estimate    # no PADs, per Academy 2009 Q28
  source_experience_study: ES_2026_mortality_v3
  
  mortality:
    products: [Term, WL, UL, VUL]
    table_base: 2017_CSO_M_NS    # or company table reference
    select_period: 25
    improvement: G2_2_0
    multipliers:                  # output from the A/E module
      - product: Term
        gender: M
        risk_class: PNT
        duration_band: [1, 10]
        factor: 0.92               # credibility-weighted A/E
  
  lapse:
    products: [Term, WL, UL, VUL, DA]
    base_table: <ref>
    shock_lapse_end_of_level_term:
      level_term_10: 0.50
      level_term_20: 0.65
    multipliers: [...]              # from A/E
  
  premium_persistency:              # UL/VUL/FA
    by_duration: [...]
  
  expenses:
    acquisition_pp: 350
    maintenance_pp: 72
    maintenance_pct_premium: 0.020
    inflation: 0.025
  
  economic:
    rdr: 0.090
    earned_rate_GA: 0.050
    earned_rate_SA: 0.060
    tax_rate: 0.21
    rc_pct_reserve:
      Term: 0.030
      WL: 0.045
      UL: 0.060
      ULSG: 0.080
      VUL: 0.035
      DA: 0.045
  
  sensitivity_grid:
    - lapse_mult_0.90
    - lapse_mult_1.10
    - mortality_life_mult_0.95
    - mortality_ann_mult_1.05
    - maintenance_mult_0.90
    - maintenance_mult_1.10
    - rdr_+100bp
    - rdr_-100bp
    - inflation_+1pct
    - inflation_-1pct
```

**What should flow from A/E → TEV:**
1. **Credibility-weighted decrement multipliers** by the cells used in the experience study (product × age × duration × risk class × gender).
2. **Confidence intervals or credibility factors** for each cell, so the TEV module can decide whether to apply a margin or run a wider sensitivity.
3. **A diff report vs. the prior assumption set** (which assumptions changed, by how much, where the experience data supports the change).
4. **A pointer to the underlying study** (data window, exposure basis, A/E ratios, p-values) for audit.

**What should flow back from TEV → the actuary's review screen:**
1. ΔTEV (baseline new vs. baseline old), broken down by product, by ANW vs. VIF, and by source of profit (mortality margin, lapse margin, expense margin, investment margin).
2. The full sensitivity grid on the proposed assumptions, presented as a tornado chart per product.
3. An "is the new assumption set inside the prior sensitivity envelope?" flag — if yes, the change is small and routine; if no, it warrants additional review.
4. A drafted assumption-change memo (auto-generated from the diff + impact + sensitivities) that the reviewer can edit and sign off.

---

## Caveats

- **The Academy 2009 Practice Note is observation, not regulation.** It does not bind any company, and small variations in ANW definition (gross-up for AVR vs. not, treatment of IMR, mark-to-market on RC-backing assets vs. only on free surplus) are common. A prototype should expose these as toggleable conventions rather than hard-coding them.
- **The CFO Forum sensitivity standards are for *EEV* (European Embedded Value), not strictly TEV.** They have been universally adopted as the de-facto standard for both EEV and TEV reporting, but the original "Achieved Profits Method" (1990s UK) TEV did not specify a fixed sensitivity grid. Using the CFO Forum grid is recommended best practice but not formally mandatory.
- **RBC factor proxies are illustrative only.** The percentage-of-reserve required-capital approximations cited in this report (e.g., "~3% for term") are typical illustrative values from textbook treatments and published case studies, not company-specific or NAIC-prescribed values; a real prototype should calibrate `RC_pct_reserve` for each product to reproduce the company's actual blended RBC ratio at t=0.
- **Discount-rate ranges are time-sensitive.** The 7.0%–9.0% RDR range cited in the 2009 Academy note reflected the late-2000s interest-rate environment; current (2025–26) levels should be re-derived from current Treasury yields, the company's β, and an ERP assumption. Treat the RDR as the most important single user-editable input.
- **Stochastic options-and-guarantees (TVOG) is deliberately out of scope.** TEV (as distinct from EEV and MCEV) does not require it. For products with material guarantees (ULSG no-lapse, VA GMxB, FIA index credits), this is a real limitation: the prototype will *under*state the cost of those guarantees relative to a full EEV/MCEV. This should be documented as a known prototype simplification rather than a defect.
- **Model-point compression to ~1,000 points works for deterministic single-scenario TEV** but is too coarse for stochastic O&G valuation; the prototype scope must remain deterministic for these compression ratios to be safe.
- **lifelib, cashflower, and pyliferisk are open-source community projects, not production-supported software.** Using them in a prototype is appropriate; using them in production requires the same model-governance discipline (ASOP guidance on model validation, change management, peer review) as any other actuarial model.
- **The exact composition of the sensitivity grid varies by company and by purpose.** The eleven-test grid suggested in §6 is a recommended starting point covering the CFO Forum minimum plus the symmetric counterparts most useful for decrement-assumption review; companies typically add product-specific tests (e.g., shock-lapse at end of level term for Term/ULSG; partial-withdrawal frequency for VAs).
- **Some figures in this report (e.g., compression ratios of 100:1–500:1) come from vendor marketing material** (Oliver Wyman Fulcrum); these are claimed achievable ratios under stochastic financial-reporting workloads, not independently audited benchmarks. Treat them as indicative of the upper end of what is achievable, not as guarantees.