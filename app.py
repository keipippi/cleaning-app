import random
import re
from collections import deque, defaultdict
from typing import Optional, List, Set, Dict, Deque, Tuple

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Lab Cleaning Scheduler", layout="wide")

st.title("🧹 Weekly Cleaning Scheduler")

st.markdown("""
- Vacuum / Mop / Garbage / Student Room は人数が多め（Vacuum, Mop=3、Garbage, Student Room=2）
- Water alcohol, Chip Tube, Autoclave Waste, Autoclave Drain, Drying Racks, Consumable Goods は各1人
- Liquid Waste は指定した週だけ+1人
- Kawakami / Kawano は Student Room から除外
- Unavailable に入れた人は完全に除外
- Monday-unavailable は Chip Tube / Autoclave Waste / Student Room / Consumable Goods を優先
- Friday-unavailable は Chip Tube / Autoclave Waste から除外
- 人数が足りないときは Vacuum→Mop→Drying Racks の順で自動で枠を減らす（Vacuum/Mop/Garbage/Student Room は最低2人）
""")


# =========================
# Constants
# =========================
DEFAULT_MEMBERS = [
    "Kawakami", "Kawano", "Yu", "Yong Sen", "Nia", "Shu",
    "Sarah", "Komiyama", "Sumi", "Takahashi", "Nishimiya"
]

DEFAULT_GROUP_A = ["Yu", "Yong Sen", "Nia", "Shu"]

DEFAULT_GROUP_B = ["Sarah", "Komiyama", "Sumi", "Takahashi", "Nishimiya"]

EXCLUDED_SR = {"Kawakami", "Kawano"}
MONDAY_PRIORITY_TASKS = {"Chip Tube", "Autoclave Waste", "Student Room", "Consumable Goods"}
FRIDAY_BLOCK_TASKS = {"Chip Tube", "Autoclave Waste"}

BASE_COUNTS = {
    "Vacuum": 3,
    "Mop": 3,
    "Garbage": 2,
    "Student Room": 2,
    "Chip Tube": 1,
    "Autoclave Waste": 1,
    "Autoclave Drain": 1,
    "Drying Racks": 1,
    "Water alcohol": 1,
    "Consumable Goods": 1,
}
ADJUST_MIN = {
    "Vacuum": 2,
    "Mop": 2,
    "Garbage": 2,
    "Student Room": 2,
    "Chip Tube": 1,
    "Autoclave Waste": 1,
    "Autoclave Drain": 1,
    "Drying Racks": 0,
    "Water alcohol": 1,
    "Consumable Goods": 1,
}
ORDERED_TASKS = [
    "Chip Tube",
    "Autoclave Waste",
    "Autoclave Drain",
    "Vacuum",
    "Mop",
    "Garbage",
    "Drying Racks",
    "Water alcohol",
    "Consumable Goods",
]


# =========================
# Helpers
# =========================
def normalize_multiline(text: str) -> str:
    parts = re.split(r"[,/\n]+", text)
    names = [p.strip() for p in parts if p.strip()]
    return "\n".join(names)


def parse_name_list(text: str) -> List[str]:
    return [m.strip() for m in normalize_multiline(text).splitlines() if m.strip()]


def parse_name_set(text: str) -> Set[str]:
    return set(parse_name_list(text))


def parse_liquid_weeks(text: str, max_weeks: int) -> Set[int]:
    result = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            week = int(token)
            if 1 <= week <= max_weeks:
                result.add(week)
        except ValueError:
            pass
    return result


def adapt_counts(available_count: int, has_liquid: bool) -> Tuple[Dict[str, int], List[str]]:
    counts = dict(BASE_COUNTS)
    warnings = []

    total_slots = sum(counts.values()) + (1 if has_liquid else 0)
    deficit = total_slots - available_count
    if deficit <= 0:
        return counts, warnings

    def reduce_slot(task_name: str) -> bool:
        nonlocal deficit
        if deficit > 0 and counts[task_name] > ADJUST_MIN[task_name]:
            counts[task_name] -= 1
            deficit -= 1
            return True
        return False

    while deficit > 0:
        changed = False

        changed = reduce_slot("Vacuum") or changed
        if deficit > 0:
            changed = reduce_slot("Mop") or changed

        if deficit <= 0:
            break

        if counts["Drying Racks"] > 0:
            counts["Drying Racks"] = 0
            deficit -= 1
            warnings.append("人数不足のため Drying Racks を 0 にしました。")
            changed = True

        if not changed:
            warnings.append("人数不足のため、いくつかの枠は未割当になります。")
            break

    return counts, warnings


def build_slots(counts: Dict[str, int], has_liquid: bool) -> List[str]:
    slots = []

    if counts["Student Room"] >= 2:
        slots.extend(["Student Room (A)", "Student Room (B)"])
    elif counts["Student Room"] == 1:
        slots.append("Student Room (A)")

    for task in ORDERED_TASKS:
        slots.extend([task] * counts[task])

    if has_liquid:
        slots.insert(0, "Liquid Waste")

    return slots


