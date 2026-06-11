import io
import json
import random
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque, Dict, List, Optional, Set, Tuple

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None

st.set_page_config(page_title="Lab Cleaning Scheduler", layout="wide")

LOCAL_CONFIG_PATH = Path("cleaning_config.json")
SHEET_TAB_NAME = "settings"

DEFAULT_MEMBERS = [
    "Kawakami", "Kawano", "Yu", "Yong Sen", "Nia", "Shu",
    "Sarah", "Felix", "Komiyama", "Sumi", "Takahashi", "Nishimiya",
]
DEFAULT_GROUP_A = ["Yu", "Yong Sen", "Nia", "Shu"]
DEFAULT_GROUP_B = ["Sarah", "Felix", "Komiyama", "Sumi", "Takahashi", "Nishimiya"]

EXCLUDED_SR = {"Kawakami", "Kawano"}
MONDAY_PRIORITY_TASKS = {"Chip Tube", "Autoclave Waste", "Student Room", "Consumable Goods"}
FRIDAY_BLOCK_TASKS = {"Chip Tube", "Autoclave Waste"}

BASE_COUNTS = {
    "Vacuum": 2,
    "Mop": 2,
    "Garbage": 2,
    "Student Room": 2,
    "Chip Tube": 1,
    "Autoclave Waste": 1,
    "Autoclave Drain": 1,
    "Drying Racks": 1,
    "Water alcohol": 1,
    "Consumable Goods": 1,
}

MIN_COUNTS = {
    "Vacuum": 1,
    "Mop": 1,
    "Garbage": 1,
    "Student Room": 2,
    "Chip Tube": 1,
    "Autoclave Waste": 1,
    "Autoclave Drain": 1,
    "Drying Racks": 0,
    "Water alcohol": 1,
    "Consumable Goods": 1,
}

MAX_COUNTS = {
    "Vacuum": 3,
    "Mop": 3,
    "Garbage": 2,
    "Student Room": 2,
    "Chip Tube": 3,
    "Autoclave Waste": 1,
    "Autoclave Drain": 1,
    "Drying Racks": 1,
    "Water alcohol": 3,
    "Consumable Goods": 1,
}

REDUCE_ORDER = [
    ("Garbage", 1),
    ("Vacuum", 1),
    ("Mop", 1),
    ("Drying Racks", 0),
]

INCREASE_ORDER = [
    ("Chip Tube", 2),
    ("Vacuum", 3),
    ("Mop", 3),
    ("Water alcohol", 2),
    ("Chip Tube", 3),
    ("Water alcohol", 3),
]

TASK_ORDER = [
    "Chip Tube",
    "Autoclave Waste",
    "Autoclave Drain",
    "Vacuum",
    "Mop",
    "Garbage",
    "Student Room",
    "Drying Racks",
    "Water alcohol",
    "Consumable Goods",
]


def normalize_multiline(text: str) -> str:
    parts = re.split(r"[,/\n、]+", text)
    names = [p.strip() for p in parts if p.strip()]
    return "\n".join(names)


def parse_name_list(text: str) -> List[str]:
    return [m.strip() for m in normalize_multiline(text).splitlines() if m.strip()]


def parse_name_set(text: str) -> Set[str]:
    return set(parse_name_list(text))


def unique_keep_order(names: List[str]) -> List[str]:
    seen = set()
    result = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def default_config() -> Dict[str, List[str]]:
    return {"members": DEFAULT_MEMBERS, "group_A": DEFAULT_GROUP_A, "group_B": DEFAULT_GROUP_B}


def get_service_account_info() -> Optional[dict]:
    if "GOOGLE_SERVICE_ACCOUNT_JSON" in st.secrets:
        raw = st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"]
        if isinstance(raw, str):
            return json.loads(raw)
        return dict(raw)
    if "gcp_service_account" in st.secrets:
        info = dict(st.secrets["gcp_service_account"])
        if "private_key" in info and isinstance(info["private_key"], str):
            info["private_key"] = info["private_key"].replace("\\n", "\n")
        return info
    return None


