# KPI threshold sources

Which thresholds the analysis judges on, and where each one comes from.
A rule is judged only when it carries a `source` in `thresholds_*.yaml`
(`ThresholdRule.judged`). Every judged line is applied to **each hourly value
on its own** — never to a daily or window average. `warning` = `critical`: a
value beyond the line is an **Issue**.

## Main KPIs — judged

| Tech | KPI | Schema name | Issue when (per hour) | Level | Source |
|---|---|---|---|---|---|
| 4G | High PRB (DL and UL) | `dl_prb_util`, `ul_prb_util` | > 80 % | Cell | R5 team analysis rules, 2026-09-27 |
| 4G | Availability | `cell_avail_pct` | < 99 % | Cell | R5 team analysis rules, 2026-09-27 |
| 4G | FDD UL interference | `ul_rssi_dbm` | worse (higher) than −105 dBm | Cell | R5 team analysis rules, 2026-09-27 |
| 4G | TDD UL interference | `ul_rssi_tdd_dbm` | worse (higher) than −100 dBm | Cell (CELL_TDD) | R5 team analysis rules, 2026-09-27 |
| 3G | Availability | `cell_avail_pct` | < 99 % | as exported (Cell or NodeB) | R5 team analysis rules, 2026-09-27 |
| 3G | RTWP | `ul_rtwp_dbm` | worse (higher) than −90 dBm | as exported (Cell or NodeB) | R5 team analysis rules, 2026-09-27 |
| 3G | DL flow control drops | `dl_flowctrl_drops` | > 100,000 in one hour | Site | R5 team analysis rules, 2026-09-27 |

Interference and RTWP: the rules were written "< −105 / < −100 / < −90 dBm =
Issue". A higher (less negative) interference or RTWP is the worse one, and the
existing configuration already judged these KPIs as bad *above* the line, so
the line is applied as "worse than": −104 dBm breaches −105, −110 dBm does not.

## Every other KPI — not judged

No official Huawei document confirming a threshold could be found. Huawei's
KPI references (eNodeB / RNC "KPI Reference") define each KPI's counters and
formula; the target values are set per operator. The only threshold values
found online were third-party reposts (slide decks on scribd, slideshare,
pdfcoffee), which are not a reliable Huawei source.

So these KPIs are **shown, never judged**. Their old values stay in the YAML
for reference only:

- 4G: RRC / E-RAB / call setup success rate (CSSR), RACH, E-RAB and context
  drop rates, handover success rates, ping-pong, DL / UL user throughput,
  latency, RSRQ, SINR, CQI, BLER, TA, PDCCH, connected / active users,
  cell unavailable minutes, PUCCH RSSI, PRB interference share.
- 3G: RRC / RAB setup, CS / PS drop rate, soft / IRAT handover, RSCP, Ec/No,
  HSDPA throughput, DL power, IPPM RTT.
- 4G S1 signalling failures: a count, shown as "Detected", never judged (unchanged).

## RSRP (coverage) — existing line, not confirmed

Poor coverage at a user location is RSRP ≤ −105 dBm: the `avg_rsrp_dbm`
warning line the system already used for Sleep coverage checks, and the
Fair/Poor boundary of the RSRP bands (`coverage:` block). It is the existing
configured value, **not confirmed from an official Huawei source**. A user
location with no RSRP grid under it is a Coverage Issue whatever the line.
