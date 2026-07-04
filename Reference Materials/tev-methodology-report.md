# Traditional Embedded Value (TEV) Methodology for Life Insurance

## Executive summary

Traditional Embedded Value (TEV) is a discounted‑cash‑flow framework used by life insurers to measure the value of existing business to shareholders, defined as adjusted net worth plus the value of in‑force business. It projects best‑estimate future distributable profits from in‑force policies, deducts the cost of holding required capital, and adds this to a suitably measured shareholder net asset value, using a risk discount rate that reflects shareholders’ required return.[^1][^2][^3][^4]

This report explains how TEV is defined and decomposed, how ANW and VIF are constructed, how the core actuarial assumptions are selected and fed from experience studies, how discount rates and capital costs are allowed for, how financial options and guarantees are handled in a traditional deterministic setting, and how TEV movements are analyzed over time. The focus is on practical, model‑oriented descriptions aligned with commonly used Asian and European TEV practices, as reflected in professional presentations and real company EV reports.[^3][^4][^1]

## 1. Concept and purpose of TEV

### 1.1 Definition and high‑level formula

Embedded value (EV) is a shareholder‑centric valuation measure for life insurers equal to the present value of future profits from existing business plus an adjusted measure of shareholder net assets. In algebraic form, EV is usually written as:[^5][^2][^4]

\[ EV = ANAV + PVFP \]

or, in more granular TEV terminology,

\[ EV = ANW + VIF,\quad VIF = PVFP - CoC. \]

Here ANW (or ANAV) is adjusted net worth, VIF is the value of in‑force business, PVFP is the present value of future profits, and CoC is the cost of capital. TEV generally excludes goodwill and the value of future new business so is often viewed as a conservative measure relative to full appraisal value, which adds a multiple of future new business value.[^2][^6][^4][^1][^3]

### 1.2 TEV versus MCEV/EEV and other EV variants

Traditional EV uses a deterministic discounted‑cash‑flow method with a risk discount rate that implicitly allows for non‑hedgeable risks, options, guarantees, and capital costs, whereas Market Consistent Embedded Value (MCEV) and European Embedded Value (EEV) aim to value cash flows using market‑consistent techniques. Under TEV, all risk premiums and many risks are “pushed into” the discount rate and cost‑of‑capital charges, while MCEV uses risk‑neutral scenarios, explicit time‑value‑of‑options‑and‑guarantees (TVFOG) and frictional cost of capital calculations. TEV remains widely used in Asia and emerging markets due to its relative simplicity and because it can be implemented using deterministic projection engines already built for pricing and statutory valuation.[^6][^7][^8][^4][^3]

EV, and by extension TEV, is primarily used for management reporting, investor communication and capital markets valuation metrics (such as Price‑to‑TEV and value of new business multiples) rather than being a regulatory requirement. Analysts and investors use EV and new business value to gauge the long‑term profitability and value creation of life insurers, while boards and management use EV movements and sensitivities to understand drivers of performance and the impact of assumption changes.[^9][^10][^11][^12]

## 2. Core components: ANW and VIF

### 2.1 Adjusted Net Worth (ANW)

Adjusted Net Worth is essentially shareholder net assets measured on a chosen basis, often statutory or regulatory, with adjustments to reflect market values and alignment with the EV projection assumptions. A common definition is:[^4][^1][^3]