def sheet_is_configured() -> bool:
    return (
        gspread is not None
        and Credentials is not None
        and "SPREADSHEET_NAME" in st.secrets
        and get_service_account_info() is not None
    )


def get_worksheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    service_account_info = get_service_account_info()
    if service_account_info is None:
        raise RuntimeError("Google service account secret is not configured.")
    creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
    client = gspread.authorize(creds)
    spreadsheet = client.open(st.secrets["SPREADSHEET_NAME"])
    try:
        worksheet = spreadsheet.worksheet(SHEET_TAB_NAME)
    except Exception:
        worksheet = spreadsheet.add_worksheet(title=SHEET_TAB_NAME, rows=100, cols=2)
        worksheet.update("A1:B1", [["type", "name"]])
    return worksheet


def load_config_from_sheet() -> Optional[Dict[str, List[str]]]:
    if not sheet_is_configured():
        return None
    try:
        worksheet = get_worksheet()
        records = worksheet.get_all_records()
        members = [str(r.get("name", "")).strip() for r in records if r.get("type") == "members"]
        group_A = [str(r.get("name", "")).strip() for r in records if r.get("type") == "group_A"]
        group_B = [str(r.get("name", "")).strip() for r in records if r.get("type") == "group_B"]
        members = unique_keep_order([m for m in members if m])
        group_A = unique_keep_order([m for m in group_A if m])
        group_B = unique_keep_order([m for m in group_B if m])
        if members and group_A and group_B:
            return {"members": members, "group_A": group_A, "group_B": group_B}
    except Exception as e:
        st.warning("Googleスプレッドシートから設定を読み込めませんでした。ローカル/初期設定を使います。")
        st.caption(str(e))
    return None


def save_config_to_sheet(members: List[str], group_A: List[str], group_B: List[str]) -> bool:
    if not sheet_is_configured():
        return False
    try:
        worksheet = get_worksheet()
        rows = [["type", "name"]]
        rows += [["members", name] for name in members]
        rows += [["group_A", name] for name in group_A]
        rows += [["group_B", name] for name in group_B]
        worksheet.clear()
        worksheet.update("A1:B{}".format(len(rows)), rows)
        return True
    except Exception as e:
        st.warning("Googleスプレッドシートへの保存に失敗しました。ローカル保存を試します。")
        st.caption(str(e))
        return False


def load_config_from_local() -> Optional[Dict[str, List[str]]]:
    if not LOCAL_CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
        if data.get("members") and data.get("group_A") and data.get("group_B"):
            return data
    except Exception:
        return None
    return None


def save_config_to_local(members: List[str], group_A: List[str], group_B: List[str]) -> bool:
    try:
        data = {"members": members, "group_A": group_A, "group_B": group_B}
        LOCAL_CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        st.warning("ローカル保存に失敗しました。")
        st.caption(str(e))
        return False


def load_config() -> Dict[str, List[str]]:
    return load_config_from_sheet() or load_config_from_local() or default_config()


def save_config(members: List[str], group_A: List[str], group_B: List[str]) -> str:
    if save_config_to_sheet(members, group_A, group_B):
        return "Googleスプレッドシートに設定を保存しました。"
    if save_config_to_local(members, group_A, group_B):
        return "ローカルに設定を保存しました。Google Sheets Secretsを設定すると、サイト再起動後もより安定して保存できます。"
    return "設定保存に失敗しました。"


