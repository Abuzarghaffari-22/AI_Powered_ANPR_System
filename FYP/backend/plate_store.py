import logging
import re
import threading
from typing import Optional

logger = logging.getLogger("anpr.plate_store")

_lock          = threading.RLock()
_by_normalized: dict[str, dict] = {}
_by_stripped  : dict[str, dict] = {}
_by_length    : dict[int, list] = {}   # length -> list of (stripped_key, row)
_by_prefix    : dict[str, list] = {}   # 4-7 char prefix -> list of (stripped_key, row)
_by_suffix    : dict[str, list] = {}   # 4-6 digit suffix -> list of (stripped_key, row)
_loaded        = False

_RE_DASH = re.compile(r"[-\s]")

# bidirectional OCR confusion map — used only at lookup time, never rewrites stored plates
_CONFUSION: dict[str, tuple[str, ...]] = {
    "0": ("O", "D", "Q"), "O": ("0", "D", "Q"), "D": ("0", "O"), "Q": ("0", "O"),
    "8": ("B",),          "B": ("8",),
    # 3 and 5 are visually similar in many Pakistani plate fonts (serifs blur them)
    "5": ("S", "3"),      "S": ("5",),
    "1": ("I", "L"),      "I": ("1", "L"),      "L": ("1", "I"),
    "6": ("G", "0"),      "G": ("6", "0"),
    "2": ("Z",),          "Z": ("2",),
    "9": ("3", "5"),      "3": ("9", "8", "5"),
    "F": ("P", "E"),      "P": ("F", "R"),      "E": ("F",),  "R": ("P",),
    "J": ("I",),          "W": ("V",),          "V": ("W", "Y"),  "Y": ("V",),

    "4": ("A",),          "A": ("4",),
    "N": ("M", "H"),      "M": ("N",),          "H": ("N",),
    "T": ("I",),          "C": ("G",),          "U": ("V", "W"),
    "X": ("K",),          "K": ("X",),
}


def _strip(plate: str) -> str:
    return _RE_DASH.sub("", plate).upper()


def _auth_status(row: dict) -> str:
    raw = row.get("is_authorized", 0)
    if isinstance(raw, (bytes, bytearray)):
        raw = int.from_bytes(raw, "little")
    if bool(int(raw) if not isinstance(raw, bool) else raw):
        return "authorized"
    dues   = str(row.get("dues",   "") or "").strip().lower()
    status = str(row.get("status", "") or "").strip().lower()
    if dues in ("clear", "paid", "", "none") and status in ("authorized", "active"):
        return "authorized"
    return "unauthorized"


def load(conn) -> int:
    global _loaded
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM vehicles")
        rows = cur.fetchall()
    finally:
        cur.close()

    new_norm:   dict[str, dict]       = {}
    new_strip:  dict[str, dict]       = {}
    new_length: dict[int, list]       = {}
    new_prefix: dict[str, list]       = {}
    new_suffix: dict[str, list]       = {}
    for row in rows:
        norm = str(row.get("license_normalized") or "").strip().upper()
        if not norm:
            continue
        stripped = _strip(norm)
        if "license_stripped" not in row or not row["license_stripped"]:
            row = dict(row)
            row["license_stripped"] = stripped
        new_norm[norm]      = row
        new_strip[stripped] = row
        bucket = new_length.setdefault(len(stripped), [])
        bucket.append((stripped, row))
        for plen in range(4, min(8, len(stripped) + 1)):
            pb = new_prefix.setdefault(stripped[:plen], [])
            pb.append((stripped, row))
        digits = re.sub(r'[^0-9]', '', stripped)
        for slen in range(4, min(7, len(digits) + 1)):
            for start in range(len(digits) - slen + 1):
                sub = digits[start:start + slen]
                sb = new_suffix.setdefault(sub, [])
                sb.append((stripped, row))

    with _lock:
        _by_normalized.clear()
        _by_normalized.update(new_norm)
        _by_stripped.clear()
        _by_stripped.update(new_strip)
        _by_length.clear()
        _by_length.update(new_length)
        _by_prefix.clear()
        _by_prefix.update(new_prefix)
        _by_suffix.clear()
        _by_suffix.update(new_suffix)
        _loaded = True

    logger.info(f"PlateStore loaded {len(new_norm)} vehicles into RAM")
    return len(new_norm)