\[ ANW = \text{Shareholders' assets on chosen basis} + \text{market value adjustments} - \text{statutory liabilities} \]

For example, an Asian insurer defined ANW as statutory assets plus a market‑value adjustment minus statutory liabilities, effectively capturing the excess of market value over book value for certain investments. Professional guidance emphasizes splitting ANW into required capital (RC) needed to support solvency requirements and free surplus (FS) that can be distributed without breaching solvency constraints, so that ANW = FS + RC.[^1][^6][^4]

### 2.2 Value of In‑Force (VIF) and PVFP

The value of in‑force business is the discounted value of projected future distributable earnings from business in force at the valuation date, net of the cost of holding required capital. A common relationship is:[^3][^4][^1]

\[ VIF = PVFP - CoC \]

where PVFP is the present value of future statutory profits after tax available for distribution to shareholders and CoC is the cost of holding required capital to support the in‑force business. Company disclosures show PVFP being calculated from projected premiums, investment income, benefits (claims, surrenders, maturities), expenses (including commission), changes in statutory reserves, tax, and the cost of required capital, all discounted at the selected risk discount rate.[^4][^1][^3]

### 2.3 Cost of Capital (CoC)

In TEV, CoC usually represents the opportunity cost of having to hold required solvency capital instead of distributing it immediately, reflecting the difference between the shareholders’ target return (RDR) and the expected after‑tax return on capital assets. One standard formulation calculates CoC as the present value of the difference between the shareholders’ required return on capital and the actual investment income plus release of capital as it runs off. Some insurers simplify CoC as a fixed percentage per year of statutory reserves or required capital, such as 4 percent of statutory reserves, with that amount taken as a deduction from distributable profits each year.[^1][^3][^4]

## 3. TEV cash‑flow modelling

### 3.1 Projection scope and unit of account

TEV projections are generally performed at the policy or model‑point level for all in‑force contracts at the valuation date, including riders and options, grouped into homogeneous model points where appropriate to manage run‑time. In‑force business includes all policies already issued (including paid‑up and reduced paid‑up contracts) and often includes certain reinstatement‑eligible lapsed policies if they retain material value. Unit‑linked, participating, traditional protection, and savings/annuity products are each modelled with their specific cash‑flow structures, bonus or crediting rules, and capital requirements.[^13][^3][^4]

### 3.2 Projected cash flows and distributable earnings

For each product line, the model projects annual or finer‑granularity cash flows including premium income, investment income, benefit payments (death, surrender, maturity, annuity payments), expenses, commissions, taxes, increases in statutory reserves, and capital movements. These are aggregated into projected statutory profits and then into distributable profits to shareholders after allowing for required capital and tax on investment gains.[^3][^1]

A typical TEV definition of distributable earnings per period is:

- statutory profit after tax, plus
- release of required capital and non‑distributable reserves, minus
- increases in required capital, minus
- explicit cost‑of‑capital charge if defined separately.[^4][^3]

Distributable profits are then discounted back to the valuation date using the risk discount rate to obtain PVFP, which feeds into VIF and hence EV.[^3][^4]

### 3.3 Deterministic projection and treatment of risk

Traditional EV uses deterministic projections with a single set of best‑estimate non‑economic assumptions (mortality, lapse, expenses etc.) combined with a chosen path of investment returns consistent with the discount rate and the company’s asset mix. The method is described as implicitly allowing for policyholder options, investment guarantees, asset‑liability mismatch risk, credit risk, and other risks through a risk‑adjusted discount rate and cost‑of‑capital charges, rather than explicitly valuing each risk component separately.[^7][^4][^3]

The deterministic method is computationally simpler and closely aligned to existing actuarial projection tools, but it is less theoretically rigorous for products with strong option‑like features or substantial financial risks than approaches based on stochastic market‑consistent techniques.[^8][^6][^7]

## 4. Assumptions in TEV modelling

### 4.1 General principles and experience‑study linkage

Best‑estimate assumptions in TEV are typically based on the insurer’s own credible experience studies, adjusted where necessary for expected future trends and external benchmarks. EV guidance and company practice emphasize that assumptions should be internally consistent across EV, statutory reserving, and pricing frameworks and regularly updated to reflect emerging experience.[^1][^4][^3]

In practice, mortality, morbidity, lapse, expense, and other non‑economic assumptions are usually derived from recent multi‑year experience studies, often segmented by product, underwriting class, distribution channel, and duration, with management judgment applied for trend adjustments and credibility. Economic assumptions (investment returns, discount rates, inflation, and tax rates) must be coherent with one another and with the asset mix used in the projection and ANW measurement.[^4][^1][^3]

### 4.2 Mortality and morbidity

TEV mortality and morbidity assumptions are often expressed as percentage loadings to a standard table or pricing table, calibrated using the company’s actual experience and expectations for future improvement or deterioration. For example, one EV disclosure assumed mortality at 30 percent of a standard table for certain non‑annuitant portfolios, and morbidity based on internal pricing tables supplemented with experience data where available.[^1][^3]

For protection and annuity business, mortality and morbidity assumptions are key drivers of PVFP and sensitivities so EV reports usually include sensitivities to plus or minus 10 percent changes in mortality or morbidity rates. Assumptions may vary by product, currency, channel, underwriting class, and duration, and are usually net of expected recoveries under reinsurance arrangements.[^13][^3]

### 4.3 Persistency (lapse, surrender, paid‑up, withdrawals)

Persistency assumptions in TEV are generally set by product and duration, with differentiation by distribution channel and policy type, and in some cases by age or policy size. Company reports describe discontinuance assumptions being based on recent historical lapse and surrender studies, with adjustments when experience for a particular product is not yet credible, in which case pricing assumptions are used as a starting point.[^3][^4]

Given the high sensitivity of VIF and VONB to persistency, EV disclosures routinely include sensitivities to plus or minus 10 percent changes in discontinuance rates. Persistency assumptions are also required to be internally consistent with the treatment of reinstatement‑eligible policies and paid‑up conversions, ensuring that the in‑force portfolio and corresponding cash flows are accurately captured.[^13][^4][^3]

### 4.4 Expenses and inflation

Maintenance expense assumptions are typically derived from an analysis of the most recent financial year’s operating expenses, allocated to product lines and per‑policy or per‑premium metrics, with projected expense levels inflated at a chosen long‑term inflation rate. TEV practice often assumes no future efficiency gains beyond those already realized; EV manuals emphasize that no allowance should be made for speculative future expense savings unless there is a concrete plan and strong evidence.[^1][^3]

Expense overruns for growing companies or new distribution channels need to be captured explicitly, usually as additional per‑policy expense loadings that run off over a management‑specified horizon. Sensitivity tests of plus or minus 10 percent expenses are commonly disclosed to show the impact on VIF and VONB.[^4][^3]

### 4.5 Investment returns and crediting/bonus strategies

Investment return assumptions for TEV are usually based on observed market yields as at the valuation date for VIF and average yields over the year for new business value, applied to an assumed asset mix that reflects the company’s investment policy and actual portfolio. EV disclosures show separate net investment return assumptions by asset class and currency (for example, different returns for rupiah and USD portfolios, and for traditional, stable‑link, and unit‑link business).[^3][^1]

Crediting rate or bonus assumptions for participating or savings business are then set consistently with the investment return assumptions and with the company’s declared bonus/crediting strategy. For example, some Asian EV reports set target crediting rates on flagship savings products slightly below assumed investment returns, reflecting the spread required to cover expenses, risks, and profit margins.[^13][^3]

### 4.6 Tax and reinsurance

Corporate tax assumptions in TEV reflect current tax legislation and rates, with EV reports often specifying statutory tax rates and expected changes over time. Tax is applied to both underwriting profits and investment income, with allowance for any specific tax treatments of policyholder funds and capital gains.[^1][^3]

Reinsurance is incorporated by projecting ceded premiums, reinsurance commissions, and recoveries under existing treaties, using assumptions consistent with the company’s pricing and reserving models. The valuation usually assumes continuation of current reinsurance arrangements, with any planned future changes disclosed qualitatively.[^3]

## 5. Risk discount rate (RDR) and required return

### 5.1 Role and conceptual basis

The risk discount rate in TEV represents the return required by shareholders for bearing the risks of the in‑force business and is used to discount future distributable profits to present value. It is often conceptualized as the sum of a risk‑free rate (to reflect the time value of money) and a risk margin that reflects the business’s risk profile.[^4][^3]

Professional and academic sources note that common approaches for estimating the RDR include building it up from a weighted average cost of capital (WACC) or using the Capital Asset Pricing Model (CAPM), where the RDR is expressed as the risk‑free rate plus a beta times the market risk premium. In practice, companies often disclose EV results under a range of risk discount rates for sensitivity analysis and to facilitate investor judgment on appropriate risk margins.[^7][^4][^1][^3]

### 5.2 Practical determination of RDR

In disclosures, risk discount rates often vary by currency and sometimes by product type, recognizing different risk profiles and capital market conditions. For example, an insurer might use higher discount rates for local currency business than for USD business in an emerging market, reflecting higher local risk‑free yields and risk premiums.[^1][^3]

Companies typically set a central RDR (for example, 10 percent, 13 percent, or 15 percent) and disclose EV and VONB at one or more alternative rates (for example, ±2 percentage points) to illustrate sensitivity. Adjustments to the RDR over time often reflect changes in risk‑free yields, capital structure, or perceived business risk, and EV guidance stresses the need for coherent rationale and disclosure when discount rates are changed.[^7][^4][^3][^1]

### 5.3 Relationship with CoC and capital structure

There is an interaction between RDR and the explicit cost‑of‑capital charge in TEV, since both are mechanisms for reflecting risk and capital costs. If CoC is defined as the spread between RDR and the investment return on required capital, raising the RDR both lowers PVFP and increases the CoC component, amplifying the sensitivity of VIF to discount rates.[^4][^3]

Some professional discussions caution about the risk of double‑counting if capital costs are reflected both in the discount rate and in additional CoC deductions or conservative assumptions elsewhere in the model. TEV frameworks therefore emphasize internal consistency between the definition of RDR, CoC, and the capital requirement measure (for example, regulatory solvency capital versus internal economic capital).[^7][^4]

## 6. Treatment of options, guarantees, and TVFOG

### 6.1 TEV approximation of option and guarantee costs

Traditional EV methodology was designed before the widespread adoption of market‑consistent techniques; it approximates the cost of options and guarantees through conservative assumptions and discount rates rather than explicit option pricing models. Presentations on TEV explain that the deterministic method makes implicit allowance for policyholder options, investment guarantees, asset‑liability mismatch risk, credit risk, and other risks through risk‑adjusted discount rates and capital costs.[^3][^4]

This approximation is reasonable for symmetric guarantees and moderate risk profiles but can be inaccurate for products with significant asymmetric payoffs, such as minimum return guarantees, guaranteed conversions, or variable bonuses highly sensitive to markets. Consequently, MCEV and EEV frameworks mandate explicit valuation of the time value of options and guarantees (TVFOG), often via stochastic simulations or option‑pricing techniques.[^6][^8][^7][^4]

### 6.2 TVFOG concept and stochastic comparison examples

The time value of options and guarantees (TVFOG) can be conceptualized as the difference between the average EV across many economic scenarios and the EV calculated under a single “average” scenario, keeping all non‑economic assumptions constant. TEV teaching material illustrates this by projecting PVFP under different investment return scenarios; if the guarantee is symmetric, the average EV across scenarios equals the EV under the average return scenario, implying negligible TVFOG.[^4]

However, with asymmetric guarantees (for example, a product that reduces survival benefits if returns are low but not correspondingly increases them if returns are high), the average EV across scenarios exceeds the EV under the average return scenario, with the difference interpreted as TVFOG. TEV frameworks allow for TVFOG either by a crude deterministic adjustment (for example, using modified discount rates) or, more robustly, by adding a separate stochastic or option‑pricing component when products are materially exposed to such risks.[^6][^7][^4]

## 7. TEV movement analysis over time

### 7.1 Purpose of movement analysis

An EV movement analysis reconciles opening and closing EV over a reporting period, decomposing the change into contributions from new business, unwinding of discount, experience variances, assumption changes, economic variances, and other capital movements. It provides management and investors with insight into how value is created or destroyed and whether performance is driven by underlying operations, assumption changes, or external market factors.[^12][^9][^4]

Standard EV charts show EV at the start of the period, adjusted for model or scope changes, plus the expected unwinding of the discount on the opening VIF, plus the value of new business written during the year, plus operating experience variances and assumption changes, plus economic variances, and finally closing adjustments leading to EV at the end of the period.[^12][^4]

### 7.2 Typical movement components

Common movement categories include:

- Opening adjustments: restatements, methodology changes, or corrections to opening EV.[^4]
- Expected existing business contribution: the unwinding of the discount on opening VIF and the release of risk margins, often viewed as the “expected EV earnings” from in‑force business.[^12][^4]
- New business contribution: the value of one year’s new business (VNB or VONB) written during the period.[^12][^3]
- Operating experience variances: differences between actual and expected mortality, lapses, expenses, and other non‑economic factors.[^12][^4]
- Operating assumption changes: updates to mortality, lapse, expense, or other assumptions based on new experience studies or management views.[^3][^4]
- Economic variances: impacts of differences between actual and assumed interest rates, equity returns, inflation, and credit spreads, including impacts on ANW, reserves, and VIF.[^13][^4]
- Closing adjustments: model refinements, changes in taxation or regulation, or adjustments related to acquisitions or capital transactions.[^12][^3]

Movement analysis is often summarized separately for ANW and VIF, and further split between operating and non‑operating (economic) items for transparency.[^9][^4]

## 8. Disclosures, sensitivities, and governance

### 8.1 Required and common disclosures

Although TEV is not mandated by regulators, several professional bodies and market practices encourage extensive disclosures on methodology, assumptions, and sensitivities to ensure transparency. TEV and EV reports typically describe the definitions of EV, ANW, VIF, and VNB; the projection methodology; the key economic and non‑economic assumptions; the basis for capital requirements; and any material limitations.[^10][^8][^3][^4]

Many EV reports also state the scope of business covered (for example, which subsidiaries, currencies, and products), materiality thresholds for exclusions, and the extent of reliance on management data and internal controls. External actuarial consultants are often engaged to review or calculate EV and provide comfort to boards and investors, with reports explicitly stating reliances, limitations, and that results are not an opinion of market value.[^13][^3]

### 8.2 Sensitivity analysis

EV reports routinely include sensitivity tables showing the impact on VIF, VNB, and sometimes ANW of changes in key assumptions such as investment returns, crediting rates, mortality/morbidity, lapses, expenses, and discount rates. For example, some reports show VIF and VNB under scenarios of plus or minus 0.5 percent net investment return, plus or minus 0.5 percent crediting rates, plus or minus 10 percent discontinuance rates, plus or minus 10 percent mortality/morbidity, and plus or minus 10 percent operating expenses.[^13][^3]

Sensitivities are presented both before and after cost of solvency capital, highlighting how capital requirements amplify the impact of adverse experience on shareholder value. Explicit discount‑rate sensitivities also help investors assess the impact of changes in required returns or changes in risk‑free yields on EV measures.[^13][^1][^3]

### 8.3 Governance and assumption management

Effective TEV practice requires robust governance around assumption setting, model changes, and EV reporting, often mirroring the governance applied to regulatory capital and IFRS reporting. Boards or specialized committees typically approve key EV assumptions, discount rates, and methodologies, and oversee periodic reviews and independent validations.[^10][^8][^9][^3]

There is documented concern in the investment community that EV and TEV measures can be inflated by overly aggressive long‑term economic or actuarial assumptions, leading to skepticism around metrics such as Price‑to‑TEV when applied without understanding assumption quality. This has led to calls for stronger disclosure, improved alignment with market‑consistent frameworks, and independent review of EV methodologies and assumptions.[^11][^10][^7]

## 9. TEV in relation to other reporting bases

### 9.1 TEV versus solvency and regulatory balance sheets

TEV is an internal and market‑communication measure and is distinct from regulatory solvency balance sheets such as Solvency II or local risk‑based capital regimes. Regulatory balance sheets typically focus on policyholder protection and minimum capital requirements, while TEV targets shareholder value from existing business.[^10][^7]

Nonetheless, there are synergies: embedded value frameworks often use capital requirements and sometimes methodologies consistent with market‑consistent solvency regimes, and guidance from the European Insurance CFO Forum explicitly allows alignment of MCEV and EEV assumptions with Solvency II where beneficial. Many companies therefore seek consistency between TEV capital measures and regulatory capital metrics to avoid conflicting signals to stakeholders.[^10][^7]

### 9.2 TEV versus IFRS profit measures

TEV can be seen as a long‑term discounted‑cash‑flow measure akin to the present value of expected shareholder profits, whereas IFRS profit measures (including under IFRS 17) reflect periodic performance and contractual service margin release. Academic and professional discussion notes that TEV and similar actuarial metrics already embed granular discounted cash‑flow projections, leading some to view traditional DCF models from external analysts as redundant relative to TEV and IFRS comprehensive equity measures when available.[^14][^11][^7]

From an embedded value perspective, assumption changes and experience variances that affect IFRS profit recognition (through changes in reserves or contractual service margins) will also affect projected distributable profits and thus VIF and EV. Aligning TEV and IFRS 17 assumption governance can therefore improve coherence across internal and external performance metrics.[^14][^10][^12]

## 10. Limitations and evolution of TEV

### 10.1 Methodological limitations

Professional summaries of TEV highlight limitations including subjective allowances for risk via discount rates, difficulty in capturing product portfolio and asset‑mix nuances, and challenges in consistently treating options and guarantees and asymmetric risks. Since TEV pushes many risk allowances into the RDR and CoC, results can be sensitive to subjective judgment and may not fully reflect the economic cost of financial options and guarantees.[^6][^7][^4]

Other limitations include reliance on deterministic projections for inherently stochastic risks, potential double‑counting of risk margins across assumptions, discount rates, and capital costs, and lack of consistency with market prices for hedging instruments or comparable financial liabilities. These limitations motivated the development of MCEV and other fair‑value oriented frameworks that aim to value cash flows in a manner more closely aligned with financial economics.[^8][^6][^7]

### 10.2 Current practice and convergence trends

Embedded value remains widely used in Europe and Asia, with many insurers disclosing MCEV or hybrid TEV/MCEV measures alongside or integrated with solvency and IFRS reporting. Industry associations such as the CFO Forum have updated embedded value principles to allow use of Solvency II methods and assumptions in MCEV and EEV, encouraging convergence between EV and regulatory economic balance sheets.[^15][^10][^7]

At the same time, increasing focus on assumption quality, disclosure, and governance around TEV is evident in empirical research linking high‑quality EV reporting to lower credit risk and better investor perceptions. As IFRS 17 and market‑consistent solvency regimes mature, many insurers are reevaluating how TEV and related measures fit into their overall performance and value‑reporting architecture.[^9][^14][^7][^12]

---

## References

1. [[PDF] Embedded value (EV)](https://www.bangkoklife.com/Upload/InvestorFile/3ec8f059606f46ec8888f198ea9252ba.pdf)

2. [Embedded value - Wikipedia](https://en.wikipedia.org/wiki/Embedded_value)

3. [Offices in Principal Cities Worldwide](http://www.sinarmasmultiartha.com/phocadownload/Siamese%20EV%20Report%20for%20Website_3%20January%202012_v2.pdf)

4. [Traditional Embedded Value](https://www.actuariesindia.org/sites/default/files/inline-files/Traditional_Embedded_Value_0.pdf)

5. [Embedded Value (EV): Definition, Calculation, and Example](https://www.investopedia.com/terms/e/embeddedvalue.asp) - Embedded value is a common valuation measure in the life insurance industry used to estimate the con...

6. [Microsoft PowerPoint - Wagner.ppt](https://www.actuaries.org.uk/system/files/documents/pdf/europeanembeddedvaluebottomsuphandout.pdf)

7. [Market Consistent Embedded Values as "Fair ...](https://www.casact.org/abstract/market-consistent-embedded-values-fair-value-measurements-life-insurance-accounting-step)

8. [[PDF] Market Consistent Embedded Values](https://www.actuary.org/files/MCEV%20Practice%20Note%20Final%20WEB%20031611.4.pdf/MCEV%20Practice%20Note%20Final%20WEB%20031611.4.pdf) - A: In its June 2008 paper, Market Consistent Embedded Value Principles, the CFO. Forum, describes th...

9. [Embedded value reporting quality and credit risk: evidence from life insurance companies](https://www.tandfonline.com/doi/full/10.1080/00014788.2020.1749979) - This study investigates the effects of releasing embedded value (EV) reports and EV report disclosur...

10. [Embedded value (MCEV/EEV Principles & Guidance) - CFO Forum](https://www.cfoforum.eu/publications/embedded-value) - Embedded Value is a way of reporting the value of the life insurance business companies have with th...

11. [Valuation of Insurance Stocks: A Discussion | Richard CHAN Long ...](https://www.linkedin.com/posts/richard-chanlongfai_%F0%9D%97%A9%F0%9D%97%AE%F0%9D%97%B9%F0%9D%98%82%F0%9D%97%AE%F0%9D%98%81%F0%9D%97%B6%F0%9D%97%BC%F0%9D%97%BB-%F0%9D%97%BC%F0%9D%97%B3-%F0%9D%97%9C%F0%9D%97%BB%F0%9D%98%80%F0%9D%98%82%F0%9D%97%BF%F0%9D%97%AE%F0%9D%97%BB%F0%9D%97%B0%F0%9D%97%B2-activity-7371188872002785280-jL84) - ➡️ P-to-TEV? The problem lies in the quality of EV, which can be inflated by aggressive long term ec...

12. [Understanding Embedded Value Reports in Life Insurance ...](https://ppnsolutions.com/blog/embedded-value-reports-in-life-insurance/) - Gain insights into how Embedded Value reports help assess profitability, performance, and long-term ...

13. [[PDF] Supplementary Information on the Life & Health Embedded Value ...](https://group.vig/media/ajfovxge/2022_vig_supplementary_information_on_the_life___health_embedded_value.pdf) - The Life and Health Embedded Value comprises the Market Consistent Embedded Values (“MCEV”) of the m...

14. [IFRS 17 Actuarial Assumptions Impact on Insurance Profits](https://primaconsulting.org/ifrs-17-actuarial-assumptions/) - Learn how IFRS 17 actuarial assumptions affect insurance profits through CSM, discount rates, and ri...

15. [[PDF] Disclosure of Market Consistent Embedded Value as at March 31 ...](https://www.sompo-hd.com/-/media/hd/en/files/news/2025/e_20250520_hl.pdf?la=ja-JP) - The MCEV Principles were amended in May 2016 and now include guidance that allows the use of EU Solv...