def adapt_counts(available_count: int, has_liquid: bool) -> Tuple[Dict[str, int], List[str]]:
    counts = dict(BASE_COUNTS)
    warnings = []
    if has_liquid:
        counts["Liquid Waste"] = 1

    total_slots = sum(counts.values())
    deficit = total_slots - available_count
    for task, min_value in REDUCE_ORDER:
        while deficit > 0 and counts.get(task, 0) > min_value:
            counts[task] -= 1
            deficit -= 1
    if deficit > 0:
        warnings.append("人数不足のため、いくつかの枠は未割当になります。")
        return counts, warnings

    extra = available_count - sum(counts.values())
    for task, target_value in INCREASE_ORDER:
        while extra > 0 and counts.get(task, 0) < target_value and counts.get(task, 0) < MAX_COUNTS[task]:
            counts[task] += 1
            extra -= 1
    if extra > 0:
        warnings.append("人数が多いため、割り当てなしのメンバーがいます。")
    return counts, warnings


def build_slots(counts: Dict[str, int], has_liquid: bool) -> List[str]:
    slots = ["Student Room (A)", "Student Room (B)"]
    for task in TASK_ORDER:
        if task == "Student Room":
            continue
        slots.extend([task] * counts.get(task, 0))
    if has_liquid:
        slots.insert(0, "Liquid Waste")
    return slots


def pick_candidate(
    dq: Deque[str],
    used_this_round: Set[str],
    task_name: str,
    required_group: Optional[Set[str]] = None,
    preferred: Optional[Set[str]] = None,
    blacklist: Optional[Set[str]] = None,
) -> Tuple[Optional[str], Deque[str]]:
    blacklist = blacklist or set()
    candidate_orders = []
    if preferred:
        pref_list = [m for m in dq if m in preferred]
        rest_list = [m for m in dq if m not in preferred]
        candidate_orders.append(pref_list + rest_list)
    candidate_orders.append(list(dq))
    for order in candidate_orders:
        tmp = deque(order)
        for _ in range(len(tmp)):
            cand = tmp[0]
            tmp.rotate(-1)
            if cand in used_this_round:
                continue
            if cand in blacklist:
                continue
            if required_group and cand not in required_group:
                continue
            return cand, tmp
    return None, dq


def assign_schedule(
    members: List[str],
    unavailable: Set[str],
    monday_unavail: Set[str],
    friday_unavail: Set[str],
    group_A_eff: Set[str],
    group_B_eff: Set[str],
    has_liquid: bool,
) -> Tuple[Dict[str, List[str]], Dict[str, int], List[str], List[str], List[str]]:
    available_members = [m for m in members if m not in unavailable]
    if not available_members:
        st.error("参加可能なメンバーが0人です。")
        st.stop()
    counts, warnings = adapt_counts(len(available_members), has_liquid)
    slots = build_slots(counts, has_liquid)
    dq_list = sorted(available_members)
    random.shuffle(dq_list)
    dq = deque(dq_list)
    assigned = defaultdict(list)
    used = set()
    unfilled = []
    base_blacklist = set(unavailable)
    for slot in slots:
        if slot == "Student Room (A)":
            preferred = monday_unavail & group_A_eff
            cand, dq = pick_candidate(dq, used, "Student Room", required_group=group_A_eff, preferred=preferred if preferred else None, blacklist=base_blacklist | EXCLUDED_SR)
            if cand:
                assigned["Student Room"].append(cand)
                used.add(cand)
            else:
                unfilled.append(slot)
            continue
        if slot == "Student Room (B)":
            preferred = monday_unavail & group_B_eff
            cand, dq = pick_candidate(dq, used, "Student Room", required_group=group_B_eff, preferred=preferred if preferred else None, blacklist=base_blacklist | EXCLUDED_SR)
            if cand:
                assigned["Student Room"].append(cand)
                used.add(cand)
            else:
                unfilled.append(slot)
            continue
        preferred = monday_unavail if slot in MONDAY_PRIORITY_TASKS else None
        blacklist = set(base_blacklist)
        if slot in FRIDAY_BLOCK_TASKS:
            blacklist |= friday_unavail
        cand, dq = pick_candidate(dq, used, slot, preferred=preferred, blacklist=blacklist)
        if cand:
            assigned[slot].append(cand)
            used.add(cand)
        else:
            unfilled.append(slot)
    return dict(assigned), counts, warnings, unfilled, available_members


