"""Phantom Watt Hunter — hackathon-ready energy monitoring MVP.

Runs with simulated telemetry. It does NOT connect to real electrical meters.
Real meter installation/integration should be done by qualified personnel.
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timedelta, time
from pathlib import Path
import random
import pandas as pd
import plotly.express as px
import streamlit as st

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "phantom_watt_hunter.db"

st.set_page_config(page_title="Phantom Watt Hunter", page_icon="⚡", layout="wide")

# ---------- Database ----------
def connect():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS facilities(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, facility_type TEXT NOT NULL,
          tariff_inr REAL NOT NULL DEFAULT 8.5, emission_factor REAL NOT NULL DEFAULT 0.82,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS zones(
          id INTEGER PRIMARY KEY, facility_id INTEGER NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
          name TEXT NOT NULL, area TEXT DEFAULT '', start_hour INTEGER NOT NULL DEFAULT 8,
          end_hour INTEGER NOT NULL DEFAULT 18, always_on INTEGER NOT NULL DEFAULT 0,
          UNIQUE(facility_id,name));
        CREATE TABLE IF NOT EXISTS circuits(
          id INTEGER PRIMARY KEY, zone_id INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
          name TEXT NOT NULL, circuit_type TEXT NOT NULL DEFAULT 'General', breaker_amps REAL NOT NULL DEFAULT 32,
          UNIQUE(zone_id,name));
        CREATE TABLE IF NOT EXISTS meters(
          id INTEGER PRIMARY KEY, facility_id INTEGER NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
          name TEXT NOT NULL, meter_code TEXT NOT NULL UNIQUE, zone_id INTEGER REFERENCES zones(id) ON DELETE SET NULL,
          circuit_id INTEGER REFERENCES circuits(id) ON DELETE SET NULL,
          parent_meter_id INTEGER REFERENCES meters(id) ON DELETE SET NULL,
          protocol TEXT NOT NULL DEFAULT 'SIMULATED', address TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
          stale_minutes INTEGER NOT NULL DEFAULT 60);
        CREATE TABLE IF NOT EXISTS appliances(
          id INTEGER PRIMARY KEY, zone_id INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
          circuit_id INTEGER REFERENCES circuits(id) ON DELETE SET NULL,
          meter_id INTEGER REFERENCES meters(id) ON DELETE SET NULL,
          name TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'General', quantity INTEGER NOT NULL DEFAULT 1,
          watts_each REAL NOT NULL, standby_watts_each REAL NOT NULL DEFAULT 0,
          duty_cycle REAL NOT NULL DEFAULT 0.7, scheduled_hours REAL NOT NULL DEFAULT 8,
          active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS readings(
          id INTEGER PRIMARY KEY, meter_id INTEGER NOT NULL REFERENCES meters(id) ON DELETE CASCADE,
          timestamp TEXT NOT NULL, power_kw REAL NOT NULL, energy_kwh REAL NOT NULL,
          source TEXT NOT NULL DEFAULT 'SIMULATED', quality TEXT NOT NULL DEFAULT 'VALID',
          UNIQUE(meter_id,timestamp));
        CREATE TABLE IF NOT EXISTS alerts(
          id INTEGER PRIMARY KEY, facility_id INTEGER NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
          meter_id INTEGER REFERENCES meters(id) ON DELETE SET NULL, zone_id INTEGER REFERENCES zones(id) ON DELETE SET NULL,
          circuit_id INTEGER REFERENCES circuits(id) ON DELETE SET NULL, timestamp TEXT NOT NULL,
          severity TEXT NOT NULL, rule TEXT NOT NULL, expected_kw REAL NOT NULL,
          measured_kw REAL NOT NULL, excess_kw REAL NOT NULL, estimated_excess_kwh REAL NOT NULL,
          estimated_cost_inr REAL NOT NULL, explanation TEXT NOT NULL, recommendation TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'OPEN');
        CREATE TABLE IF NOT EXISTS audit_log(
          id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL);
        """)


def log_action(action, details):
    with connect() as con:
        con.execute("INSERT INTO audit_log(timestamp,action,details) VALUES(?,?,?)",
                    (datetime.now().isoformat(timespec="seconds"), action, details))


