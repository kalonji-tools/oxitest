/// Serialize an `AHashMap` in alphabetically sorted key order.
///
/// `AHashMap` iteration order is non-deterministic. Without sorting, the cache
/// file would diff on every write even when no data changed, breaking VCS workflows
/// and making it harder to audit what actually changed between runs.
pub(super) fn serialize_sorted<S, V>(
    map: &ahash::AHashMap<String, V>,
    serializer: S,
) -> Result<S::Ok, S::Error>
where
    S: ::serde::Serializer,
    V: ::serde::Serialize,
{
    use serde::ser::SerializeMap;
    let mut entries: Vec<(&str, &V)> = map.iter().map(|(k, v)| (k.as_str(), v)).collect();
    entries.sort_unstable_by_key(|(k, _)| *k);
    let mut state = serializer.serialize_map(Some(entries.len()))?;
    for (k, v) in entries {
        state.serialize_entry(k, v)?;
    }
    state.end()
}