def pick_candidate(
    dq: Deque[str],
    used_this_week: Set[str],
    last_task: Dict[str, Optional[str]],
    task_name: str,
    required_group: Optional[Set[str]] = None,
    preferred: Optional[Set[str]] = None,
    blacklist: Optional[Set[str]] = None,
) -> Tuple[Optional[str], Deque[str]]:
    blacklist = blacklist or set()

    def try_pick(order: List[str], relax_same: bool = False) -> Tuple[Optional[str], Deque[str]]:
        tmp = deque(order)
        remaining_checks = len(tmp)

        while remaining_checks > 0:
            cand = tmp[0]
            tmp.rotate(-1)
            remaining_checks -= 1

            if cand in used_this_week:
                continue
            if cand in blacklist:
                continue
            if required_group and cand not in required_group:
                continue
            if (not relax_same) and last_task.get(cand) == task_name:
                continue

            return cand, tmp

        return None, dq

    candidate_orders = []

    if preferred:
        pref_list = [m for m in dq if m in preferred]
        rest_list = [m for m in dq if m not in preferred]
        candidate_orders.append(pref_list + rest_list)

    candidate_orders.append(list(dq))

    for order in candidate_orders:
        cand, newdq = try_pick(order, relax_same=False)
        if cand:
            return cand, newdq

    for order in candidate_orders:
        cand, newdq = try_pick(order, relax_same=True)
        if cand:
            return cand, newdq

    return None, dq


def assign_one_week(
    start_deque: Deque[str],
    last_task: Dict[str, Optional[str]],
    has_liquid: bool,
    counts: Dict[str, int],
    group_A_eff: Set[str],
    group_B_eff: Set[str],
    unavailable: Set[str],
    monday_unavail: Set[str],
    friday_unavail: Set[str],
) -> Tuple[Dict[str, List[str]], Deque[str], List[str]]:
    used = set()
    dq = deque(start_deque)
    assigned = defaultdict(list)
    unfilled = []

    slots = build_slots(counts, has_liquid)
    base_blacklist = set(unavailable)

    for slot in slots:
        if slot == "Student Room (A)":
            preferred = monday_unavail & group_A_eff
            cand, dq = pick_candidate(
                dq,
                used,
                last_task,
                "Student Room",
                required_group=group_A_eff,
                preferred=preferred if preferred else None,
                blacklist=base_blacklist | EXCLUDED_SR,
            )
            if not cand:
                unfilled.append(slot)
                continue
            assigned["Student Room"].append(cand)
            used.add(cand)
            continue

        if slot == "Student Room (B)":
            preferred = monday_unavail & group_B_eff
            cand, dq = pick_candidate(
                dq,
                used,
                last_task,
                "Student Room",
                required_group=group_B_eff,
                preferred=preferred if preferred else None,
                blacklist=base_blacklist | EXCLUDED_SR,
            )
            if not cand:
                unfilled.append(slot)
                continue
            assigned["Student Room"].append(cand)
            used.add(cand)
            continue

        preferred = monday_unavail if slot in MONDAY_PRIORITY_TASKS else None
        blacklist = set(base_blacklist)

        if slot in FRIDAY_BLOCK_TASKS:
            blacklist |= friday_unavail

        cand, dq = pick_candidate(
            dq,
            used,
            last_task,
            slot,
            preferred=preferred,
            blacklist=blacklist,
        )
        if not cand:
            unfilled.append(slot)
            continue

        assigned[slot].append(cand)
        used.add(cand)

    new_start = deque(start_deque)
    new_start.rotate(-5 if len(new_start) >= 6 else -1)

    return dict(assigned), new_start, unfilled


def build_schedule(
    members: List[str],
    unavailable: Set[str],
    monday_unavail: Set[str],
    friday_unavail: Set[str],
    weeks: int,
    liquid_weeks: Set[int],
    group_A_eff: Set[str],
    group_B_eff: Set[str],
) -> Tuple[List[str], List[Dict[str, List[str]]], List[Tuple[Dict[str, int], List[str], List[str]]]]:
    available_members = [m for m in members if m not in unavailable]
    if not available_members:
        st.error("参加可能なメンバーが0人です。")
        st.stop()

    dq_list = sorted(available_members)
    random.shuffle(dq_list)
    dq = deque(dq_list)

    last_task = {m: None for m in available_members}
    all_weeks = []
    info = []

    for week in range(1, weeks + 1):
        has_liquid = week in liquid_weeks
        counts, warns = adapt_counts(len(available_members), has_liquid)
        week_assign, dq, unfilled = assign_one_week(
            dq,
            last_task,
            has_liquid,
            counts,
            group_A_eff,
            group_B_eff,
            unavailable,
            monday_unavail,
            friday_unavail,
        )

        for task_name, people in week_assign.items():
            for person in people:
                last_task[person] = task_name

        all_weeks.append(week_assign)
        info.append((counts, warns, unfilled))

    return available_members, all_weeks, info


