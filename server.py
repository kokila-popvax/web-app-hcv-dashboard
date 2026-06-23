"""
server.py — HCV Cross-Neutralization Dashboard (Flask)
=======================================================
Flask web server replacing the Streamlit frontend.

Run locally:
    pip install -r requirements.txt
    python server.py

Deploy on Render.com:
    - Push to GitHub, connect repo on render.com — render.yaml handles the rest.
    - Set SECRET_KEY and GOOGLE_SERVICE_ACCOUNT_JSON env vars in Render dashboard.

Google Sheets credentials (pick one):
    1. Set GOOGLE_SERVICE_ACCOUNT_JSON env var to the full service-account JSON string
    2. Place service_account.json under .secrets/service_account.json
    3. Keep the existing .streamlit/secrets.toml (auto-detected as fallback)
"""
from __future__ import annotations

import io
import json
import os
import time
import uuid
import logging

import pandas as pd
from flask import (Flask, render_template, request, jsonify,
                   session, send_file)

import hcv_data as H
import hcv_viz as V

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "hcv-dashboard-dev-key-change-in-prod")
# Allow session cookie in cross-origin iframes (needed for HF Spaces)
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True

DEFAULT_IC50_SHEET = "1pGonKQsnbD4E_-ywm8-XjX_cG6eW0yF5-5hRgHDVWRc"
DEFAULT_CONSTRUCT_SHEET = "1iv9fbnKjvWt_LCKuNPJy58ldJJGfey0SoJSv69cJF3E"

TOP4_PSVS = [
    "IH_1a154/H77_Twist_PL2069", "IH_1b34_PVX_PL2056",
    "IH_1b58_PVX_PL2058", "IH_1a72_PVX_PL2014",
]
TOP4_N = 4

# ── in-memory session cache ───────────────────────────────────────────────────
_CACHE: dict[str, dict] = {}
CACHE_TTL = 600


def _get_raw(sid: str):
    entry = _CACHE.get(sid)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["df_ic50"], entry.get("df_const")
    return None, None


def _set_raw(sid: str, df_ic50: pd.DataFrame, df_const=None):
    _CACHE[sid] = {"df_ic50": df_ic50, "df_const": df_const, "ts": time.time()}


def _ensure_sid():
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return session["sid"]


# ── Google Sheets auth ────────────────────────────────────────────────────────
def _gspread_client():
    import gspread
    from google.oauth2.service_account import Credentials

    SCOPES = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    json_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_str:
        creds = Credentials.from_service_account_info(json.loads(json_str), scopes=SCOPES)
        return gspread.authorize(creds)

    local = os.path.join(os.path.dirname(__file__), ".secrets", "service_account.json")
    if os.path.exists(local):
        return gspread.service_account(filename=local)

    try:
        import toml
        secrets_path = os.path.join(os.path.dirname(__file__), ".streamlit", "secrets.toml")
        if os.path.exists(secrets_path):
            sec = toml.load(secrets_path)
            if "gcp_service_account" in sec:
                creds = Credentials.from_service_account_info(
                    sec["gcp_service_account"], scopes=SCOPES)
                return gspread.authorize(creds)
    except Exception:
        pass

    raise RuntimeError(
        "No Google service-account credentials found. "
        "Set the GOOGLE_SERVICE_ACCOUNT_JSON environment variable, "
        "or place service_account.json under .secrets/."
    )


def _load_gsheet(sheet_id: str, worksheet: str | None) -> pd.DataFrame:
    gc = _gspread_client()
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(worksheet) if worksheet else sh.sheet1
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    header = [c.strip() for c in values[0]]
    body = [row + [""] * (len(header) - len(row)) for row in values[1:]]
    return pd.DataFrame(body, columns=header)


# ── routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    _ensure_sid()
    return render_template(
        "index.html",
        default_ic50=DEFAULT_IC50_SHEET,
        default_const=DEFAULT_CONSTRUCT_SHEET,
    )