def _confusion_candidates(stripped_query: str) -> list[str]:
    """single-char confusion variants of query"""
    out: set[str] = set()
    for i, ch in enumerate(stripped_query):
        for alt in _CONFUSION.get(ch, ()):
            out.add(stripped_query[:i] + alt + stripped_query[i + 1:])
    return list(out)


def _confusion_candidates_double(stripped_query: str) -> list[str]:
    """two-char confusion variants — last resort, bounded combinatorial"""
    single = _confusion_candidates(stripped_query)
    out: set[str] = set()
    for s in single:
        for i, ch in enumerate(s):
            for alt in _CONFUSION.get(ch, ()):
                out.add(s[:i] + alt + s[i + 1:])
    out.discard(stripped_query)
    for s in single:
        out.discard(s)
    return list(out)


def lookup(plate: str) -> tuple[Optional[dict], str, str]:
    if not plate or len(plate) < 5:
        return None, "unauthorized", "no_text"

    plate_up = plate.strip().upper()

    with _lock:
        row = _by_normalized.get(plate_up)
        if row is not None:
            return row, _auth_status(row), "exact"

        stripped_query = _strip(plate_up)

        row = _by_stripped.get(stripped_query)
        if row is not None:
            return row, _auth_status(row), "fuzzy"

        if len(stripped_query) >= 5:
            head1 = stripped_query[:-1]
            row = _by_stripped.get(head1)
            if row is not None:
                return row, _auth_status(row), "trail_strip"

        bucket = _by_length.get(len(stripped_query), [])
        for key, candidate in bucket:
            if sum(a != b for a, b in zip(key, stripped_query)) == 1:
                return candidate, _auth_status(candidate), "edit1"

        seen_rows: dict[int, dict] = {}
        confusion_variants = _confusion_candidates(stripped_query)
        for variant in confusion_variants:
            r = _by_stripped.get(variant)
            if r is not None:
                seen_rows[id(r)] = r
        if len(seen_rows) == 1:
            r = next(iter(seen_rows.values()))
            return r, _auth_status(r), "confusion"

        # confusion + prefix: OCR read body only but DB has year suffix
        conf_prefix_hits: dict[int, dict] = {}
        for variant in confusion_variants:
            for plen in range(4, min(8, len(variant) + 1)):
                prefix_key = variant[:plen]
                cands = _by_prefix.get(prefix_key, [])
                if len(cands) == 1:
                    _, candidate = cands[0]
                    cand_stripped = _strip(candidate.get('license_normalized', ''))
                    if cand_stripped[:len(variant)] == variant:
                        conf_prefix_hits[id(candidate)] = candidate
        if len(conf_prefix_hits) == 1:
            r = next(iter(conf_prefix_hits.values()))
            return r, _auth_status(r), "confusion_prefix"

        # confusion + edit1
        conf_edit_hits: dict[int, dict] = {}
        q_alpha_ce = re.sub(r'[^A-Z]', '', stripped_query)
        for variant in confusion_variants:
            variant_bucket = _by_length.get(len(variant), [])
            for key, candidate in variant_bucket:
                if sum(a != b for a, b in zip(key, variant)) == 1:
                    cand_alpha_ce = re.sub(r'[^A-Z]', '', _strip(
                        candidate.get('license_normalized', '')))
                    if any(c in cand_alpha_ce for c in q_alpha_ce):
                        conf_edit_hits[id(candidate)] = candidate
        if len(conf_edit_hits) == 1:
            r = next(iter(conf_edit_hits.values()))
            return r, _auth_status(r), "confusion_edit"

        # confusion + single-char insertion: drop 1 char from each confusion variant
        _sq_len_early = len(stripped_query)
        conf_ins_hits: dict[int, dict] = {}
        if 5 <= _sq_len_early <= 10:
            for variant in confusion_variants:
                v_len = len(variant)
                for drop in range(v_len):
                    candidate_key = variant[:drop] + variant[drop + 1:]
                    r = _by_stripped.get(candidate_key)
                    if r is not None:
                        conf_ins_hits[id(r)] = r
            if len(conf_ins_hits) == 1:
                r = next(iter(conf_ins_hits.values()))
                return r, _auth_status(r), "conf_insertion"

        sq_len = len(stripped_query)
        if 4 <= sq_len <= 8:
            candidates = _by_prefix.get(stripped_query, [])
            if len(candidates) == 1:
                key, candidate = candidates[0]
                return candidate, _auth_status(candidate), "prefix"

        # edit-distance-2 (same length, same first-3 prefix) — unambiguous only
        ed2_hits: dict[int, dict] = {}
        for key, candidate in bucket:
            if (sum(a != b for a, b in zip(key, stripped_query)) == 2
                    and key[:3] == stripped_query[:3]):
                ed2_hits[id(candidate)] = candidate
        if len(ed2_hits) == 1:
            r = next(iter(ed2_hits.values()))
            return r, _auth_status(r), "edit2"

        if sq_len >= 6:
            ed3_hits: dict[int, dict] = {}
            for key, candidate in bucket:
                if (sum(a != b for a, b in zip(key, stripped_query)) == 3
                        and key[:4] == stripped_query[:4]):
                    ed3_hits[id(candidate)] = candidate
            if len(ed3_hits) == 1:
                r = next(iter(ed3_hits.values()))
                return r, _auth_status(r), "edit3"

        # 5. Strip 1–3 leading garbage chars
        for trim in range(1, 4):
            if sq_len - trim < 4:
                break
            tail = stripped_query[trim:]
            row = _by_stripped.get(tail)
            if row is not None:
                return row, _auth_status(row), "fuzzy"
            if 4 <= len(tail) <= 8:
                tail_cands = _by_prefix.get(tail, [])
                if len(tail_cands) == 1:
                    _, candidate = tail_cands[0]
                    return candidate, _auth_status(candidate), "prefix"

        for trail in range(1, 3):
            if sq_len - trail < 3:
                break
            head = stripped_query[:-trail]
            row = _by_stripped.get(head)
            if row is not None:
                return row, _auth_status(row), "fuzzy"

        # single-char insertion recovery
        if 5 <= sq_len <= 9:
            insertion_hits: dict[int, dict] = {}
            insertion_match: dict[int, str] = {}
            for drop in range(sq_len):
                candidate_key = stripped_query[:drop] + stripped_query[drop+1:]
                r = _by_stripped.get(candidate_key)
                if r is not None:
                    insertion_hits[id(r)] = r
                    insertion_match[id(r)] = candidate_key
            if len(insertion_hits) == 1:
                r = next(iter(insertion_hits.values()))
                return r, _auth_status(r), "insertion"

        # leading-garbage + confusion recovery
        trim_conf_hits: dict[int, dict] = {}
        for trim in range(1, 4):
            if sq_len - trim < 4:
                break
            tail = stripped_query[trim:]

            tail_conf_variants = _confusion_candidates(tail)
            for tv in tail_conf_variants:
                r = _by_stripped.get(tv)
                if r is not None:
                    trim_conf_hits[id(r)] = r
                if 4 <= len(tv) <= 8:
                    tv_cands = _by_prefix.get(tv, [])
                    if len(tv_cands) == 1:
                        _, candidate = tv_cands[0]
                        cand_s = _strip(candidate.get('license_normalized', ''))
                        if cand_s[:len(tv)] == tv:
                            trim_conf_hits[id(candidate)] = candidate
            tail_len = len(tail)
            tail_bucket = _by_length.get(tail_len, [])
            for key, candidate in tail_bucket:
                if sum(a != b for a, b in zip(key, tail)) == 1:
                    trim_conf_hits[id(candidate)] = candidate
        if len(trim_conf_hits) == 1:
            r = next(iter(trim_conf_hits.values()))
            return r, _auth_status(r), "trim_confusion"

        # 6. Missing leading char(s): OCR dropped 1–2 chars from start
        if 4 <= sq_len <= 7:
            for lead in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
                ckey = lead + stripped_query
                row = _by_stripped.get(ckey)
                if row is not None:
                    return row, _auth_status(row), "prefix"
                if 4 <= len(ckey) <= 8:
                    cands = _by_prefix.get(ckey, [])
                    if len(cands) == 1:
                        _, candidate = cands[0]
                        return candidate, _auth_status(candidate), "prefix"

        # trim + insert
        if sq_len >= 5:
            trim_insert_hits: dict[int, dict] = {}
            for trim in range(1, 3):
                if sq_len - trim < 4:
                    break
                tail = stripped_query[trim:]
                tail_len = len(tail)
                if tail_len > 7:
                    continue

                for ins_pos in range(tail_len + 1):
                    for ins_char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
                        candidate_key = tail[:ins_pos] + ins_char + tail[ins_pos:]
                        r = _by_stripped.get(candidate_key)
                        if r is not None:
                            trim_insert_hits[id(r)] = r
            if len(trim_insert_hits) == 1:
                r = next(iter(trim_insert_hits.values()))
                return r, _auth_status(r), "trim_insert"

        # leading-char + confusion recovery
        if 4 <= sq_len <= 7:
            lead_conf_hits: dict[int, dict] = {}
            for lead in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                ckey = lead + stripped_query
                ckey_conf = _confusion_candidates(ckey)
                for cv in ckey_conf:
                    r = _by_stripped.get(cv)
                    if r is not None:
                        lead_conf_hits[id(r)] = r
                    if 4 <= len(cv) <= 8:
                        cv_cands = _by_prefix.get(cv, [])
                        if len(cv_cands) == 1:
                            _, candidate = cv_cands[0]
                            cand_s = _strip(candidate.get('license_normalized', ''))
                            if cand_s[:len(cv)] == cv:
                                lead_conf_hits[id(candidate)] = candidate
            if len(lead_conf_hits) == 1:
                r = next(iter(lead_conf_hits.values()))
                return r, _auth_status(r), "lead_confusion"

        # digit-suffix match — for garbled prefix or pure-digit OCR
        # unambiguous only, requires >=5 digits
        digits_only = re.sub(r'[^0-9]', '', stripped_query)
        has_alpha_q = bool(re.search(r'[A-Z]', stripped_query))
        if len(digits_only) >= 5:
            hits: dict[int, dict] = {}
            best_slen = 0
            best_sub = ""
            for slen in range(min(6, len(digits_only)), 3, -1):
                hits.clear()
                for start in range(len(digits_only) - slen + 1):
                    sub = digits_only[start:start + slen]
                    for _, candidate in _by_suffix.get(sub, []):
                        hits[id(candidate)] = candidate
                if len(hits) == 1:
                    best_slen = slen
                    best_sub = sub
                    break
            if len(hits) == 1 and best_slen >= 4:
                r = next(iter(hits.values()))
                r_stripped = _strip(r.get('license_normalized', ''))
                if len(stripped_query) <= int(len(r_stripped) * 1.5) + 1:
                    r_alpha = re.sub(r'[^A-Z]', '', r_stripped)
                    q_alpha = re.sub(r'[^A-Z]', '', stripped_query)
                    prefix_match = (len(q_alpha) >= 2 and len(r_alpha) >= 2
                                    and q_alpha[:2] == r_alpha[:2])
                    pure_digit_q = not has_alpha_q
                    long_suffix = best_slen >= 5
                    garbled_prefix = (len(q_alpha) >= 2 and len(r_alpha) >= 2
                                      and not any(c in r_alpha for c in q_alpha))
                    unique_suffix = (
                        best_slen == 4
                        and len(_by_suffix.get(best_sub, [])) == 1
                        and len(q_alpha) >= 1 and len(r_alpha) >= 1
                        and any(c in r_alpha for c in q_alpha)
                    )
                    if pure_digit_q or prefix_match or long_suffix or garbled_prefix or unique_suffix:
                        return r, _auth_status(r), "suffix"

        # confusion-aware digit suffix match
        if len(digits_only) >= 3:
            digit_conf_hits: dict[int, dict] = {}
            digit_conf_best_slen = 0
            digit_conf_strings: set[str] = {digits_only}
            for i, d in enumerate(digits_only):
                for alt in _CONFUSION.get(d, ()):
                    if alt.isdigit():
                        variant_d = digits_only[:i] + alt + digits_only[i+1:]
                        digit_conf_strings.add(variant_d)
            for dcs in digit_conf_strings:
                if dcs == digits_only:
                    continue  # already tried above
                for slen in range(min(6, len(dcs)), 3, -1):
                    for start in range(len(dcs) - slen + 1):
                        sub = dcs[start:start + slen]
                        for _, candidate in _by_suffix.get(sub, []):
                            digit_conf_hits[id(candidate)] = candidate
                    if len(digit_conf_hits) == 1:
                        digit_conf_best_slen = slen
                        break
                if len(digit_conf_hits) == 1:
                    break
            if len(digit_conf_hits) == 1 and digit_conf_best_slen >= 4:
                r = next(iter(digit_conf_hits.values()))
                r_stripped = _strip(r.get('license_normalized', ''))
                if len(stripped_query) > int(len(r_stripped) * 1.5) + 1:
                    pass
                else:
                    r_alpha = re.sub(r'[^A-Z]', '', r_stripped)
                    q_alpha = re.sub(r'[^A-Z]', '', stripped_query)
                    alpha_overlap = any(c in r_alpha for c in q_alpha)
                    if alpha_overlap or digit_conf_best_slen >= 5:
                        return r, _auth_status(r), "conf_suffix"
            # Disambiguation: if multiple hits but they narrow to 1 with 3-char alpha prefix
            if len(digit_conf_hits) > 1 and digit_conf_best_slen >= 4:
                q_alpha3 = re.sub(r'[^A-Z]', '', stripped_query)[:3]
                if len(q_alpha3) >= 3:
                    narrow = {rid: row for rid, row in digit_conf_hits.items()
                               if re.sub(r'[^A-Z]', '', _strip(row.get('license_normalized','')))[:3] == q_alpha3}
                    if len(narrow) == 1:
                        r = next(iter(narrow.values()))
                        return r, _auth_status(r), "conf_suffix"

        # trim + digit confusion: leading-garbage + suffix confusion
        if sq_len >= 5:
            for trim in range(1, 4):
                if sq_len - trim < 4:
                    break
                tail = stripped_query[trim:]
                tail_digits = re.sub(r'[^0-9]', '', tail)
                tail_alpha = re.sub(r'[^A-Z]', '', tail)
                if len(tail_digits) < 3:
                    continue

                tail_dconf_strings: set[str] = set()
                for i, d in enumerate(tail_digits):
                    for alt in _CONFUSION.get(d, ()):
                        if alt.isdigit():
                            tail_dconf_strings.add(tail_digits[:i] + alt + tail_digits[i+1:])
                trim_dc_hits: dict[int, dict] = {}
                trim_dc_best_slen = 0
                for dcs in tail_dconf_strings:
                    for slen in range(min(6, len(dcs)), 3, -1):
                        for start in range(len(dcs) - slen + 1):
                            sub = dcs[start:start + slen]
                            for _, candidate in _by_suffix.get(sub, []):
                                trim_dc_hits[id(candidate)] = candidate
                        if len(trim_dc_hits) == 1:
                            trim_dc_best_slen = slen
                            break
                    if len(trim_dc_hits) == 1:
                        break
                if len(trim_dc_hits) == 1 and trim_dc_best_slen >= 4:
                    r = next(iter(trim_dc_hits.values()))
                    r_stripped = _strip(r.get('license_normalized', ''))
                    if len(tail) > int(len(r_stripped) * 1.5) + 1:
                        continue
                    r_alpha_dc = re.sub(r'[^A-Z]', '', r_stripped)
                    alpha_common = sum(1 for c in tail_alpha if c in r_alpha_dc)
                    if alpha_common >= 2 or trim_dc_best_slen >= 5:
                        return r, _auth_status(r), "trim_dc_suffix"

        # confusion + leading-digit-strip
        full_conf_variants = _confusion_candidates(stripped_query)
        for fv in full_conf_variants:
            fv_digits = re.sub(r'[^0-9]', '', fv)
            fv_alpha = re.sub(r'[^A-Z]', '', fv)
            if len(fv_digits) >= 2 and fv_digits[0].isdigit():
                stripped_digits = fv_digits[1:]

                stripped_cand = fv_alpha + stripped_digits
                r = _by_stripped.get(stripped_cand)
                if r is not None:
                    return r, _auth_status(r), "conf_leading_strip"
                if 4 <= len(stripped_cand) <= 8:
                    sc_cands = _by_prefix.get(stripped_cand, [])
                    if len(sc_cands) == 1:
                        _, candidate = sc_cands[0]
                        cand_s = _strip(candidate.get('license_normalized', ''))
                        if cand_s[:len(stripped_cand)] == stripped_cand:
                            return candidate, _auth_status(candidate), "conf_leading_strip"

        # 8. Substring match: query alpha+digit, 6+ chars, is a substring of exactly ONE plate
        has_alpha = bool(re.search(r'[A-Z]', stripped_query))
        has_digit = bool(re.search(r'[0-9]', stripped_query))
        if sq_len >= 6 and has_alpha and has_digit:
            hits2: dict[int, dict] = {}
            for key, candidate in _by_stripped.items():
                if stripped_query in key and len(key) > sq_len:
                    hits2[id(candidate)] = candidate
            if len(hits2) == 1:
                r = next(iter(hits2.values()))
                return r, _auth_status(r), "substring"

        # double confusion (two OCR errors) — strictly unambiguous
        if has_alpha and has_digit and 5 <= sq_len <= 9:
            double_conf_hits: dict[int, dict] = {}
            double_conf_variants = _confusion_candidates_double(stripped_query)
            for variant in double_conf_variants:
                r = _by_stripped.get(variant)
                if r is not None:
                    double_conf_hits[id(r)] = r
                if 4 <= len(variant) <= 8:
                    dv_cands = _by_prefix.get(variant, [])
                    if len(dv_cands) == 1:
                        _, candidate = dv_cands[0]
                        cand_s = _strip(candidate.get('license_normalized', ''))
                        if cand_s[:len(variant)] == variant:
                            double_conf_hits[id(candidate)] = candidate
            if len(double_conf_hits) == 1:
                r = next(iter(double_conf_hits.values()))
                return r, _auth_status(r), "double_confusion"

    return None, "unauthorized", "not_found"


