/// Compute the Levenshtein edit distance between two strings.
///
/// Uses the standard two-row dynamic programming approach with O(min(m, n)) space.
pub fn edit_distance(a: &str, b: &str) -> usize {
    let a: Vec<char> = a.chars().collect();
    let b: Vec<char> = b.chars().collect();

    // Ensure `a` is the shorter string to minimize memory usage.
    let (a, b) = if a.len() <= b.len() { (a, b) } else { (b, a) };

    let m = a.len();
    let n = b.len();

    // `prev` holds distances for row i-1, `curr` for row i.
    let mut prev: Vec<usize> = (0..=m).collect();
    let mut curr = vec![0usize; m + 1];

    for j in 1..=n {
        curr[0] = j;
        for i in 1..=m {
            curr[i] = if a[i - 1] == b[j - 1] {
                prev[i - 1]
            } else {
                1 + prev[i - 1].min(prev[i]).min(curr[i - 1])
            };
        }
        std::mem::swap(&mut prev, &mut curr);
    }

    prev[m]
}

/// Find the closest match for `name` among `candidates` within `max_distance`.
///
/// Returns `None` if no candidate is within `max_distance` edit operations.
/// When multiple candidates tie on distance, the first one encountered wins.
pub fn closest_match<'a>(
    name: &str,
    candidates: impl IntoIterator<Item = &'a str>,
    max_distance: usize,
) -> Option<&'a str> {
    let mut best: Option<(&'a str, usize)> = None;

    for candidate in candidates {
        let d = edit_distance(name, candidate);
        if d <= max_distance {
            match best {
                None => best = Some((candidate, d)),
                Some((_, best_d)) if d < best_d => best = Some((candidate, d)),
                _ => {}
            }
        }
    }

    best.map(|(s, _)| s)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identical_strings_distance_zero() {
        assert_eq!(edit_distance("store", "store"), 0);
    }

    #[test]
    fn identical_empty_strings_distance_zero() {
        assert_eq!(edit_distance("", ""), 0);
    }

    #[test]
    fn one_empty_string() {
        assert_eq!(edit_distance("store", ""), 5);
        assert_eq!(edit_distance("", "store"), 5);
    }

    #[test]
    fn single_transposition() {
        // Classic Levenshtein counts a swap of two adjacent chars as 2 ops
        // (one delete + one insert). "abcd" → "bacd": delete 'a', insert 'a' after 'b'.
        // Damerau-Levenshtein would give 1, but we implement classic Levenshtein.
        assert_eq!(edit_distance("abcd", "bacd"), 2);
    }

    #[test]
    fn single_insertion() {
        // "store" vs "stor" — one deletion from "store" perspective
        assert_eq!(edit_distance("store", "stor"), 1);
    }

    #[test]
    fn single_deletion() {
        // "store" vs "storee" — one insertion from "store" perspective
        assert_eq!(edit_distance("store", "storee"), 1);
    }

    #[test]
    fn completely_different() {
        // "abc" vs "xyz" — all three chars replaced
        assert_eq!(edit_distance("abc", "xyz"), 3);
    }

    #[test]
    fn closest_match_finds_typo() {
        let candidates = ["db_session", "http_client", "auth_user"];
        let result = closest_match("db_sessoin", candidates.iter().copied(), 2);
        assert_eq!(result, Some("db_session"));
    }

    #[test]
    fn closest_match_returns_none_when_nothing_close() {
        let candidates = ["alpha", "beta", "gamma"];
        let result = closest_match("zzzzzzz", candidates.iter().copied(), 2);
        assert_eq!(result, None);
    }

    #[test]
    fn closest_match_picks_closest_among_multiple_candidates() {
        // "fixtuer" is 2 away from "fixture" and 4+ away from "factory"
        let candidates = ["factory", "fixture", "filter"];
        let result = closest_match("fixtuer", candidates.iter().copied(), 3);
        assert_eq!(result, Some("fixture"));
    }

    #[test]
    fn accented_chars_count_as_single_operations() {
        // café → cafe: one substitution (é → e)
        assert_eq!(edit_distance("café", "cafe"), 1);
    }

    #[test]
    fn cjk_characters() {
        assert_eq!(edit_distance("你好世界", "你好世界"), 0);
        assert_eq!(edit_distance("你好", "你坏"), 1);
    }

    #[test]
    fn emoji_characters() {
        assert_eq!(edit_distance("🎉🎊", "🎉🎊"), 0);
        assert_eq!(edit_distance("🎉", "🎊"), 1);
    }

    #[test]
    fn mixed_ascii_and_unicode() {
        assert_eq!(edit_distance("test_café", "test_cafe"), 1);
    }

    #[test]
    fn closest_match_with_unicode_candidates() {
        let candidates = ["données", "donnée", "donner"];
        let result = closest_match("donnees", candidates.iter().copied(), 2);
        assert_eq!(result, Some("données"));
    }
}
