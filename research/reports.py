"""Figures and searchable local HTML reports for fleetcan-sim-research-1.0 (from results/ and validation/ CSVs).

Usage: .venv/bin/python -m research.reports
Writes RELEASE/reports/figures/*.png and RELEASE/reports/html/*.html. The Markdown reports in RELEASE/reports/*.md
(written from the validated results) are rendered into the HTML pages. Raw CAN frames are never embedded.
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import numpy as np                       # noqa: E402
import pandas as pd                      # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.descriptor_schema import FEATURE_COLUMNS          # noqa: E402
from research.common import RELEASE, atomic_text                # noqa: E402

RES, VAL, REP = RELEASE / "results", RELEASE / "validation", RELEASE / "reports"
FIG, HT = REP / "figures", REP / "html"
PCOL = {"A": "#1f77b4", "B": "#ff7f0e", "C": "#2ca02c"}
FAM = ["flooding", "fuzzy", "malfunction", "replay"]


def r(name, **kw):
    return pd.read_csv(RES / name, **kw)


def v(name, **kw):
    return pd.read_csv(VAL / name, **kw)


def save(fig, name):
    FIG.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(FIG / name, dpi=130)
    plt.close(fig)


def figures():
    sess, veh, att = r("session_summary.csv"), r("vehicle_summary.csv"), r("attack_instances.csv")
    camp, cid = r("campaign_instances.csv"), r("can_id_statistics.csv")
    # 01 frames per session
    fig, ax = plt.subplots(figsize=(8, 4))
    for i, (sc, g) in enumerate(sess.groupby("scenario")):
        ax.scatter(g.N + (i - 2.5) * 1.2, g.frames / 1e6, s=10, label=sc)
    ax.set_xlabel("fleet size N"); ax.set_ylabel("CAN frames per session (M)"); ax.set_xscale("log")
    ax.set_xticks([10, 20, 50, 100]); ax.set_xticklabels(["10", "20", "50", "100"]); ax.legend(fontsize=8)
    ax.set_title("Frames per session (835 sessions)")
    save(fig, "01_frames_per_session.png")
    # 02 benign rate per vehicle by profile
    fig, ax = plt.subplots(figsize=(7, 4))
    data = [veh[veh.profile == p].benign_fps_active.dropna() for p in "ABC"]
    ax.boxplot(data, tick_labels=[f"profile {p}" for p in "ABC"], showfliers=True)
    ax.set_ylabel("benign frames / s (active period)"); ax.set_title("Benign message rate per vehicle trace")
    save(fig, "02_benign_rate_by_profile.png")
    # 03 per-ID benign period
    b = cid[cid.frame_class == "benign"]
    fig, ax = plt.subplots(figsize=(9, 4))
    lab = [f"{p}:{i}" for p, i in zip(b.profile, b.can_id)]
    ax.bar(range(len(b)), b.period_ms_mean, yerr=b.period_ms_std, color=[PCOL[p] for p in b.profile])
    ax.set_xticks(range(len(b))); ax.set_xticklabels(lab, rotation=60, fontsize=7)
    ax.set_ylabel("period (ms), mean ± std"); ax.set_title("Benign CAN-ID periods per profile (all vehicles)")
    save(fig, "03_benign_can_id_periods.png")
    # 04 payload byte variability
    pl = r("payload_statistics.csv")
    fig, ax = plt.subplots(figsize=(7, 3))
    m = pl.pivot(index="profile", columns="byte_position", values="std")
    im = ax.imshow(m.values, aspect="auto", cmap="viridis")
    ax.set_yticks(range(len(m))); ax.set_yticklabels(m.index); ax.set_xlabel("payload byte position")
    for (i, j), x in np.ndenumerate(m.values):
        ax.text(j, i, "–" if np.isnan(x) else f"{x:.0f}", ha="center", va="center", color="w", fontsize=8)
    fig.colorbar(im, ax=ax, label="std of byte value"); ax.set_title("Benign payload byte variability")
    save(fig, "04_payload_byte_std.png")
    # 05 onset delay by family
    fig, ax = plt.subplots(figsize=(8, 4))
    d = [att[att.attack_family == f].derived_onset_delay_ms.dropna().clip(lower=1e-3) for f in FAM]
    ax.boxplot(d, tick_labels=FAM); ax.set_yscale("log"); ax.set_ylabel("first malicious frame − configured start (ms)")
    ax.set_title("Observable onset delay per attack instance (12,180 instances)")
    save(fig, "05_onset_delay_by_family.png")
    # 06 first window delays
    fig, ax = plt.subplots(figsize=(8, 4))
    for k, c in (("ge1", "C0"), ("ge5", "C1"), ("ge50", "C3")):
        x = att[f"derived_first_window_{k}_delay_ms"].dropna()
        ax.hist(np.log10(x.clip(lower=1)), bins=60, alpha=.5, color=c, label=f"first window with ≥{k[2:]} attack frames (n={len(x)})")
    ax.set_xlabel("log10 delay from configured start to descriptor timestamp (ms)"); ax.legend(fontsize=8)
    ax.set_title("Descriptor-level observability")
    save(fig, "06_first_window_delay.png")
    # 07 campaign spread
    co = camp[camp.coordinated.astype(str) == "true"]
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.scatter(co.configured_onset_spread_s, co.derived_window_onset_spread_s, s=8, alpha=.6)
    lim = [0, max(1, co.configured_onset_spread_s.max())]
    ax.plot(lim, lim, "k--", lw=.8); ax.set_xlabel("configured onset spread (s)"); ax.set_ylabel("descriptor-level onset spread (s)")
    ax.set_xscale("symlog", linthresh=0.1); ax.set_yscale("symlog", linthresh=0.1)
    ax.set_title("Coordinated campaigns: configured vs observable spread")
    save(fig, "07_campaign_spread.png")
    # 08 replay
    rp = r("replay_activation_timing.csv")
    fig, ax = plt.subplots(figsize=(7, 4))
    for cls, g in rp.groupby("classification"):
        ax.scatter((g.source_ecu_activation_ns - g.replay_window_start_ns) / 1e6, g.configured_to_observable_onset_ms, s=8, label=f"{cls} ({len(g)})")
    ax.axvline(0, color="k", lw=.6); ax.set_xlabel("ECU activation − replay window start (ms)")
    ax.set_ylabel("configured → observable onset (ms)"); ax.set_yscale("log"); ax.legend(fontsize=7)
    ax.set_title("Replay onset vs source-ECU activation")
    save(fig, "08_replay_onset.png")
    # 09 G0 SMD
    g0 = r("G0_route_bias_statistics.csv")
    gb = g0[(g0.analysis == "benign_vehicle_metric") & (g0.comparison == "G0_vs_N20") & (g0.profile != "all")]
    fig, ax = plt.subplots(figsize=(9, 4))
    mets = list(dict.fromkeys(gb.metric))
    for j, p in enumerate("ABC"):
        x = gb[gb.profile == p].set_index("metric").reindex(mets).standardized_mean_difference
        ax.bar(np.arange(len(mets)) + (j - 1) * .27, x, .27, color=PCOL[p], label=f"profile {p}")
    ax.axhline(0.2, ls=":", c="k"); ax.axhline(-0.2, ls=":", c="k")
    ax.set_xticks(range(len(mets))); ax.set_xticklabels(mets, rotation=40, ha="right", fontsize=7)
    ax.set_ylabel("standardised mean difference"); ax.legend(fontsize=8)
    ax.set_title("G0 (N=10) vs N=20 non-attacked vehicles")
    save(fig, "09_G0_vs_N20_smd.png")
    # 10 attack rate by vehicle number
    vi = veh[veh.session_attack_family != "none"].copy()
    vi["num"] = vi.vehicle_id.str.extract(r"(\d+)$")[0].astype(int)
    vi["G0"] = vi.route_group == "G0"
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.5))
    for ax, (flag, g) in zip(axs, vi.groupby("G0")):
        rate = g.groupby("num").attacked.mean()
        ax.bar(rate.index, rate.values, width=1.0)
        ax.set_title("G0 (N = 10)" if flag else "P1/P2 (N = 20/50/100)"); ax.set_xlabel("vehicle number"); ax.set_ylabel("fraction of sessions attacked")
    save(fig, "10_attack_rate_by_vehicle_id.png")
    # 11 benign IF FPR
    fp = r("benign_false_positive_statistics.csv")
    fig, ax = plt.subplots(figsize=(8, 4))
    sel = fp[fp.grouping.isin(["pooled", "profile", "fleet_size", "route_group"])].copy()
    sel["label"] = sel.apply(lambda x: x.grouping if x.grouping == "pooled" else f"{x.grouping}={x.get('profile') if x.grouping == 'profile' else (int(x.get('N')) if x.grouping == 'fleet_size' else x.get('route_group'))}", axis=1)
    ax.bar(range(len(sel)), sel.fpr_strong_ge_0_80, yerr=[sel.fpr_strong_ge_0_80 - sel.fpr_strong_95ci_low, sel.fpr_strong_95ci_high - sel.fpr_strong_ge_0_80])
    ax.axhline(0.2, ls="--", c="r", label="exchangeable reference 0.20")
    ax.set_xticks(range(len(sel))); ax.set_xticklabels(sel.label, rotation=40, ha="right", fontsize=8)
    ax.set_ylabel("held-out benign windows ≥ 0.80"); ax.legend(fontsize=8)
    ax.set_title("Benign-only Isolation Forest: false-positive rate (original P2 strong band)")
    save(fig, "11_benign_if_fpr.png")
    # 12 benign over time
    # S0 sessions only (all 40 s, benign): mixing session lengths would show session ends as dips
    tb = pd.read_csv(RES / "benign_traffic_over_time_S0.csv")
    fig, ax = plt.subplots(figsize=(8, 4))
    for (p, grp), g in tb.groupby(["profile", "route_group"]):
        ax.plot(g.second + 0.5, g.fps_per_vehicle_mean, color=PCOL[p], ls="-" if grp == "P1" else ":", marker=".", label=f"{p} {grp}")
    ax.set_xlabel("simulation time (s, 1-s bins)"); ax.set_ylabel("benign frames / vehicle / s"); ax.legend(fontsize=7, ncol=2)
    ax.set_title("Benign traffic over time, S0 sessions (first bin = ECU switch-on U[0,1] s)")
    save(fig, "12_benign_over_time.png")
    # 13 descriptor features
    dsx = r("descriptor_statistics.csv")
    fig, axs = plt.subplots(1, 3, figsize=(13, 5), sharey=True)
    for ax, p in zip(axs, "ABC"):
        t = dsx[dsx.profile == p].pivot(index="feature", columns="window_class", values="mean").reindex(FEATURE_COLUMNS)
        z = (t.sub(t.benign_only, axis=0)).div(dsx[(dsx.profile == p) & (dsx.window_class == "benign_only")].set_index("feature").reindex(FEATURE_COLUMNS)["std"].replace(0, np.nan), axis=0)
        im = ax.imshow(z.values, aspect="auto", cmap="coolwarm", vmin=-3, vmax=3)
        ax.set_xticks(range(len(z.columns))); ax.set_xticklabels(z.columns, rotation=30, fontsize=7); ax.set_title(f"profile {p}")
    axs[0].set_yticks(range(24)); axs[0].set_yticklabels(FEATURE_COLUMNS, fontsize=7)
    fig.colorbar(im, ax=axs, label="(class mean − benign mean) / benign std"); fig.suptitle("Descriptor feature shift by window class")
    fig.savefig(FIG / "13_descriptor_shift.png", dpi=130); plt.close(fig)
    # 14 bus utilisation
    fig, ax = plt.subplots(figsize=(7, 4))
    dd = [att[att.attack_family == f].derived_bus_utilization_during_attack.dropna() for f in FAM]
    ax.boxplot(dd, tick_labels=FAM); ax.set_ylabel("bus utilisation during attack (100 ms bins)")
    ax.set_title("Logical-bus utilisation during attacks")
    save(fig, "14_bus_utilisation_attack.png")


# ---------------------------------------------------------------------------------------------------- HTML
CSS = """body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0;color:#1d1d1f;background:#fff}
header{background:#0b3d5c;color:#fff;padding:14px 22px}header a{color:#cde;margin-right:14px;font-size:13px}
main{padding:18px 22px;max-width:1500px}h1{font-size:22px;margin:4px 0}h2{font-size:18px;border-bottom:1px solid #ddd;padding-bottom:3px}
table{border-collapse:collapse;font-size:12px;margin:8px 0}th,td{border:1px solid #ccc;padding:3px 6px;text-align:left;vertical-align:top}
th{background:#eef3f7;position:sticky;top:0;cursor:pointer}tr:nth-child(even){background:#fafafa}.pass{background:#e3f6e3}.fail{background:#fde2e2}
.warn{background:#fff4d6}.wrap{max-height:620px;overflow:auto;border:1px solid #ddd}input.f{padding:5px;width:340px;margin:6px 0}
img{max-width:100%;border:1px solid #eee;margin:8px 0}code{background:#f3f3f3;padding:1px 3px}.note{color:#555;font-size:13px}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-weight:600;font-size:12px}"""
JS = """function filt(id){var q=document.getElementById('q_'+id).value.toLowerCase();var rows=document.querySelectorAll('#'+id+' tbody tr');
var n=0;rows.forEach(function(r){var s=r.textContent.toLowerCase().indexOf(q)>=0;r.style.display=s?'':'none';if(s)n++;});document.getElementById('n_'+id).textContent=n;}
function srt(id,c){var t=document.getElementById(id),b=t.tBodies[0],rs=Array.from(b.rows),d=t.dataset.d==c?-1:1;t.dataset.d=d==1?c:'';
rs.sort(function(x,y){var a=x.cells[c].textContent,z=y.cells[c].textContent,na=parseFloat(a),nz=parseFloat(z);
if(!isNaN(na)&&!isNaN(nz))return (na-nz)*d;return a.localeCompare(z)*d;});rs.forEach(function(r){b.appendChild(r)});}"""
PAGES = [("index", "Index"), ("dataset_overview", "Dataset overview"), ("sessions", "Sessions"), ("vehicles", "Vehicles"),
         ("vehicle_profiles", "Vehicle profiles"), ("benign_analysis", "Benign traffic"), ("attack_analysis", "Attacks"),
         ("campaign_analysis", "Campaigns"), ("temporal_analysis", "Temporal"), ("replay_analysis", "Replay"),
         ("G0_bias_analysis", "G0 / identity bias"), ("validation_results", "Validation"), ("limitations", "Limitations")]


def md_inline(t):
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    return t


def md_to_html(md: str) -> str:
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre>" + html.escape("\n".join(lines[i + 1:j])) + "</pre>")
            i = j + 1
            continue
        m = re.match(r"^(#{1,4}) (.*)", l)
        if m:
            k = min(len(m.group(1)) + 1, 4)
            out.append(f"<h{k}>{md_inline(m.group(2))}</h{k}>")
        elif l.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                if not re.match(r"^\|[-| :]+\|$", lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            h = "<table><thead><tr>" + "".join(f"<th>{md_inline(c)}</th>" for c in rows[0]) + "</tr></thead><tbody>"
            h += "".join("<tr>" + "".join(f"<td>{md_inline(c)}</td>" for c in rr) + "</tr>" for rr in rows[1:])
            out.append(h + "</tbody></table>")
            continue
        elif re.match(r"^\s*([*-]|\d+\.) ", l):
            items = []
            while i < len(lines) and re.match(r"^\s*([*-]|\d+\.) ", lines[i]):
                txt = re.sub(r"^\s*([*-]|\d+\.) ", "", lines[i])
                while i + 1 < len(lines) and lines[i + 1].startswith("  ") and not re.match(r"^\s*([*-]|\d+\.) ", lines[i + 1]):
                    i += 1
                    txt += " " + lines[i].strip()
                items.append(f"<li>{md_inline(txt)}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif l.strip():
            para = [l.strip()]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"^(#|\||```|\s*([*-]|\d+\.) )", lines[i + 1]):
                i += 1
                para.append(lines[i].strip())
            out.append(f"<p>{md_inline(' '.join(para))}</p>")
        i += 1
    return "\n".join(out)


def cell(x):
    if isinstance(x, float):
        if np.isnan(x):
            return ""
        return f"{x:.6g}"
    return html.escape(str(x))


def table(df: pd.DataFrame, tid: str, links: dict | None = None, status_col: str | None = None, limit=None) -> str:
    df = df if limit is None else df.head(limit)
    h = [f'<input class="f" id="q_{tid}" placeholder="search table…" onkeyup="filt(\'{tid}\')"> '
         f'<span class="note"><span id="n_{tid}">{len(df)}</span> rows</span><div class="wrap"><table id="{tid}"><thead><tr>']
    h += [f'<th onclick="srt(\'{tid}\',{j})">{html.escape(str(c))}</th>' for j, c in enumerate(df.columns)]
    h.append("</tr></thead><tbody>")
    si = list(df.columns).index(status_col) if status_col is not None else None
    for row in df.itertuples(index=False, name=None):
        cls = ""
        if si is not None:
            val = str(row[si]).lower()
            cls = ' class="pass"' if val in ("true", "pass", "passed", "ok") else (' class="fail"' if val in ("false", "fail", "failed", "error") else ' class="warn"')
        tds = []
        for c, x in zip(df.columns, row):
            if links and c in links and str(x):
                tds.append(f'<td><a href="{html.escape(links[c](x))}">{cell(x)}</a></td>')
            else:
                tds.append(f"<td>{cell(x)}</td>")
        h.append(f"<tr{cls}>" + "".join(tds) + "</tr>")
    h.append("</tbody></table></div>")
    return "".join(h)


def page(name: str, title: str, body: str):
    nav = "".join(f'<a href="{p}.html">{t}</a>' for p, t in PAGES)
    doc = (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>{html.escape(title)} — fleetcan-sim-research-1.0</title><style>{CSS}</style><script>{JS}</script></head><body>"
           f"<header><h1>fleetcan-sim-research-1.0 — {html.escape(title)}</h1>{nav}</header><main>{body}</main></body></html>")
    atomic_text(doc, HT / f"{name}.html")


def fig(name, caption=""):
    return f'<figure><img src="../figures/{name}" alt="{html.escape(caption)}"><figcaption class="note">{html.escape(caption)}</figcaption></figure>'


def csvlink(path):
    return f'<a href="../../{path}">{path}</a>'


def mdfile(name):
    p = REP / name
    return md_to_html(p.read_text()) if p.exists() else f"<p class=note>({name} not present)</p>"


def html_pages():
    HT.mkdir(parents=True, exist_ok=True)
    ds = r("dataset_summary.csv")
    sess = r("session_summary.csv")
    dec = (REP / "SCIENTIFIC_READINESS_DECISION.md").read_text() if (REP / "SCIENTIFIC_READINESS_DECISION.md").exists() else ""
    m = re.search(r"\*\*(RESEARCH DATASET [A-Z ]+?)\*\*", dec)
    verdict = m.group(1) if m else "see SCIENTIFIC_READINESS_DECISION.md"
    links = "".join(f"<li><a href='{p}.html'>{t}</a></li>" for p, t in PAGES[1:])
    page("index", "Index", f"<p><b>Readiness decision:</b> <span class='badge warn'>{verdict}</span> — "
         f"<a href='../SCIENTIFIC_READINESS_DECISION.md'>SCIENTIFIC_READINESS_DECISION.md</a>, "
         f"<a href='../FLEETCAN_SIM_Research_Dataset_Validation.pdf'>PDF validation report</a></p>"
         f"<p class=note>Synthetic data (SUMO mobility + synthetic CAN over a logical bus and SocketCAN vCAN). No production ECU captures."
         f" All tables cover all 835 sessions. Open locally; links point to files inside the release directory.</p><ul>{links}</ul>"
         + table(ds, "summary") + mdfile("SCIENTIFIC_READINESS_DECISION.md"))
    page("dataset_overview", "Dataset overview",
         table(ds, "ds") + "<h2>Scenarios</h2>" + table(r("scenario_summary.csv"), "sc") + "<h2>Fleet sizes</h2>"
         + table(r("fleet_size_summary.csv"), "fs") + fig("01_frames_per_session.png", "Frames per session") +
         "<p>CSV: " + ", ".join(csvlink(f"results/{x}") for x in ("dataset_summary.csv", "scenario_summary.csv", "fleet_size_summary.csv")) + "</p>")
    sl = {"session_id": lambda s: f"../../data/metadata/{s}/session.json", "raw_csv": lambda p: f"../../{p}",
          "raw_parquet": lambda p: f"../../{p}", "ground_truth": lambda p: f"../../{p}", "metadata": lambda p: f"../../{p}",
          "descriptors": lambda p: f"../../{p}"}
    page("sessions", "Sessions", "<p class=note>All 835 sessions. Click a session ID for its metadata; file columns open the "
         "session's observation, ground-truth, metadata and descriptor files. Green = all schema and ground-truth checks pass.</p>"
         + table(sess.assign(checks=sess.schema_checks_pass & sess.ground_truth_checks_pass), "sess", sl, "checks") + "<p>CSV: " + csvlink("results/session_summary.csv") + "</p>")
    veh = r("vehicle_summary.csv")
    cols = ["session_id", "vehicle_id", "profile", "attacked", "frames", "benign_frames", "malicious_frames", "benign_fps_active",
            "unique_benign_can_ids", "benign_can_id_entropy_bits", "benign_iat_ms_mean", "benign_mean_byte_std", "windows",
            "windows_with_malicious", "windows_majority_malicious", "switch_on_ns", "route_group", "N"]
    page("vehicles", "Vehicles", f"<p class=note>{len(veh)} vehicle traces (all sessions). The full table with every column is "
         f"{csvlink('results/vehicle_summary.csv')}. Search filters the list below (first 5,000 rows shown here; use the CSV for all).</p>"
         + table(veh[cols], "veh", {"session_id": lambda s: f"../../data/metadata/{s}/vehicles.csv"}, limit=5000))
    page("vehicle_profiles", "Vehicle profiles", mdfile_section("VEHICLE_PROFILES") + table(r("vehicle_profile_summary.csv"), "vp")
         + "<h2>CAN IDs</h2>" + table(r("can_id_statistics.csv"), "cid") + "<h2>DLC</h2>" + table(r("dlc_statistics.csv"), "dlc")
         + "<h2>Payload bytes</h2>" + table(r("payload_statistics.csv"), "pl") + fig("03_benign_can_id_periods.png") + fig("04_payload_byte_std.png"))
    page("benign_analysis", "Benign traffic and false-positive sanity check", mdfile("P1_benign_behavior.md")
         + fig("02_benign_rate_by_profile.png") + fig("12_benign_over_time.png") + fig("11_benign_if_fpr.png")
         + "<h2>benign_traffic_statistics.csv</h2>" + table(r("benign_traffic_statistics.csv"), "bt")
         + "<h2>benign_false_positive_statistics.csv</h2>" + table(r("benign_false_positive_statistics.csv"), "fp")
         + "<h2>Inter-arrival</h2>" + table(r("interarrival_statistics.csv"), "ia"))
    att = r("attack_instances.csv")
    page("attack_analysis", "Attacks", table(r("attack_family_summary.csv"), "af") + fig("05_onset_delay_by_family.png")
         + fig("14_bus_utilisation_attack.png") + fig("13_descriptor_shift.png")
         + f"<h2>All attack instances ({len(att)})</h2><p class=note>Full table: {csvlink('results/attack_instances.csv')}; first 5,000 rows below.</p>"
         + table(att[["session_id", "attack_id", "attack_family", "vehicle_id", "profile", "campaign_id", "configured_start_ns",
                      "derived_first_malicious_timestamp_ns", "derived_onset_delay_ms", "derived_malicious_frames",
                      "derived_injection_rate_hz", "derived_first_window_ge1_delay_ms", "derived_majority_windows"]], "ai",
                 {"session_id": lambda s: f"../../data/ground_truth/{s}/attack_instances.csv"}, limit=5000))
    camp = r("campaign_instances.csv")
    page("campaign_analysis", "Campaigns", table(r("campaign_timing.csv"), "ct") + fig("07_campaign_spread.png")
         + f"<h2>All campaigns ({len(camp)})</h2>" + table(camp, "ci", {"session_id": lambda s: f"../../data/ground_truth/{s}/campaigns.csv"}))
    page("temporal_analysis", "Temporal observability", mdfile("P1_temporal_validation.md") + fig("06_first_window_delay.png")
         + "<p>CSV: " + ", ".join(csvlink(f"results/{x}") for x in ("temporal_observability.csv", "descriptor_timing.csv", "campaign_timing.csv"))
         + ", " + csvlink("validation/P1_temporal_consistency.csv") + "</p>" + table(v("P1_temporal_consistency.csv"), "tc", status_col="pass"))
    page("replay_analysis", "Replay timing and ECU activation", mdfile("P2_replay_timing.md") + fig("08_replay_onset.png")
         + table(r("replay_activation_timing.csv"), "ra") + "<h2>Replay links</h2>" + table(r("replay_link_validation.csv"), "rl"))
    page("G0_bias_analysis", "G0 route and vehicle-identity bias", mdfile("P2_G0_bias.md") + fig("09_G0_vs_N20_smd.png")
         + fig("10_attack_rate_by_vehicle_id.png") + table(r("G0_route_bias_statistics.csv"), "g0") + "<h2>Vehicle identity</h2>"
         + table(r("vehicle_identity_bias.csv"), "vi"))
    vbody = "<p class=note>Green = pass, red = fail, yellow = needs attention / informational.</p>"
    for name, sc in (("source_integrity.csv", "status"), ("source_manifest_validation.csv", "pass"), ("source_schema_validation.csv", "pass"),
                     ("source_ground_truth_validation.csv", "pass"), ("source_campaign_validation.csv", "pass"),
                     ("source_replay_validation.csv", "pass"), ("P1_metadata_leakage.csv", None), ("P1_temporal_consistency.csv", "pass"),
                     ("P1_benign_sanity_check.csv", None), ("P2_replay_validation.csv", "pass"), ("P2_G0_bias_assessment.csv", None),
                     ("descriptor_compatibility.csv", "compatible"), ("test_results.csv", "outcome"), ("output_integrity.csv", "pass"),
                     ("issue_register.csv", None), ("unresolved_issues.csv", None), ("corrections_log.csv", None)):
        p = VAL / name
        if p.exists():
            df = pd.read_csv(p)
            if name == "source_integrity.csv":
                df = pd.concat([df[df.check == "anchor"], df[df.status != "OK"], df[df.check != "anchor"].head(200)])
                vbody += f"<h2>{name}</h2><p class=note>Anchors, all failures (if any) and the first 200 of {len(pd.read_csv(p))} file rows; full list in {csvlink('validation/' + name)}.</p>"
            else:
                vbody += f"<h2>{name}</h2><p class=note>{csvlink('validation/' + name)}</p>"
            vbody += table(df, re.sub(r"\W", "_", name), status_col=sc if sc in df.columns else None)
    page("validation_results", "Validation results", mdfile("P1_metadata_leakage.md") + vbody)
    page("limitations", "Limitations", mdfile_section("KNOWN_LIMITATIONS") + mdfile("descriptor_methodology.md"))


def mdfile_section(doc):
    p = RELEASE / "documentation" / f"{doc}.md"
    return md_to_html(p.read_text()) if p.exists() else ""


def main():
    figures()
    html_pages()
    print("figures:", len(list(FIG.glob("*.png"))), "html:", len(list(HT.glob("*.html"))))


if __name__ == "__main__":
    main()