def seed_demo():
    with connect() as con:
        if con.execute("SELECT COUNT(*) FROM facilities").fetchone()[0]:
            return
        cur = con.execute("INSERT INTO facilities(name,facility_type,tariff_inr) VALUES(?,?,?)",
                          ("KITSW Demo Campus", "College", 8.5))
        fid = cur.lastrowid
        demo = [
            ("Central Library", "Block II", 8, 19, 0),
            ("Sports Ground", "Outdoor arena", 17, 21, 0),
            ("Main Auditorium", "Silver Jubilee Block", 9, 17, 0),
            ("Computer Lab", "CSE Block", 8, 18, 0),
            ("Data Center", "Admin Block", 0, 23, 1),
        ]
        zone_ids = {}
        for name, area, start, end, always in demo:
            z = con.execute("INSERT INTO zones(facility_id,name,area,start_hour,end_hour,always_on) VALUES(?,?,?,?,?,?)",
                            (fid,name,area,start,end,always)).lastrowid
            zone_ids[name] = z
        circuits = {}
        for zone, cname, ctype, amps in [
            ("Central Library","Library AC","HVAC",63), ("Central Library","Library lights/fans","Lighting",32),
            ("Sports Ground","Floodlight feed","Lighting",40), ("Main Auditorium","Auditorium HVAC","HVAC",100),
            ("Main Auditorium","Stage lighting","Lighting",50), ("Computer Lab","Workstation power","IT",63),
            ("Data Center","Server/UPS feed","IT",80)]:
            circuits[(zone,cname)] = con.execute("INSERT INTO circuits(zone_id,name,circuit_type,breaker_amps) VALUES(?,?,?,?)",
                                                 (zone_ids[zone],cname,ctype,amps)).lastrowid
        main = con.execute("INSERT INTO meters(facility_id,name,meter_code,protocol) VALUES(?,?,?,?)",
                           (fid,"Main incomer","M-MAIN","SIMULATED")).lastrowid
        meter_defs = [
            ("Library AC meter","M-LIB-AC","Central Library","Library AC"),
            ("Library lighting meter","M-LIB-LT","Central Library","Library lights/fans"),
            ("Sports ground meter","M-SPORT","Sports Ground","Floodlight feed"),
            ("Auditorium HVAC meter","M-AUD-HV","Main Auditorium","Auditorium HVAC"),
            ("Stage lighting meter","M-AUD-LT","Main Auditorium","Stage lighting"),
            ("Computer lab meter","M-LAB","Computer Lab","Workstation power"),
            ("Server room meter","M-SERVER","Data Center","Server/UPS feed"),
        ]
        meter_ids = {}
        for name, code, zone, circuit in meter_defs:
            mid = con.execute("INSERT INTO meters(facility_id,name,meter_code,zone_id,circuit_id,parent_meter_id,protocol) VALUES(?,?,?,?,?,?,?)",
                              (fid,name,code,zone_ids[zone],circuits[(zone,circuit)],main,"SIMULATED")).lastrowid
            meter_ids[code] = mid
        app_defs = [
            ("Central Library","Library lights/fans","M-LIB-LT","LED lights", "Lighting",20,20,1.5,0.9,10.5),
            ("Central Library","Library lights/fans","M-LIB-LT","Ceiling fans", "Motors",10,60,2,0.8,10.5),
            ("Central Library","Library AC","M-LIB-AC","Split AC units", "HVAC",2,1500,15,0.75,8),
            ("Sports Ground","Floodlight feed","M-SPORT","Floodlights", "Lighting",4,500,0,1,3.5),
            ("Main Auditorium","Auditorium HVAC","M-AUD-HV","Package HVAC units", "HVAC",4,2000,25,0.8,6),
            ("Main Auditorium","Stage lighting","M-AUD-LT","Stage luminaires", "Lighting",30,100,5,0.9,4),
            ("Computer Lab","Workstation power","M-LAB","Desktop workstations", "IT",40,150,8,0.7,9.5),
            ("Data Center","Server/UPS feed","M-SERVER","Servers and storage", "IT",4,1200,1100,0.95,24),
        ]
        for zone,circuit,code,name,cat,qty,watts,standby,duty,hours in app_defs:
            con.execute("INSERT INTO appliances(zone_id,circuit_id,meter_id,name,category,quantity,watts_each,standby_watts_each,duty_cycle,scheduled_hours) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (zone_ids[zone],circuits[(zone,circuit)],meter_ids[code],name,cat,qty,watts,standby,duty,hours))
    log_action("SEED_DEMO", "Created KITSW demo campus, zones, circuits, meters and equipment")

# ---------- Data / analytics ----------
def load_table(query, params=()):
    with connect() as con:
        return pd.read_sql_query(query, con, params=params)


def expected_load_by_meter(facility_id):
    apps = load_table("""SELECT a.*, m.id AS mid, m.meter_code, m.name AS meter_name,
                        z.name AS zone_name, c.name AS circuit_name, z.start_hour,z.end_hour,z.always_on
                        FROM appliances a JOIN meters m ON a.meter_id=m.id
                        JOIN zones z ON a.zone_id=z.id LEFT JOIN circuits c ON a.circuit_id=c.id
                        WHERE m.facility_id=? AND a.active=1 AND m.active=1""", (facility_id,))
    if apps.empty:
        return apps
    apps["active_kw"] = apps["quantity"] * apps["watts_each"] * apps["duty_cycle"] / 1000
    apps["standby_kw"] = apps["quantity"] * apps["standby_watts_each"] / 1000
    return apps


def generate_demo_readings(facility_id, days=2, seed=42):
    random.seed(seed)
    apps = expected_load_by_meter(facility_id)
    meters = load_table("SELECT * FROM meters WHERE facility_id=? AND active=1", (facility_id,))
    if meters.empty:
        return 0
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=days)
    # Replace only readings for this facility; readings are explicitly marked SIMULATED.
    with connect() as con:
        ids = [int(x) for x in meters["id"].tolist()]
        con.executemany("DELETE FROM readings WHERE meter_id=?", [(x,) for x in ids])
        con.execute("DELETE FROM alerts WHERE facility_id=?", (facility_id,))
        rows = []
        times = [start + timedelta(minutes=30*i) for i in range(days*48+1)]
        energy_totals = {int(mid): 0.0 for mid in meters["id"]}
        child_ids = set(meters.loc[meters["parent_meter_id"].notna(),"id"].astype(int))
        for ts in times:
            hour = ts.hour + ts.minute/60
            child_power = {}
            for _, m in meters.iterrows():
                mid = int(m["id"])
                if mid not in child_ids:
                    continue
                mapped = apps[apps["mid"] == mid]
                if mapped.empty:
                    active_kw, standby_kw = 0.3, 0.05
                    zone = None
                else:
                    active_kw = mapped["active_kw"].sum()
                    standby_kw = mapped["standby_kw"].sum()
                    zone = mapped.iloc[0]
                is_open = bool(zone["always_on"]) if zone is not None else False
                if zone is not None and not is_open:
                    s,e = float(zone["start_hour"]), float(zone["end_hour"])
                    is_open = s <= hour < e if s < e else (hour >= s or hour < e)
                if is_open:
                    kw = max(0.02, random.gauss(active_kw, max(0.03,active_kw*0.07)))
                else:
                    kw = max(0.01, random.gauss(standby_kw, max(0.01,standby_kw*0.12)))
                # Deterministic demo anomalies for judging.
                code = m["meter_code"]
                if code == "M-LIB-AC" and 1 <= hour < 4.5: kw = random.uniform(2.8,3.3)
                if code == "M-SPORT" and 12 <= hour < 15: kw = random.uniform(1.9,2.1)
                if code == "M-LAB" and 20 <= hour < 23.5: kw = random.uniform(3.6,4.2)
                child_power[mid] = kw
                energy_totals[mid] += kw*0.5
                rows.append((mid,ts.isoformat(timespec="seconds"),round(kw,3),round(kw*0.5,4),"SIMULATED","VALID"))
            # Main meter sums direct children only; does not sum itself.
            for _, m in meters.iterrows():
                mid = int(m["id"])
                if pd.isna(m["parent_meter_id"]):
                    child_sum = sum(child_power.values())
                    kw = child_sum*1.025 + random.uniform(0.05,0.15)
                    if 10 <= hour < 12: kw += 6.5
                    energy_totals[mid] += kw*0.5
                    rows.append((mid,ts.isoformat(timespec="seconds"),round(kw,3),round(kw*0.5,4),"SIMULATED","VALID"))
        con.executemany("INSERT INTO readings(meter_id,timestamp,power_kw,energy_kwh,source,quality) VALUES(?,?,?,?,?,?)", rows)
    log_action("GENERATE_SIMULATION", f"Generated {days} days of SIMULATED readings for facility {facility_id}")
    run_anomaly_detection(facility_id)
    return len(rows)