@app.route("/api/load/gsheet", methods=["POST"])
def api_load_gsheet():
    sid = _ensure_sid()
    body = request.get_json(force=True)
    sheet_id = body.get("sheet_id", DEFAULT_IC50_SHEET)
    worksheet = body.get("worksheet") or None
    const_id = body.get("const_id", DEFAULT_CONSTRUCT_SHEET)
    try:
        df_ic50 = _load_gsheet(sheet_id, worksheet)
        df_const = None
        if const_id:
            try:
                df_const = _load_gsheet(const_id, None)
            except Exception:
                pass
        _set_raw(sid, df_ic50, df_const)
        return jsonify({"ok": True, "rows": len(df_ic50)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/load/url", methods=["POST"])
def api_load_url():
    sid = _ensure_sid()
    body = request.get_json(force=True)
    url = body.get("url", "")
    if not url:
        return jsonify({"ok": False, "error": "No URL provided"}), 400
    try:
        import requests as req
        r = req.get(url, timeout=30)
        r.raise_for_status()
        df_ic50 = pd.read_csv(io.StringIO(r.text))
        _set_raw(sid, df_ic50)
        return jsonify({"ok": True, "rows": len(df_ic50)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/load/upload", methods=["POST"])
def api_load_upload():
    sid = _ensure_sid()
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400
    try:
        name = f.filename.lower()
        data = f.read()
        if name.endswith((".xlsx", ".xls")):
            df_ic50 = pd.read_excel(io.BytesIO(data))
        else:
            df_ic50 = pd.read_csv(io.BytesIO(data))
        _set_raw(sid, df_ic50)
        return jsonify({"ok": True, "rows": len(df_ic50)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/clear", methods=["POST"])
def api_clear():
    sid = _ensure_sid()
    _CACHE.pop(sid, None)
    return jsonify({"ok": True})


def _prepare(sid: str, corrected_ic50: bool):
    df_ic50, df_const = _get_raw(sid)
    if df_ic50 is None:
        return None, None, "No data loaded. Please load a data source first."
    construct_lookup = H.build_construct_lookup(df_const) if df_const is not None else {}
    tidy, info = H.prepare_dataframe(
        df_ic50,
        construct_lookup=construct_lookup or None,
        corrected_ic50=corrected_ic50,
    )
    return tidy, info, None


def _top4_filter(tidy_df, view_df, subgroups, n=TOP4_N):
    rows = []
    for sg in subgroups:
        sg_df = tidy_df[tidy_df["Subgroup"] == sg]
        if sg_df.empty:
            continue
        constructs_in_sg = set(sg_df["Construct_Description"].unique())
        scores = {}
        for construct in constructs_in_sg:
            cv = view_df[view_df["Construct_Description"] == construct]["value"]
            positive = cv[cv > 0].dropna()
            scores[construct] = (len(positive), float(positive.sum()))
        top_constructs = sorted(scores, key=scores.get, reverse=True)[:n]
        rows.append(sg_df[sg_df["Construct_Description"].isin(top_constructs)])
    return pd.concat(rows) if rows else pd.DataFrame(columns=tidy_df.columns)


@app.route("/api/render", methods=["POST"])
def api_render():
    try:
        return _api_render_inner()
    except Exception as exc:
        import traceback
        app.logger.error("api_render error: %s\n%s", exc, traceback.format_exc())
        return jsonify({"ok": False, "error": str(exc)}), 500


def _api_render_inner():
    sid = _ensure_sid()
    b = request.get_json(force=True)

    corrected_ic50 = b.get("corrected_ic50", True)
    tidy, info, err = _prepare(sid, corrected_ic50)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if tidy.empty:
        return jsonify({"ok": False, "error":
                        "No rows survived filtering. Check the sheet has the expected columns and HCV experiments."}), 400

    metric        = b.get("metric", "pct_neut")
    dilution      = float(b["dilution"]) if b.get("dilution") is not None else None
    mode          = b.get("mode", "gradient")
    threshold     = float(b.get("threshold", 50.0))
    ge            = b.get("ge", True)
    view_mode     = b.get("view_mode", "single")
    bucket        = b.get("bucket") or None
    subgroup      = b.get("subgroup", "All constructs")
    sort_by       = b.get("sort_by", "breadth")
    sort_desc     = b.get("sort_descending", True)
    experiments   = b.get("experiments") or list(info["experiments"])
    groups        = b.get("groups") or list(info["groups"])
    psvs_sel      = b.get("psvs") or list(info["psvs"])
    show_values   = b.get("show_values", True)
    use_geno      = b.get("use_geno", False)

    psv_geno = H.PSV_GENOTYPE if (use_geno and H.PSV_GENOTYPE) else None
    thr_pct  = threshold if metric == "pct_neut" else 50.0

    # If dilution wasn't sent (e.g. first render before dropdown is populated),
    # pick the best default: prefer 90, then 30, then first available.
    if metric == "pct_neut" and dilution is None:
        dils = list(info["all_dilutions"] or [30.0])
        dilution = next((d for d in dils if d == 90.0),
                        next((d for d in dils if d == 30.0),
                             dils[0] if dils else 30.0))

    f = tidy[tidy["Experiment"].isin(experiments) & tidy["PSV"].isin(psvs_sel)]
    if groups:
        f = f[f["Group"].isin(groups)]
    if subgroup != "All constructs":
        f = f[f["Subgroup"] == subgroup]

    results = []

    if view_mode == "single":
        view = H.compute_view(f, metric=metric, dilution=dilution)
        vp, sp, cnt = H.build_pivots(
            view, bucket=bucket, metric=metric, mode=mode,
            threshold=threshold, ge=ge,
            sort_by=sort_by, sort_descending=sort_desc, psv_genotype=psv_geno)
        if not vp.empty:
            dil_lbl    = f"% neut @ 1:{int(dilution)}" if metric == "pct_neut" else "log₁₀(IC50)"
            bucket_lbl = bucket or "All (pooled)"
            thr_sfx    = f"  ·  ≥{threshold}" if mode == "threshold" else ""
            title      = f"{subgroup}  ·  {bucket_lbl}  ·  {dil_lbl}{thr_sfx}"
            fig = V.build_heatmap_figure(vp, sp, cnt, metric, mode, threshold,
                                         title=title, psv_genotype=psv_geno,
                                         show_values=show_values)
            results.append({"id": "main", "title": title,
                             "fig": json.loads(fig.to_json()),
                             "constructs": list(vp.index), "psvs": list(vp.columns)})

    elif view_mode == "small_multiples":
        for sg in (info["subgroups_present"] or ["All constructs"]):
            fsg  = f[f["Subgroup"] == sg] if sg != "All constructs" else f
            vsub = H.compute_view(fsg, metric=metric, dilution=dilution)
            vp, sp, cnt = H.build_pivots(
                vsub, bucket=bucket, metric=metric, mode=mode,
                threshold=threshold, ge=ge,
                sort_by=sort_by, sort_descending=sort_desc, psv_genotype=psv_geno)
            if vp.empty:
                continue
            fig = V.build_heatmap_figure(vp, sp, cnt, metric, mode, threshold,
                                         title="", psv_genotype=psv_geno,
                                         show_values=show_values)
            results.append({"id": f"sm_{sg}",
                             "title": f"{sg}  ·  {len(vp)} constructs × {len(vp.columns)} PSVs",
                             "fig": json.loads(fig.to_json()),
                             "constructs": list(vp.index), "psvs": list(vp.columns)})

    else:  # top4
        all_psvs_set = set(tidy["PSV"].unique())
        matched_psvs = []
        for target in TOP4_PSVS:
            exact = [p for p in all_psvs_set if p == target]
            matched_psvs.extend(exact if exact else
                                 [p for p in all_psvs_set if target.lower() in p.lower()])
        matched_psvs = list(dict.fromkeys(matched_psvs))

        if matched_psvs:
            subs         = info["subgroups_present"] or []
            f_top4_psvs  = f[f["PSV"].isin(matched_psvs)]
            v_top4_psvs  = H.compute_view(f_top4_psvs, metric=metric, dilution=dilution)
            f_top4       = _top4_filter(f_top4_psvs, v_top4_psvs, subs)

            if not f_top4.empty:
                for sg in subs:
                    fsg = f_top4[f_top4["Subgroup"] == sg]
                    if fsg.empty:
                        continue
                    vsg = H.compute_view(fsg, metric=metric, dilution=dilution)
                    vp, sp, cnt = H.build_pivots(
                        vsg, bucket=bucket, metric=metric, mode=mode,
                        threshold=threshold, ge=ge,
                        sort_by=sort_by, sort_descending=sort_desc, psv_genotype=None)
                    if vp.empty:
                        continue
                    for p in matched_psvs:
                        if p not in vp.columns:
                            vp[p] = float("nan")
                            sp[p] = "not_tested"
                    vp = vp[matched_psvs]
                    sp = sp[matched_psvs]
                    fig = V.build_heatmap_figure(vp, sp, cnt, metric, mode, threshold,
                                                 title="", psv_genotype=None,
                                                 show_values=show_values, row_height=48)
                    results.append({"id": f"top4_{sg}",
                                    "title": f"{sg}  ·  {len(vp)} constructs × {len(vp.columns)} PSVs",
                                    "fig": json.loads(fig.to_json()),
                                    "constructs": list(vp.index), "psvs": list(vp.columns)})

    buckets_present = [bk for bk in H.BUCKETS if bk in set(tidy["Bucket_Type"].unique())]

    return jsonify({
        "ok": True,
        "results": results,
        "thr_pct": thr_pct,
        "info": {
            "n_raw": info["n_raw"],
            "n_after_filter": info["n_after_filter"],
            "n_unknown_bucket": info["n_unknown_bucket"],
            "n_uncategorized": info["n_uncategorized"],
            "experiments": list(info["experiments"]),
            "groups": list(info["groups"]),
            "psvs": list(info["psvs"]),
            "subgroups_present": list(info["subgroups_present"]),
            "all_dilutions": list(info["all_dilutions"] or [30.0]),
            "buckets_present": buckets_present,
            "columns": info["columns"],
        },
    })


@app.route("/api/curve", methods=["POST"])
def api_curve():
    sid = _ensure_sid()
    b = request.get_json(force=True)
    corrected_ic50 = b.get("corrected_ic50", True)
    tidy, info, err = _prepare(sid, corrected_ic50)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    construct   = b.get("construct")
    psv         = b.get("psv")
    bucket      = b.get("bucket") or None
    metric      = b.get("metric", "pct_neut")
    threshold   = float(b.get("threshold", 50.0))
    experiments = b.get("experiments") or list(info["experiments"])
    groups      = b.get("groups") or list(info["groups"])
    psvs_sel    = b.get("psvs") or list(info["psvs"])

    f = tidy[tidy["Experiment"].isin(experiments) & tidy["PSV"].isin(psvs_sel)]
    if groups:
        f = f[f["Group"].isin(groups)]

    thr_pct = threshold if metric == "pct_neut" else 50.0
    curve   = H.get_curve(f, construct, psv, buckets=[bucket] if bucket else None)
    fig     = V.build_curve_figure(curve, construct, psv, thr_pct)
    return jsonify({"ok": True, "fig": json.loads(fig.to_json())})


@app.route("/api/download", methods=["POST"])
def api_download():
    sid = _ensure_sid()
    b = request.get_json(force=True)
    corrected_ic50 = b.get("corrected_ic50", True)
    tidy, info, err = _prepare(sid, corrected_ic50)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    metric      = b.get("metric", "pct_neut")
    dilution    = float(b["dilution"]) if b.get("dilution") is not None else None
    mode        = b.get("mode", "gradient")
    threshold   = float(b.get("threshold", 50.0))
    ge          = b.get("ge", True)
    bucket      = b.get("bucket") or None
    subgroup    = b.get("subgroup", "All constructs")
    sort_by     = b.get("sort_by", "breadth")
    sort_desc   = b.get("sort_descending", True)
    experiments = b.get("experiments") or list(info["experiments"])
    groups      = b.get("groups") or list(info["groups"])
    psvs_sel    = b.get("psvs") or list(info["psvs"])

    f = tidy[tidy["Experiment"].isin(experiments) & tidy["PSV"].isin(psvs_sel)]
    if groups:
        f = f[f["Group"].isin(groups)]
    if subgroup != "All constructs":
        f = f[f["Subgroup"] == subgroup]

    view = H.compute_view(f, metric=metric, dilution=dilution)
    vp, sp, _ = H.build_pivots(view, bucket=bucket, metric=metric, mode=mode,
                                threshold=threshold, ge=ge,
                                sort_by=sort_by, sort_descending=sort_desc)
    if vp.empty:
        return jsonify({"ok": False, "error": "No data to download"}), 400

    if vp.index.name is None:
        vp.index.name = "Construct_Description"
    if sp.index.name is None:
        sp.index.name = "Construct_Description"

    long   = vp.reset_index().melt(id_vars="Construct_Description", var_name="PSV", value_name="value")
    stat   = sp.reset_index().melt(id_vars="Construct_Description", var_name="PSV", value_name="status")
    merged = long.merge(stat, on=["Construct_Description", "PSV"])

    buf = io.BytesIO(merged.to_csv(index=False).encode())
    buf.seek(0)
    return send_file(buf, mimetype="text/csv", as_attachment=True,
                     download_name="hcv_view.csv")


if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
