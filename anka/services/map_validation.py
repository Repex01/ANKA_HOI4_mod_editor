"""Map integrity validation (problems panel of the map editor).

Checks the invariants listed in MAP_EDITOR_TODO: definition.csv ↔ provinces.bmp
orphans, unique ids/colors, state membership (land in exactly one state, no
sea/lake in states), VP and building sanity, strategic-region and supply-area
coverage. Returns typed issues the UI maps to localized texts
(``map.issue.<code>``) and to a clickable map/tree target.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MapIssue:
    severity: str          # "error" | "warning"
    code: str              # locale suffix: map.issue.<code>
    subject: str           # display label (province/state id ...)
    detail: dict = field(default_factory=dict)
    target_kind: str = ""  # "province" | "state" | ""
    target_id: int = 0


def validate(map_service, state_service, building_service,
             strategic_regions=None, supply_areas=None) -> list[MapIssue]:
    issues: list[MapIssue] = []
    map_service.ensure_bitmap()
    defs = map_service.defs

    # --- definition.csv self-consistency -------------------------------------
    seen_ids: set[int] = set()
    seen_colors: dict[tuple[int, int, int], int] = {}
    for d in defs:
        if d.id in seen_ids:
            issues.append(MapIssue("error", "dup_id", f"#{d.id}",
                                   {"id": d.id}, "province", d.id))
        seen_ids.add(d.id)
        if d.id == 0:
            continue
        color = (d.r, d.g, d.b)
        if color in seen_colors:
            issues.append(MapIssue("error", "dup_color", f"#{d.id}",
                                   {"id": d.id, "other": seen_colors[color]},
                                   "province", d.id))
        seen_colors[color] = d.id

    # --- bmp ↔ csv orphans -----------------------------------------------------
    # NOT `map_service._uidx`: the dense index keeps stale entries after undo.
    present_codes = map_service.present_codes()
    def_codes = {d.code for d in defs if d.id != 0}
    for code in sorted(present_codes - def_codes - {0}):
        r, g, b = (code >> 16) & 255, (code >> 8) & 255, code & 255
        issues.append(MapIssue("error", "orphan_color", f"RGB {r},{g},{b}",
                               {"r": r, "g": g, "b": b}))
    ids_with_pixels = {d.id for d in defs if d.id != 0 and d.code in present_codes}
    for d in defs:
        if d.id != 0 and d.id not in ids_with_pixels:
            issues.append(MapIssue("warning", "no_pixels", f"#{d.id}",
                                   {"id": d.id}, "province", d.id))

    # --- state membership -------------------------------------------------------
    states = state_service.list_states()
    # Contiguous state numbering: HOI4 indexes states by id and crashes on holes.
    used_ids = {st.id for st in states}
    if used_ids:
        gaps = sorted(set(range(1, max(used_ids) + 1)) - used_ids)
        if gaps:
            shown = ", ".join(map(str, gaps[:15])) + ("…" if len(gaps) > 15 else "")
            issues.append(MapIssue("error", "id_gap", f"{len(gaps)}",
                                   {"count": len(gaps), "ids": shown}))
    # A state without provinces is invalid in HOI4 — usually a merge leftover.
    for st in states:
        if not st.provinces:
            issues.append(MapIssue("error", "empty_state", f"{st.id}",
                                   {"state": st.id}, "state", st.id))
    prov_owner: dict[int, int] = {}
    for st in states:
        for p in st.provinces:
            if p in prov_owner:
                issues.append(MapIssue("error", "two_states", f"#{p}",
                                       {"id": p, "a": prov_owner[p], "b": st.id},
                                       "province", p))
            else:
                prov_owner[p] = st.id
            d = map_service.by_id.get(p)
            if d is None:
                issues.append(MapIssue("error", "state_unknown_province",
                                       f"{st.id}", {"state": st.id, "id": p},
                                       "state", st.id))
            elif d.type == "sea":
                # Lakes DO occur inside vanilla states (118 of them, e.g.
                # IJsselmeer in Friesland) — only sea provinces are an error.
                issues.append(MapIssue("error", "sea_in_state", f"#{p}",
                                       {"id": p, "state": st.id},
                                       "province", p))
    for d in defs:
        if (d.type == "land" and d.id != 0 and d.id in ids_with_pixels
                and d.id not in prov_owner):
            issues.append(MapIssue("error", "land_no_state", f"#{d.id}",
                                   {"id": d.id}, "province", d.id))

    # --- per-state document checks (VP, buildings) -------------------------------
    buildings = building_service.buildings()
    for ref in state_service.list_docs(include_vanilla=True):
        try:
            doc = state_service.load(ref)
        except Exception:
            continue
        st = doc.state
        if st is None:
            continue
        provinces = set(st.provinces)
        seen_vp: set[int] = set()
        for prov, _val in st.victory_points:
            if prov in seen_vp:
                issues.append(MapIssue("warning", "dup_vp", f"{st.id}",
                                       {"state": st.id, "id": prov},
                                       "state", st.id))
            seen_vp.add(prov)
            if prov not in provinces:
                issues.append(MapIssue("error", "vp_foreign", f"{st.id}",
                                       {"state": st.id, "id": prov},
                                       "state", st.id))
        for name, level in st.state_buildings.items():
            bdef = buildings.get(name)
            if bdef is None:
                issues.append(MapIssue("warning", "unknown_building", f"{st.id}",
                                       {"state": st.id, "name": name},
                                       "state", st.id))
            elif level > bdef.max_level:
                issues.append(MapIssue("warning", "building_over_max", f"{st.id}",
                                       {"state": st.id, "name": name,
                                        "level": level, "max": bdef.max_level},
                                       "state", st.id))
        for prov, blds in st.province_buildings.items():
            if prov not in provinces:
                issues.append(MapIssue("error", "pb_foreign", f"{st.id}",
                                       {"state": st.id, "id": prov},
                                       "state", st.id))
            d = map_service.by_id.get(prov)
            for name, level in blds.items():
                bdef = buildings.get(name)
                if bdef is None:
                    issues.append(MapIssue("warning", "unknown_building",
                                           f"{st.id}",
                                           {"state": st.id, "name": name},
                                           "state", st.id))
                    continue
                if level > bdef.max_level:
                    issues.append(MapIssue("warning", "building_over_max",
                                           f"{st.id}",
                                           {"state": st.id, "name": name,
                                            "level": level,
                                            "max": bdef.max_level},
                                           "state", st.id))
                if bdef.only_coastal and d is not None and not d.coastal:
                    issues.append(MapIssue("warning", "coastal_building",
                                           f"#{prov}",
                                           {"id": prov, "name": name},
                                           "province", prov))

    # --- strategic regions ---------------------------------------------------------
    if strategic_regions is not None:
        region_of = strategic_regions.member_map()
        counted: dict[int, int] = {}
        for ref in strategic_regions.list_docs():
            try:
                doc = strategic_regions.load(ref)
            except Exception:
                continue
            for p in doc.members:
                counted[p] = counted.get(p, 0) + 1
        for d in defs:
            if d.id == 0 or d.id not in ids_with_pixels:
                continue
            if d.id not in region_of:
                issues.append(MapIssue("warning", "no_strategic_region",
                                       f"#{d.id}", {"id": d.id},
                                       "province", d.id))
            elif counted.get(d.id, 0) > 1:
                issues.append(MapIssue("error", "two_strategic_regions",
                                       f"#{d.id}", {"id": d.id},
                                       "province", d.id))

    # --- supply areas -----------------------------------------------------------------
    # Supply areas are legacy since the 1.11 logistics rework — vanilla keeps a
    # single remnant file. Coverage is only meaningful when the mod actually
    # uses the system (2+ areas); duplicate membership is always wrong.
    if supply_areas is not None:
        area_refs = supply_areas.list_docs()
        counted = {}
        for ref in area_refs:
            try:
                doc = supply_areas.load(ref)
            except Exception:
                continue
            for s in doc.members:
                counted[s] = counted.get(s, 0) + 1
        check_coverage = len(area_refs) > 1
        for st in states:
            if counted.get(st.id, 0) > 1:
                issues.append(MapIssue("error", "two_supply_areas", f"{st.id}",
                                       {"state": st.id}, "state", st.id))
            elif check_coverage and st.id not in counted:
                issues.append(MapIssue("warning", "no_supply_area", f"{st.id}",
                                       {"state": st.id}, "state", st.id))

    order = {"error": 0, "warning": 1}
    issues.sort(key=lambda i: (order.get(i.severity, 2), i.code, i.subject))
    return issues