def upsert(row: dict) -> None:
    norm = str(row.get("license_normalized") or "").strip().upper()
    if not norm:
        return
    stripped = _strip(norm)
    row = dict(row)
    if "license_stripped" not in row or not row.get("license_stripped"):
        row["license_stripped"] = stripped
    with _lock:
        old_row = _by_normalized.get(norm)
        if old_row is not None:
            old_norm     = str(old_row.get("license_normalized") or "").strip().upper()
            old_stripped = _strip(old_norm)
            if old_stripped != stripped:
                old_bucket = _by_length.get(len(old_stripped), [])
                _by_length[len(old_stripped)] = [(k, v) for k, v in old_bucket if k != old_stripped]
                _by_stripped.pop(old_stripped, None)
                for plen in range(4, min(8, len(old_stripped) + 1)):
                    pb = _by_prefix.get(old_stripped[:plen], [])
                    _by_prefix[old_stripped[:plen]] = [(k, v) for k, v in pb if k != old_stripped]
                old_digits = re.sub(r'[^0-9]', '', old_stripped)
                for slen in range(4, min(7, len(old_digits) + 1)):
                    for start in range(len(old_digits) - slen + 1):
                        sub = old_digits[start:start + slen]
                        if sub in _by_suffix:
                            _by_suffix[sub] = [(k, v) for k, v in _by_suffix[sub] if k != old_stripped]

        _by_normalized[norm]   = row
        _by_stripped[stripped] = row
        bucket = _by_length.setdefault(len(stripped), [])
        for i, (k, _) in enumerate(bucket):
            if k == stripped:
                bucket[i] = (stripped, row)
                break
        else:
            bucket.append((stripped, row))
        for plen in range(4, min(8, len(stripped) + 1)):
            pb = _by_prefix.setdefault(stripped[:plen], [])
            for i, (k, _) in enumerate(pb):
                if k == stripped:
                    pb[i] = (stripped, row)
                    break
            else:
                pb.append((stripped, row))
        digits = re.sub(r'[^0-9]', '', stripped)
        for slen in range(4, min(7, len(digits) + 1)):
            for start in range(len(digits) - slen + 1):
                sub = digits[start:start + slen]
                sb = _by_suffix.setdefault(sub, [])
                for i, (k, _) in enumerate(sb):
                    if k == stripped:
                        sb[i] = (stripped, row)
                        break
                else:
                    sb.append((stripped, row))


def invalidate(license_normalized: str) -> None:
    norm = license_normalized.strip().upper()
    stripped = _strip(norm)
    with _lock:
        _by_normalized.pop(norm, None)
        _by_stripped.pop(stripped, None)
        bucket = _by_length.get(len(stripped), [])
        _by_length[len(stripped)] = [(k, v) for k, v in bucket if k != stripped]
        for plen in range(4, min(8, len(stripped) + 1)):
            pb = _by_prefix.get(stripped[:plen], [])
            _by_prefix[stripped[:plen]] = [(k, v) for k, v in pb if k != stripped]
        digits = re.sub(r'[^0-9]', '', stripped)
        for slen in range(4, min(7, len(digits) + 1)):
            for start in range(len(digits) - slen + 1):
                sub = digits[start:start + slen]
                if sub in _by_suffix:
                    _by_suffix[sub] = [(k, v) for k, v in _by_suffix[sub] if k != stripped]


def is_loaded() -> bool:
    return _loaded


def size() -> int:
    with _lock:
        return len(_by_normalized)


# No-op kept so pipeline can call it safely without checking
def clear_cache() -> None:
    pass