def make_schedule_dataframe(assigned: Dict[str, List[str]], has_liquid: bool) -> pd.DataFrame:
    tasks = TASK_ORDER.copy()
    if has_liquid:
        tasks = ["Liquid Waste"] + tasks
    rows = []
    for task in tasks:
        people = assigned.get(task, [])
        rows.append({"Cleaning Area": task, "Member": ", ".join(people) if people else "-"})
    return pd.DataFrame(rows)


def make_png_bytes(df: pd.DataFrame) -> bytes:
    height = max(4.0, 0.48 * len(df) + 1.2)
    fig, ax = plt.subplots(figsize=(8.5, height))
    ax.axis("off")
    ax.set_title("Cleaning Duty", fontsize=18, pad=16)
    table = ax.table(cellText=df.values, colLabels=df.columns, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(12)
    table.scale(1, 1.55)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=220, bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return buffer.getvalue()


def apply_base_style():
    st.markdown(
        """
        <style>
        header[data-testid="stHeader"] { height: 3.2rem; }
        .block-container { padding-top: 5.2rem !important; padding-bottom: 4rem !important; }
        h1 { line-height: 1.18 !important; margin-top: 0.4rem !important; margin-bottom: 1rem !important; overflow: visible !important; }
        textarea { line-height: 1.45 !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def apply_mobile_style():
    st.markdown(
        """
        <style>
        .block-container { padding-left: 0.8rem !important; padding-right: 0.8rem !important; max-width: 760px; }
        textarea, input, button { font-size: 16px !important; }
        .stButton > button, .stDownloadButton > button { width: 100%; border-radius: 0.8rem; min-height: 3rem; }
        div[data-testid="stDataFrame"] { font-size: 14px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def apply_pc_style():
    st.markdown(
        """
        <style>
        .block-container { max-width: 1200px; }
        .stButton > button, .stDownloadButton > button { border-radius: 0.7rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


apply_base_style()
config = load_config()

st.title("🧹 Cleaning Duty Scheduler")
st.caption("毎週木曜日に当番を作成し、その週の金曜日・翌週月曜日に掃除する想定の1回分作成アプリです。")

view_mode = st.radio("表示モード", ["スマホ版", "パソコン版"], horizontal=True)
if view_mode == "スマホ版":
    apply_mobile_style()
else:
    apply_pc_style()

if sheet_is_configured():
    st.success("Googleスプレッドシート連携：設定あり")
else:
    st.info("Googleスプレッドシート連携：未設定。設定保存はローカル保存になります。")

if "last_result" not in st.session_state:
    st.session_state["last_result"] = None


def input_area():
    members_raw = st.text_area("Members", value="\n".join(config["members"]), height=220)
    group_A_raw = st.text_area("Student Room Group A", value="\n".join(config["group_A"]), height=120)
    group_B_raw = st.text_area("Student Room Group B", value="\n".join(config["group_B"]), height=120)
    unavailable_raw = st.text_area("Unavailable members（完全に除外）", value="", height=95)
    monday_raw = st.text_area("Monday-unavailable members（月曜に来れない）", value="", height=95)
    friday_raw = st.text_area("Friday-unavailable members（金曜に来れない）", value="", height=95)
    has_liquid = st.checkbox("Liquid Waste を追加する", value=False)
    return members_raw, group_A_raw, group_B_raw, unavailable_raw, monday_raw, friday_raw, has_liquid


if view_mode == "パソコン版":
    col_left, col_right = st.columns([1.1, 0.9])
    with col_left:
        members_raw = st.text_area("Members", value="\n".join(config["members"]), height=300)
        group_A_raw = st.text_area("Student Room Group A", value="\n".join(config["group_A"]), height=130)
        group_B_raw = st.text_area("Student Room Group B", value="\n".join(config["group_B"]), height=130)
    with col_right:
        unavailable_raw = st.text_area("Unavailable members（完全に除外）", value="", height=110)
        monday_raw = st.text_area("Monday-unavailable members（月曜に来れない）", value="", height=110)
        friday_raw = st.text_area("Friday-unavailable members（金曜に来れない）", value="", height=110)
        has_liquid = st.checkbox("Liquid Waste を追加する", value=False)
else:
    members_raw, group_A_raw, group_B_raw, unavailable_raw, monday_raw, friday_raw, has_liquid = input_area()

members = unique_keep_order(parse_name_list(members_raw))
group_A_list = unique_keep_order(parse_name_list(group_A_raw))
group_B_list = unique_keep_order(parse_name_list(group_B_raw))
group_A = set(group_A_list)
group_B = set(group_B_list)
unavailable = parse_name_set(unavailable_raw)
monday_unavail = parse_name_set(monday_raw)
friday_unavail = parse_name_set(friday_raw)

if not group_A.isdisjoint(group_B):
    st.error("Group A と Group B は重複しないようにしてください。")
    st.stop()

group_A_eff = (group_A - unavailable) - EXCLUDED_SR
group_B_eff = (group_B - unavailable) - EXCLUDED_SR

if len(group_A_eff) < 1 or len(group_B_eff) < 1:
    st.error("Student Room のA/Bグループの有効メンバーが足りません。")
    st.stop()

if view_mode == "パソコン版":
    col_btn1, col_btn2 = st.columns([1, 1])
    with col_btn1:
        save_only = st.button("💾 メンバー設定だけ保存")
    with col_btn2:
        generate = st.button("🔁 掃除当番を作成 / 再作成")
else:
    save_only = st.button("💾 メンバー設定だけ保存")
    generate = st.button("🔁 掃除当番を作成 / 再作成")

if save_only:
    msg = save_config(members, group_A_list, group_B_list)
    st.success(msg)

if generate:
    msg = save_config(members, group_A_list, group_B_list)
    assigned, counts, warnings, unfilled, available_members = assign_schedule(
        members=members,
        unavailable=unavailable,
        monday_unavail=monday_unavail,
        friday_unavail=friday_unavail,
        group_A_eff=group_A_eff,
        group_B_eff=group_B_eff,
        has_liquid=has_liquid,
    )
    df = make_schedule_dataframe(assigned, has_liquid)
    png_bytes = make_png_bytes(df)
    st.session_state["last_result"] = {
        "df": df,
        "png_bytes": png_bytes,
        "counts": counts,
        "warnings": warnings,
        "unfilled": unfilled,
        "available_members": available_members,
        "save_msg": msg,
    }

if st.session_state["last_result"] is not None:
    result = st.session_state["last_result"]
    st.success("掃除当番を作成しました。" + " " + result["save_msg"])
    st.subheader("📅 Cleaning Duty")
    st.dataframe(result["df"], use_container_width=True, hide_index=True)
    st.download_button(
        "📷 画像として保存（PNG）",
        data=result["png_bytes"],
        file_name="cleaning_schedule.png",
        mime="image/png",
    )
    with st.expander("ℹ️ 調整・未割当情報"):
        counts = result["counts"]
        st.write(
            "Vacuum: {0}, Mop: {1}, Garbage: {2}, Student Room: {3}, Drying Racks: {4}, Chip Tube: {5}, Water alcohol: {6}".format(
                counts.get("Vacuum", 0),
                counts.get("Mop", 0),
                counts.get("Garbage", 0),
                counts.get("Student Room", 0),
                counts.get("Drying Racks", 0),
                counts.get("Chip Tube", 0),
                counts.get("Water alcohol", 0),
            )
        )
        for msg in result["warnings"]:
            st.write("•", msg)
        if result["unfilled"]:
            st.write("未割当:", ", ".join(result["unfilled"]))
        else:
            st.write("未割当なし")
        st.write("参加可能メンバー:", ", ".join(result["available_members"]))
