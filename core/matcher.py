# -*- coding: utf-8 -*-


def get_match_key(item):
    """
    使用 relative_path 作为匹配键。
    注意：空字符串 "" 表示 mesh 直接位于 root 下，也是合法 key。
    """
    return item.get("relative_path")


def match_abc_to_original(abc_items, original_items):
    """
    根据 relative_path 做精确匹配。

    返回：
        {
            "matched": [(orig_item, abc_item), ...],
            "conflicts": [(abc_item, [orig_item, ...]), ...],
            "unmatched": [abc_item, ...],
            "unmatched_originals": [orig_item, ...]
        }
    """
    orig_map = {}
    for orig in original_items:
        key = get_match_key(orig)
        if key is None:
            continue
        orig_map.setdefault(key, []).append(orig)

    matched = []
    conflicts = []
    unmatched = []
    matched_abc_keys = set()

    for abc in abc_items:
        key = get_match_key(abc)
        if key is None:
            unmatched.append(abc)
            continue

        candidates = orig_map.get(key, [])

        if len(candidates) == 1:
            matched.append((candidates[0], abc))
            matched_abc_keys.add(key)
        elif len(candidates) > 1:
            conflicts.append((abc, candidates))
        else:
            unmatched.append(abc)

    matched_orig_paths = {orig["dag_path"] for orig, _ in matched}
    unmatched_originals = [
        orig for orig in original_items
        if orig.get("relative_path") is not None
        and orig["dag_path"] not in matched_orig_paths
    ]

    return {
        "matched": matched,
        "conflicts": conflicts,
        "unmatched": unmatched,
        "unmatched_originals": unmatched_originals,
    }
