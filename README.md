# One Health Early Warning — north-west India

District-level forecasting of human disease outbreak risk, joining human
surveillance, climate, livestock population and animal disease data into one
weekly panel and one map.

**[Open the live dashboard →](https://onehealth-early-warning.vercel.app)**

**Hack4Pashudhan · Track 5 (One Health Dashboard) · PGIVER Jaipur**
Lakkarsu Nanditha · Ch. Vignesh Reddy

---

## The problem

India runs good disease surveillance, in three systems that do not talk to each
other:

| System | Watches | Blind to |
|---|---|---|
| NADRES / NADRS (ICAR-NIVEDI) | Livestock outbreaks, district, monthly | Whether people later fell ill |
| IDSP / IHIP (MoHFW) | Human outbreaks, district, weekly | What the local animals were doing |
| IMD / satellite | Rainfall, temperature, humidity | Linked to neither disease feed |

This project builds the join: one district × week table carrying all three, a
forecast of human outbreak risk four weeks ahead, and a map a district officer
can open.

## Headline result

Of the district-weeks flagged as highest risk (top 5%), **8.4% saw a reported
outbreak within four weeks, against a background rate of 1.6%** — a **5.4×
lift, 95% CI 4.6–6.4**, on a test period the model never saw in training.

PR-AUC 0.068 ± 0.0015 across five seeds; hit rate 8.4% ± 0.17%. Measured, not
asserted: see `src/evaluate.py` and `data/interim/stability.json`.

**It transfers to states it has never seen.** Trained on eight states and asked
to predict the ninth — contributing no district and no history to training — it
averages **4.7× lift** (range 3.6–6.7 across six states with enough test cases).
Transfer costs about 13% of the lift. `src/validation.py`.

**The operating trade-off, stated plainly.** Inspecting the riskiest 5% of
district-weeks catches **27.7% of all outbreaks** — random inspection at that
effort would catch 5%. The cost is ~10.6 false alarms per catch, and 342
outbreaks went unflagged. At a 1.6% background rate, random inspection would
cost roughly 60 false alarms per catch.

**5% was a choice; here is the evidence for it.** Lift decays steeply with
inspection effort, so the threshold is a budget decision, not a tuning
parameter:

| Inspect | Outbreaks caught | Lift | False alarms per catch |
|---|---|---|---|
| 1% | 9.5% | 9.5× | 5.8 |
| 2% | 16.5% | 8.2× | 6.8 |
| **5%** | **27.7%** | **5.5×** | **10.6** |
| 10% | 37.2% | 3.7× | 16.3 |
| 15% | 45.7% | 3.0× | 20.2 |
| 20% | 51.8% | 2.6× | 23.8 |
| 30% | 65.8% | 2.2× | 28.3 |

5% is where lift is still strong and the waste is still defensible. A district
with spare capacity should run at 10%: it catches half again as many outbreaks
for 50% more waste per catch. Below 2% the ranking is sharp but the coverage is
too thin to be worth a standing process.

**The blind spot, named.** `own_8w` is the strongest feature, so the model was
expected to catch recurrences and miss first appearances. It does, starkly. Of
473 test outbreaks:

| | Outbreaks | Caught in advance |
|---|---|---|
| Something active nearby or recently here | 218 | **57.8%** |
| Quiet beforehand (nothing nearby, nothing recent) | 255 | **2.0%** |
| Monsoon weeks | 241 | 34.4% |
| Dry-season weeks | 232 | 20.7% |

A ~29× gap. **This is a tool for allocating follow-up where something is already
stirring, not for detecting emergence.** Half of all outbreaks arrive with no
warning signal in the data, and for those nothing replaces a report from the
ground. Stating this is more useful to a health officer than the headline lift.

## What we found, including what failed

**Outbreak risk is strongly seasonal.** Six times higher in July than January
(19 vs 3 per 1,000 district-weeks). Monthly rainfall and monthly outbreak rate
correlate at 0.908 — co-movement, since both are seasonal, not causation.

**Unusual weather does not predict outbreaks.** Raw rainfall anomalies appeared
to give a 1.96× risk ratio. That was seasonal leakage: rainfall deviates by tens
of millimetres in July and fractions in January, so every "extreme" week was a
monsoon week. After standardising anomalies per district-week — extreme weeks
then spread across all 12 months — the effect vanishes (1.04×). Weather
anomalies were dropped from the model.

**District identity is not needed.** Removing the district's own historical
outbreak rate slightly *improves* the model (8.6% vs 8.4% hit rate), so the
forecast is not merely learning which districts report well, and it transfers to
districts never seen in training.

**The animal layer does not predict these diseases.** Adding livestock
population, FMD vaccination reach and FMD seroprevalence changes PR-AUC by
+0.0013 on average, with the sign flipping across seeds (sd 0.0048) and
run-to-run variance more than doubling. It is excluded from the production model
and retained in the pipeline and dashboard. This is expected: the human diseases
in the open data (diarrhoeal disease, dengue, malaria, cholera) are not
livestock-transmitted.

**Splitting by disease makes it worse.** Dengue, diarrhoeal disease and cholera
have different mechanisms, so separate models should in principle be sharper.
They are not: diarrhoeal 4.1×, dengue 4.6×, cholera 5.3× against the combined
5.4×, because each model loses most of its training positives. Chikungunya and
malaria have too few test cases to measure at all. The combined target stays.

**Spread does not reach past the immediate border.** Adding a second ring of
neighbours (neighbours-of-neighbours, median 12 districts) changes PR-AUC by
amounts that flip sign across seeds. One hop only.

**Four weeks is the right horizon, measured.** Lift by forecast horizon:
1 week 6.2× (± 0.5, only 134 test cases), 2 weeks 5.0×, **4 weeks 5.5×**,
8 weeks 4.3×. Four weeks is the best of the stable options and gives enough
lead time to act on.

**Animal disease burden is falling.** FMD seroprevalence in Rajasthan fell from
37.4% (2013) to 8.9% (2022) as national vaccination scaled; the nine-state range
is 2.1%–60.5%.

### Four alternatives tested and rejected

Every one of these was built, measured and dropped on the evidence rather than
assumed away: weather anomalies, the animal layer, per-disease models, and
two-hop spatial spread. The production model is what survived.

## Data

All sources are public, free and open. No fieldwork, no hardware, no licences.

| Source | Gives | Resolution | Access |
|---|---|---|---|
| [EpiClim](https://zenodo.org/records/14580510) (from IDSP) | Human outbreak records | District, weekly | Zenodo, CC BY 4.0 |
| [NASA POWER](https://power.larc.nasa.gov) | Temperature, rainfall, humidity | District, daily | Free API, no key |
| [20th Livestock Census 2019](https://publicdata.artpark.ai/en/latest/datasets/0041.html) | Cattle, buffalo, sheep, goat, pig | District | Open CSV |
| [FMD vaccination, NADCP](https://publicdata.artpark.ai/en/latest/datasets/0086.html) | Doses and farmers, rounds 1–6 | District | Open CSV |
| [FMD serosurveillance](https://publicdata.artpark.ai/en/latest/datasets/0089.html) | % bovines testing positive | State, yearly | Open CSV |
| [India district boundaries](https://github.com/datta07/INDIAN-SHAPEFILES) | Polygons and the adjacency graph | District | Open GeoJSON |

EpiClim *is* the IDSP data: it is the Integrated Disease Surveillance Programme's
weekly outbreak reports, extracted from the ministry's PDFs and published as one
clean CSV. We first tried to scrape the IDSP portal directly; it was unreachable,
and EpiClim turned out to be the same source already parsed and quality-checked.

**Scope:** 295 districts across Rajasthan, Gujarat, Madhya Pradesh, Uttar
Pradesh, Haryana, Punjab, Delhi, Himachal Pradesh and Uttarakhand.
2015–2022, ISO weeks. **123,015 district-weeks.**

## Reproducing

```bash
pip install -r requirements.txt

cd src
python skeleton.py        # district x week grid            (no network)
python fetch_climate.py   # NASA POWER -> weekly climate    (~10 min, cached)
python fetch_animal.py    # FMD vaccination + seroprevalence
python build_map.py <INDIA_DISTRICTS.geojson>   # map + adjacency
python join.py            # -> data/interim/panel.csv
python lag_analysis.py    # the weather-anomaly null result
python model.py           # trains, compares baselines, writes predictions.json
python evaluate.py        # seed stability + bootstrap CIs
python per_disease.py     # one model per disease (rejected: worse than combined)
python ablations.py       # horizon sweep + two-hop spread (two-hop rejected)
python validation.py      # leave-one-state-out transfer, confusion matrix, real catches
```

EpiClim must be downloaded once to `data/raw/epiclim.csv`:

```bash
curl -L -o data/raw/epiclim.csv \
  "https://zenodo.org/records/14580510/files/Final_data.csv?download=1"
```

Then the dashboard:

```bash
python -m http.server 8000     # from the project root
# open http://localhost:8000/dashboard/
```

Every script runs an assert-based self-check before writing anything.

## How it is built

```
public sources ──> Python pipeline ──> static JSON ──> Leaflet dashboard
                   (weekly, scheduled)
```

**There is no application server.** The data changes weekly; serving it live
would add a deployment that can fail and buys nothing. The pipeline writes
static files and the map reads them, so the whole thing hosts for free and
cannot fall over mid-demo.

| Layer | Choice | Why |
|---|---|---|
| Processing | pandas, shapely | District polygons, adjacency, panel joins |
| Model | scikit-learn `HistGradientBoostingClassifier` | Strong on small tabular data, handles NaN natively, explains itself |
| Dashboard | Leaflet + OpenStreetMap | No API key, static hosting |
| Refresh | GitHub Actions, weekly | Free, and the data updates no faster |

## Method notes

- **Time-based split**, never random: train 2015–2019, test 2021–2022. A random
  split lets the model see the future and reports a meaningless score.
- **2020 excluded.** Reported outbreaks fell from 127 to 14 that year; COVID
  diverted surveillance, disease did not vanish.
- **PR-AUC and hit rate, not accuracy.** Outbreaks occur in 1.6% of
  district-weeks; a model predicting "never" scores 98% accuracy and is useless.
- **History windows end at week _t_** and the target covers _t+1_ to _t+4_, so no
  feature uses information unavailable at forecast time.
- **66 district-name aliases reconciled by hand.** Gurgaon→Gurugram,
  Allahabad→Prayagraj, Mewat→Nuh, Hoshangabad→Narmadapuram. Fuzzy matching
  proposed Rae-Bareilly→Bareilly — two different districts 200 km apart — which
  is why every alias was checked rather than trusted.
- **Keys are (state, district).** Pratapgarh exists in both Rajasthan and Uttar
  Pradesh.

## Limitations

- **No zoonotic link is demonstrated.** The open human data covers diseases that
  are not livestock-transmitted. Brucellosis and anthrax are reported too rarely
  in open sources to model at district-week resolution.
- **Reported, not observed.** Surveillance records outbreaks that were
  *reported*; a better-staffed district reports more at the same true burden.
  Removing district identity limits, but does not eliminate, this.
- **Most flagged districts see nothing.** Precision at the operating threshold is
  8.6%. This is a triage ranking for scarce inspection capacity, not a prediction
  that a named district will have an outbreak.
- **It cannot see emergence.** Outbreaks with no recent local or neighbouring
  activity — 255 of 473, over half — are caught 2.0% of the time, against 57.8%
  when some signal is present. The features are activity-based, so this is a
  property of the design, not a bug to tune away. Closing it needs a leading
  indicator the open data does not carry: veterinary case reports, drug sales,
  animal movement records.
- **Dry-season recall is weaker** (20.7% vs 34.4% in monsoon). Fewer outbreaks
  cluster, so less spreads.
- **Modest absolute performance.** PR-AUC 0.068 on 473 positive test cases. The
  lift is robust; the interval around it is wide.
- **Human data ends in 2022.** EpiClim's coverage stops there; later IDSP records
  are not openly available in clean form.
- **This is a triage aid**, for allocating scarce inspection effort. It is not a
  diagnosis and not a replacement for surveillance.

## Repository

```
src/        pipeline, model, evaluation — each with a self-check
dashboard/  static Leaflet dashboard
data/raw/       downloaded sources (not committed)
data/interim/   built panel, predictions, stability results
```

## Licence and attribution

Code MIT. Data remains under its original licences: EpiClim CC BY 4.0, ARTPARK
public data per its repository, NASA POWER free for research.