def join_names(value) -> str:
    return ", ".join(value) if isinstance(value, list) else (value or "-")


def make_schedule_dataframe(all_weeks: List[Dict[str, List[str]]], liquid_weeks: Set[int]) -> Tuple[pd.DataFrame, List[str]]:
    task_columns = [
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
    if liquid_weeks:
        task_columns = ["Liquid Waste"] + task_columns

    rows = []
    for i, week_assign in enumerate(all_weeks, start=1):
        row = {"Week": "Week {0}".format(i)}
        for col in task_columns:
            row[col] = join_names(week_assign.get(col, []))
        rows.append(row)

    return pd.DataFrame(rows), task_columns


def make_count_dataframe(df: pd.DataFrame, task_columns: List[str], available_members: List[str]) -> pd.DataFrame:
    counts = {m: 0 for m in available_members}
    for _, row in df.iterrows():
        for col in task_columns:
            names = row[col]
            if names and names != "-":
                for name in [x.strip() for x in names.split(",")]:
                    if name in counts:
                        counts[name] += 1
    return pd.DataFrame(sorted(counts.items()), columns=["Member", "Total Assignments"])


# =========================
# Sidebar
# =========================
with st.sidebar:
    st.header("⚙️ Settings")

    members_text_raw = st.text_area("Members (one per line)", value="\n".join(DEFAULT_MEMBERS), height=260)
    group_A_text_raw = st.text_area("Student Room Group A", value="\n".join(DEFAULT_GROUP_A), height=120)
    group_B_text_raw = st.text_area("Student Room Group B", value="\n".join(DEFAULT_GROUP_B), height=140)

    st.markdown("---")
    st.subheader("🚫 Unavailable members (全週参加不可)")
    unavail_raw = st.text_area("参加できないメンバー（1行1人）", value="", height=100)

    st.subheader("🗓 Monday-unavailable members")
    monday_raw = st.text_area("月曜に来れないメンバー（1行1人）", value="", height=100)

    st.subheader("🗓 Friday-unavailable members")
    friday_raw = st.text_area("金曜に来れないメンバー（1行1人）", value="", height=100)

    members = parse_name_list(members_text_raw)
    group_A = parse_name_set(group_A_text_raw)
    group_B = parse_name_set(group_B_text_raw)
    unavailable = parse_name_set(unavail_raw)
    monday_unavail = parse_name_set(monday_raw)
    friday_unavail = parse_name_set(friday_raw)

    weeks = st.number_input("Number of weeks", min_value=4, max_value=52, value=8)
    liquid_weeks_input = st.text_input("Liquid Waste weeks (例: 1,3,5)")
    liquid_weeks = parse_liquid_weeks(liquid_weeks_input, weeks)

    generate = st.button("🔁 Generate / Regenerate")


# =========================
# Validation
# =========================
if not group_A.isdisjoint(group_B):
    st.error("Group A と Group B は重複しないようにしてください。")
    st.stop()

group_A_eff = (group_A - unavailable) - EXCLUDED_SR
group_B_eff = (group_B - unavailable) - EXCLUDED_SR

if len(group_A_eff) < 1 or len(group_B_eff) < 1:
    st.error("Student Room のA/Bグループの有効メンバーが足りません。")
    st.stop()


# =========================
# Main
# =========================
if generate or "run_once" not in st.session_state:
    st.session_state["run_once"] = True

    available_members, all_weeks, weekly_info = build_schedule(
        members=members,
        unavailable=unavailable,
        monday_unavail=monday_unavail,
        friday_unavail=friday_unavail,
        weeks=weeks,
        liquid_weeks=liquid_weeks,
        group_A_eff=group_A_eff,
        group_B_eff=group_B_eff,
    )

    df, task_columns = make_schedule_dataframe(all_weeks, liquid_weeks)

    st.subheader("📅 Schedule")
    st.dataframe(df, use_container_width=True)

    df_cnt = make_count_dataframe(df, task_columns, available_members)
    st.subheader("📈 Assignment Counts")
    st.dataframe(df_cnt, use_container_width=True)

    with st.expander("ℹ️ Weekly adjustments & warnings"):
        for i, (cnt, warns, unfilled) in enumerate(weekly_info, start=1):
            st.markdown(
                "**Week {0}** — Vacuum {1}, Mop {2}, Garbage {3}, SR {4}, DR {5}".format(
                    i, cnt["Vacuum"], cnt["Mop"], cnt["Garbage"], cnt["Student Room"], cnt["Drying Racks"]
                )
            )
            for msg in warns:
                st.write("•", msg)
            if unfilled:
                st.write("未割当スロット:", ", ".join(unfilled))

    st.download_button(
        "⬇️ Download CSV",
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name="cleaning_schedule.csv",
        mime="text/csv",
    )