def run_anomaly_detection(facility_id):
    readings = load_table("""SELECT r.*,m.facility_id,m.name AS meter_name,m.meter_code,m.zone_id,m.circuit_id,
                            z.name AS zone_name,z.start_hour,z.end_hour,z.always_on
                            FROM readings r JOIN meters m ON r.meter_id=m.id
                            LEFT JOIN zones z ON m.zone_id=z.id
                            WHERE m.facility_id=? AND r.source='SIMULATED' ORDER BY r.timestamp""", (facility_id,))
    if readings.empty: return 0
    apps = expected_load_by_meter(facility_id)
    facility = load_table("SELECT tariff_inr FROM facilities WHERE id=?",(facility_id,))
    rate = float(facility.iloc[0]["tariff_inr"]) if not facility.empty else 8.5
    alerts = []
    # Use appliance-derived baselines; avoid counting the same sample in two rules.
    for mid, grp in readings.groupby("meter_id"):
        meta = grp.iloc[0]
        mapped = apps[apps["mid"] == mid]
        if mapped.empty: continue
        active_kw = float(mapped["active_kw"].sum())
        standby_kw = float(mapped["standby_kw"].sum())
        for _, r in grp.iterrows():
            ts = pd.Timestamp(r["timestamp"])
            hour = ts.hour + ts.minute/60
            is_open = bool(meta["always_on"])
            if not is_open:
                s,e = float(meta["start_hour"]),float(meta["end_hour"])
                is_open = s <= hour < e if s < e else (hour >= s or hour < e)
            expected = active_kw if is_open else standby_kw
            measured = float(r["power_kw"])
            excess = measured - expected
            if excess <= max(0.25, expected*0.5): continue
            # The main feeder is handled as a separate feeder-loss check below.
            severity = "Critical" if excess >= 5 else ("High" if excess >= 1 else "Moderate")
            duration_hours = 0.5
            excess_kwh = excess*duration_hours
            rule = "AFTER_HOURS_LOAD" if not is_open else "LOAD_ABOVE_BASELINE"
            explanation = (f"Measured {measured:.2f} kW vs expected {expected:.2f} kW "
                           f"at {ts.strftime('%Y-%m-%d %H:%M')}. This is a suspicion, not proof of waste.")
            recommendation = "Check schedules and equipment state; verify against the meter and occupancy before changing operations."
            alerts.append((facility_id,int(mid),int(meta["zone_id"]) if pd.notna(meta["zone_id"]) else None,
                           int(meta["circuit_id"]) if pd.notna(meta["circuit_id"]) else None,
                           ts.isoformat(),severity,rule,expected,measured,excess,excess_kwh,excess_kwh*rate,
                           explanation,recommendation))
    # Main incomer vs child submeters: detect a persistent unexplained residual.
    # Match readings by timestamp and compare main power with direct child-meter sum.
    meter_meta = load_table("SELECT id,meter_code,parent_meter_id,zone_id,circuit_id FROM meters WHERE facility_id=?", (facility_id,))
    roots = meter_meta[meter_meta["parent_meter_id"].isna() & meter_meta["zone_id"].isna()]
    for _, root in roots.iterrows():
        root_id = int(root["id"])
        children = meter_meta[meter_meta["parent_meter_id"] == root_id]["id"].astype(int).tolist()
        if not children:
            continue
        root_series = readings[readings["meter_id"] == root_id][["timestamp","power_kw"]].rename(columns={"power_kw":"main_kw"})
        child_series = readings[readings["meter_id"].isin(children)].pivot_table(index="timestamp", columns="meter_id", values="power_kw", aggfunc="sum")
        if root_series.empty or child_series.empty:
            continue
        child_series["children_kw"] = child_series.sum(axis=1)
        compare = root_series.merge(child_series[["children_kw"]], left_on="timestamp", right_index=True, how="inner")
        for _, row in compare.iterrows():
            residual = float(row["main_kw"] - row["children_kw"])
            # Ignore normal small distribution differences; flag only material residuals.
            if residual > 3.0:
                ts = str(row["timestamp"])
                excess_kwh = residual * 0.5
                alerts.append((facility_id, root_id, None, None, ts, "High" if residual < 5 else "Critical",
                               "FEEDER_RESIDUAL", float(row["children_kw"]), float(row["main_kw"]), residual,
                               excess_kwh, excess_kwh*rate,
                               f"Main incomer reads {row['main_kw']:.2f} kW while mapped child submeters total {row['children_kw']:.2f} kW; residual {residual:.2f} kW.",
                               "Verify meter timestamps, coverage, wiring losses and any unmetered loads before concluding energy is wasted."))
    # Insert fresh generated alerts; avoid stale duplicate alerts after reruns.
    with connect() as con:
        con.execute("DELETE FROM alerts WHERE facility_id=?",(facility_id,))
        con.executemany("""INSERT INTO alerts(facility_id,meter_id,zone_id,circuit_id,timestamp,severity,rule,
          expected_kw,measured_kw,excess_kw,estimated_excess_kwh,estimated_cost_inr,explanation,recommendation)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", alerts)
    return len(alerts)


def facility_list():
    return load_table("SELECT * FROM facilities ORDER BY name")


def safe_num(value, default=0.0):
    try: return float(value)
    except (TypeError,ValueError): return default

# ---------- Styling ----------
st.markdown("""
<style>
.stApp {background: #0b1220; color: #edf4ff;}
[data-testid='stMetricValue'] {color:#47e6b1;}
.phw-subtitle {color:#9bb4c9; margin-top:-10px;}
</style>
""", unsafe_allow_html=True)

init_db()
seed_demo()
facilities = facility_list()

st.sidebar.title("⚡ Phantom Watt Hunter")
st.sidebar.caption("Energy waste detection · Hackathon MVP")
facility_options = {f"{r['name']} ({r['facility_type']})": int(r["id"]) for _,r in facilities.iterrows()}
selected_facility_label = st.sidebar.selectbox("Facility", list(facility_options))
facility_id = facility_options[selected_facility_label]
facility_row = facilities[facilities["id"] == facility_id].iloc[0]
page = st.sidebar.radio("Navigate", ["Dashboard", "Inventory & Mapping", "Waste Hunter", "Readings & Export", "Settings & Audit"])
st.sidebar.divider()
st.sidebar.caption("DEMO MODE: all generated readings are simulated, not live meter data.")

if page == "Dashboard":
    st.title("⚡ Phantom Watt Hunter")
    st.markdown("<p class='phw-subtitle'>Find suspicious energy use across facilities, zones, circuits and mapped meters.</p>",unsafe_allow_html=True)
    latest = load_table("""SELECT r.*,m.name AS meter_name,m.meter_code,m.zone_id,m.parent_meter_id,m.circuit_id,z.name AS zone_name
                         FROM readings r JOIN meters m ON r.meter_id=m.id LEFT JOIN zones z ON m.zone_id=z.id
                         WHERE m.facility_id=? ORDER BY r.timestamp DESC""",(facility_id,))
    if latest.empty:
        st.info("No readings yet. Use the sidebar's demo-data button below to generate readings.")
    else:
        latest_ts = latest["timestamp"].max()
        latest_slice = latest[latest["timestamp"] == latest_ts]
        # Prefer the main incomer for whole-facility KPIs; never add parent and child meters together.
        main_latest = latest_slice[latest_slice["parent_meter_id"].isna() & latest_slice["zone_id"].isna()]
        total_kw = float(main_latest["power_kw"].sum()) if not main_latest.empty else float(latest_slice["power_kw"].sum())
        if not main_latest.empty:
            main_ids = main_latest["meter_id"].tolist()
            total_kwh = float(load_table("SELECT COALESCE(SUM(energy_kwh),0) AS e FROM readings WHERE meter_id IN (" + ",".join("?" for _ in main_ids) + ")", tuple(main_ids)).iloc[0]["e"])
        else:
            # For facilities without a configured main incomer, use only meters that are not parents of other meters.
            meter_meta = load_table("SELECT id,parent_meter_id FROM meters WHERE facility_id=?", (facility_id,))
            parent_ids = set(meter_meta["parent_meter_id"].dropna().astype(int).tolist())
            leaf_ids = [int(mid) for mid in meter_meta["id"].tolist() if int(mid) not in parent_ids]
            total_kwh = float(load_table("SELECT COALESCE(SUM(energy_kwh),0) AS e FROM readings WHERE meter_id IN (" + ",".join("?" for _ in leaf_ids) + ")", tuple(leaf_ids)).iloc[0]["e"]) if leaf_ids else 0.0
        alerts = load_table("SELECT * FROM alerts WHERE facility_id=?",(facility_id,))
        open_alerts = alerts[alerts["status"]=="OPEN"] if not alerts.empty else alerts
        estimated_cost = total_kwh*float(facility_row["tariff_inr"])
        a,b,c,d = st.columns(4)
        a.metric("Latest recorded load",f"{total_kw:,.2f} kW")
        b.metric("Recorded energy*",f"{total_kwh:,.1f} kWh")
        c.metric("Open alerts",f"{len(open_alerts)}")
        d.metric("Tariff estimate*",f"₹{estimated_cost:,.0f}")
        st.caption("*Energy totals sum meter records, so do not add main-meter and submeter energy together for a facility-wide bill. Tariff estimate is illustrative.")
        chart = latest.copy()
        chart["timestamp"] = pd.to_datetime(chart["timestamp"])
        series = load_table("SELECT r.timestamp,r.power_kw,m.meter_code,m.name FROM readings r JOIN meters m ON m.id=r.meter_id WHERE m.facility_id=? ORDER BY r.timestamp",(facility_id,))
        series["timestamp"] = pd.to_datetime(series["timestamp"])
        st.subheader("Power trend by meter")
        fig = px.line(series, x="timestamp", y="power_kw", color="meter_code", hover_name="name", template="plotly_dark")
        fig.update_layout(height=380, legend_title="Meter")
        st.plotly_chart(fig,use_container_width=True)
        st.subheader("Latest meter readings")
        st.dataframe(latest_slice[["meter_code","meter_name","zone_name","power_kw","source","quality"]],use_container_width=True,hide_index=True)

    with st.expander("Generate demo telemetry",expanded=latest.empty):
        days = st.slider("Days to simulate",1,7,2,key="dashboard_days")
        if st.button("Generate / refresh simulated readings",type="primary"):
            with st.spinner("Generating synthetic readings and checking anomalies..."):
                count = generate_demo_readings(facility_id,days=days)
            st.success(f"Generated {count:,} simulated meter readings.")
            st.rerun()

elif page == "Inventory & Mapping":
    st.title("Inventory & Meter Mapping")
    st.caption("Map each meter to a zone/circuit, then assign equipment to the meter that actually measures it.")
    zones = load_table("SELECT * FROM zones WHERE facility_id=? ORDER BY name",(facility_id,))
    meters = load_table("SELECT * FROM meters WHERE facility_id=? ORDER BY name",(facility_id,))
    apps = load_table("""SELECT a.*,z.name AS zone_name,c.name AS circuit_name,m.name AS meter_name,m.meter_code
                        FROM appliances a JOIN zones z ON a.zone_id=z.id LEFT JOIN circuits c ON a.circuit_id=c.id
                        LEFT JOIN meters m ON a.meter_id=m.id WHERE z.facility_id=? ORDER BY z.name,a.name""",(facility_id,))
    t1,t2,t3 = st.tabs(["Equipment inventory","Meters & circuits","Add records"])
    with t1:
        if apps.empty: st.info("No equipment inventory for this facility yet.")
        else:
            show = apps[["name","category","quantity","watts_each","standby_watts_each","zone_name","circuit_name","meter_code","meter_name","active"]].rename(columns={"name":"Equipment","category":"Category","quantity":"Qty","watts_each":"Rated W/unit","standby_watts_each":"Standby W/unit","zone_name":"Zone","circuit_name":"Circuit","meter_code":"Meter ID","meter_name":"Mapped meter","active":"Active"})
            st.dataframe(show,use_container_width=True,hide_index=True)
        if not apps.empty:
            st.subheader("Edit equipment-to-meter mapping")
            app_options = {f"{r['name']} — {r['zone_name']} (ID {r['id']})":int(r["id"]) for _,r in apps.iterrows()}
            selected_app_label = st.selectbox("Equipment",list(app_options))
            app_id = app_options[selected_app_label]
            app_row = apps[apps["id"]==app_id].iloc[0]
            meter_opts = {f"{r['meter_code']} — {r['name']}":int(r["id"]) for _,r in meters.iterrows()}
            current_meter = int(app_row["meter_id"]) if pd.notna(app_row["meter_id"]) else None
            meter_labels = list(meter_opts)
            default_index = next((i for i,k in enumerate(meter_labels) if meter_opts[k]==current_meter),0)
            new_meter_label = st.selectbox("Meter that measures this equipment",meter_labels,index=default_index)
            if st.button("Save meter mapping"):
                with connect() as con: con.execute("UPDATE appliances SET meter_id=? WHERE id=?",(meter_opts[new_meter_label],app_id))
                log_action("UPDATE_MAPPING",f"Equipment ID {app_id} mapped to meter ID {meter_opts[new_meter_label]}")
                st.success("Mapping saved. Regenerate demo readings to apply the mapping.")
                st.rerun()
    with t2:
        st.subheader("Meters")
        st.dataframe(meters[["meter_code","name","protocol","address","zone_id","circuit_id","parent_meter_id","active"]],use_container_width=True,hide_index=True)
        st.subheader("Circuits")
        circs = load_table("SELECT c.*,z.name AS zone_name FROM circuits c JOIN zones z ON c.zone_id=z.id WHERE z.facility_id=? ORDER BY z.name",(facility_id,))
        st.dataframe(circs[["zone_name","name","circuit_type","breaker_amps"]],use_container_width=True,hide_index=True)
        st.info("The main incomer measures the aggregate facility load. Submeters should measure distinct downstream circuits; don't sum main and submeter readings as if they were separate consumption.")
    with t3:
        st.subheader("Add a zone")
        with st.form("add_zone"):
            zname = st.text_input("Zone name")
            area = st.text_input("Building / floor / area")
            start_h = st.number_input("Operating start hour",0,23,8)
            end_h = st.number_input("Operating end hour",0,23,18)
            always = st.checkbox("Critical 24×7 zone")
            if st.form_submit_button("Add zone"):
                if not zname.strip(): st.error("Enter a zone name.")
                else:
                    try:
                        with connect() as con: con.execute("INSERT INTO zones(facility_id,name,area,start_hour,end_hour,always_on) VALUES(?,?,?,?,?,?)",(facility_id,zname.strip(),area,int(start_h),int(end_h),int(always)))
                        log_action("ADD_ZONE",zname.strip()); st.success("Zone added."); st.rerun()
                    except sqlite3.IntegrityError: st.error("A zone with that name already exists in this facility.")
        zones = load_table("SELECT * FROM zones WHERE facility_id=? ORDER BY name",(facility_id,))
        st.subheader("Add a circuit and meter")
        with st.form("add_circuit_meter"):
            zone_map = {r["name"]:int(r["id"]) for _,r in zones.iterrows()}
            zone_name = st.selectbox("Zone",list(zone_map),key="new_meter_zone")
            circuit_name = st.text_input("Circuit name")
            circuit_type = st.selectbox("Circuit type",["HVAC","Lighting","IT","General","Pumps","Machinery"])
            breaker = st.number_input("Breaker rating (A)",1.0,1000.0,32.0)
            meter_name = st.text_input("Meter name")
            meter_code = st.text_input("Unique meter ID / code",placeholder="e.g. M-LIB-02")
            protocol = st.selectbox("Data source / protocol",["SIMULATED","Modbus-TCP","Modbus-RTU","MQTT","HTTP API"])
            if st.form_submit_button("Create circuit + meter"):
                if not circuit_name.strip() or not meter_name.strip() or not meter_code.strip(): st.error("Fill in circuit, meter name and meter ID.")
                else:
                    try:
                        with connect() as con:
                            cid = con.execute("INSERT INTO circuits(zone_id,name,circuit_type,breaker_amps) VALUES(?,?,?,?)",(zone_map[zone_name],circuit_name.strip(),circuit_type,breaker)).lastrowid
                            con.execute("INSERT INTO meters(facility_id,name,meter_code,zone_id,circuit_id,protocol) VALUES(?,?,?,?,?,?)",(facility_id,meter_name.strip(),meter_code.strip(),zone_map[zone_name],cid,protocol))
                        log_action("ADD_METER",f"{meter_code.strip()} in {zone_name}/{circuit_name.strip()}")
                        st.success("Circuit and meter created. Add equipment and map it to this meter in the next iteration.")
                        st.rerun()
                    except sqlite3.IntegrityError: st.error("Meter ID must be unique, and circuit name must be unique within its zone.")
        st.subheader("Add equipment")
        meters_now = load_table("SELECT * FROM meters WHERE facility_id=? ORDER BY name",(facility_id,))
        circuits_now = load_table("SELECT c.*,z.name AS zone_name FROM circuits c JOIN zones z ON c.zone_id=z.id WHERE z.facility_id=? ORDER BY z.name,c.name",(facility_id,))
        with st.form("add_equipment"):
            zone_map2 = {r["name"]:int(r["id"]) for _,r in zones.iterrows()}
            zchoice = st.selectbox("Equipment zone",list(zone_map2),key="equipment_zone")
            possible_circuits = circuits_now[circuits_now["zone_id"]==zone_map2[zchoice]]
            circuit_map = {r["name"]:int(r["id"]) for _,r in possible_circuits.iterrows()}
            cchoice = st.selectbox("Equipment circuit",list(circuit_map) if circuit_map else ["No circuit"],key="equipment_circuit")
            meter_map = {f"{r['meter_code']} — {r['name']}":int(r["id"]) for _,r in meters_now.iterrows() if pd.notna(r["zone_id"]) and int(r["zone_id"])==zone_map2[zchoice]}
            if not meter_map: meter_map = {f"{r['meter_code']} — {r['name']}":int(r["id"]) for _,r in meters_now.iterrows()}
            mchoice = st.selectbox("Meter",list(meter_map),key="equipment_meter")
            ename = st.text_input("Equipment name")
            category = st.selectbox("Category",["HVAC","Lighting","IT","Motors","Pumps","General"])
            qty = st.number_input("Quantity",1,100000,1)
            watts = st.number_input("Rated watts per unit",1.0,1000000.0,100.0)
            standby = st.number_input("Standby watts per unit",0.0,1000000.0,5.0)
            duty = st.slider("Typical active duty factor",0.0,1.0,0.7,0.05)
            hours = st.number_input("Scheduled hours/day",0.0,24.0,8.0)
            if st.form_submit_button("Add equipment"):
                if not ename.strip(): st.error("Enter an equipment name.")
                else:
                    with connect() as con:
                        con.execute("INSERT INTO appliances(zone_id,circuit_id,meter_id,name,category,quantity,watts_each,standby_watts_each,duty_cycle,scheduled_hours) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                    (zone_map2[zchoice],circuit_map.get(cchoice),meter_map[mchoice],ename.strip(),category,int(qty),watts,standby,duty,hours))
                    log_action("ADD_EQUIPMENT",ename.strip()); st.success("Equipment added."); st.rerun()

elif page == "Waste Hunter":
    st.title("🔍 Waste Hunter")
    alerts = load_table("""SELECT a.*,m.name AS meter_name,m.meter_code,z.name AS zone_name,c.name AS circuit_name
                        FROM alerts a LEFT JOIN meters m ON a.meter_id=m.id LEFT JOIN zones z ON a.zone_id=z.id
                        LEFT JOIN circuits c ON a.circuit_id=c.id WHERE a.facility_id=? ORDER BY a.timestamp DESC""",(facility_id,))
    if alerts.empty:
        st.info("No alerts yet. Generate demo telemetry on the Dashboard first.")
    else:
        statuses = st.multiselect("Status",["OPEN","RESOLVED"],default=["OPEN"])
        severities = st.multiselect("Severity",["Moderate","High","Critical"],default=["Moderate","High","Critical"])
        filtered = alerts[alerts["status"].isin(statuses)&alerts["severity"].isin(severities)]
        a,b,c = st.columns(3)
        a.metric("Matching alerts",len(filtered)); b.metric("Estimated excess energy",f"{filtered['estimated_excess_kwh'].sum():.1f} kWh"); c.metric("Estimated excess cost",f"₹{filtered['estimated_cost_inr'].sum():,.0f}")
        st.caption("Estimates use appliance baselines and the configured tariff; investigate and verify before treating an alert as confirmed waste.")
        st.dataframe(filtered[["timestamp","severity","meter_code","meter_name","zone_name","circuit_name","rule","expected_kw","measured_kw","excess_kw","estimated_excess_kwh","estimated_cost_inr","explanation","recommendation","status"]],use_container_width=True,hide_index=True)
        alert_options = {f"Alert {r['id']} — {r['severity']} — {r['meter_code']} — {r['timestamp']}":int(r["id"]) for _,r in alerts[alerts["status"]=="OPEN"].iterrows()}
        if alert_options:
            choice = st.selectbox("Resolve alert",list(alert_options))
            if st.button("Mark selected alert resolved"):
                with connect() as con: con.execute("UPDATE alerts SET status='RESOLVED' WHERE id=?",(alert_options[choice],))
                log_action("RESOLVE_ALERT",choice); st.rerun()
        st.download_button("Export alerts CSV",filtered.to_csv(index=False).encode("utf-8"),"phantom_watt_alerts.csv","text/csv")

elif page == "Readings & Export":
    st.title("Readings & Export")
    readings = load_table("""SELECT r.timestamp,m.meter_code,m.name AS meter_name,z.name AS zone_name,c.name AS circuit_name,
                           r.power_kw,r.energy_kwh,r.source,r.quality FROM readings r JOIN meters m ON r.meter_id=m.id
                           LEFT JOIN zones z ON m.zone_id=z.id LEFT JOIN circuits c ON m.circuit_id=c.id
                           WHERE m.facility_id=? ORDER BY r.timestamp DESC""",(facility_id,))
    if readings.empty: st.info("No readings available. Generate demo telemetry from Dashboard.")
    else:
        st.dataframe(readings.head(1000),use_container_width=True,hide_index=True)
        st.download_button("Export readings CSV",readings.to_csv(index=False).encode("utf-8"),"phantom_watt_readings.csv","text/csv")

elif page == "Settings & Audit":
    st.title("Settings & Audit Log")
    st.subheader("Facility configuration")
    with st.form("facility_settings"):
        fname = st.text_input("Facility name",str(facility_row["name"]))
        ftype = st.selectbox("Facility type",["College","Hospital","Airport","Mall","Office","Industrial","Other"],index=["College","Hospital","Airport","Mall","Office","Industrial","Other"].index(facility_row["facility_type"]) if facility_row["facility_type"] in ["College","Hospital","Airport","Mall","Office","Industrial","Other"] else 0)
        tariff = st.number_input("Tariff (₹ per kWh)",0.0,1000.0,float(facility_row["tariff_inr"]),0.25)
        emissions = st.number_input("Grid emissions factor (kg CO₂/kWh)",0.0,5.0,float(facility_row["emission_factor"]),0.01)
        if st.form_submit_button("Save settings"):
            if not fname.strip(): st.error("Facility name cannot be empty.")
            else:
                try:
                    with connect() as con: con.execute("UPDATE facilities SET name=?,facility_type=?,tariff_inr=?,emission_factor=? WHERE id=?",(fname.strip(),ftype,tariff,emissions,facility_id))
                    log_action("UPDATE_FACILITY",f"Updated facility {facility_id}"); st.success("Settings saved."); st.rerun()
                except sqlite3.IntegrityError: st.error("Another facility already uses that name.")
    st.subheader("Create another facility")
    with st.form("new_facility"):
        new_name = st.text_input("New facility name")
        new_type = st.selectbox("New facility type",["College","Hospital","Airport","Mall","Office","Industrial","Other"],key="new_fac_type")
        new_tariff = st.number_input("New facility tariff (₹/kWh)",0.0,1000.0,8.5,0.25,key="new_tariff")
        if st.form_submit_button("Create facility"):
            if not new_name.strip(): st.error("Enter a facility name.")
            else:
                try:
                    with connect() as con: con.execute("INSERT INTO facilities(name,facility_type,tariff_inr) VALUES(?,?,?)",(new_name.strip(),new_type,new_tariff))
                    log_action("CREATE_FACILITY",new_name.strip()); st.success("Facility created. Add zones, circuits, meters and equipment in Inventory & Mapping."); st.rerun()
                except sqlite3.IntegrityError: st.error("A facility with that name already exists.")
    audit = load_table("SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 200")
    st.subheader("Recent audit events")
    if audit.empty: st.info("No audit events yet.")
    else: st.dataframe(audit,use_container_width=True,hide_index=True)

