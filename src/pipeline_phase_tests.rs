use super::*;
use crate::types::NodeId;

mod fixture_validation_format_tests {
    use super::*;

    #[test]
    fn format_errors_with_suggestion() {
        let errors = vec![(NodeId::from_raw("test.py::test_foo"), "sotre".to_string())];
        let registered = vec!["store".to_string(), "backend".to_string()];
        let msg = format_fixture_errors(&errors, &registered);
        assert!(msg.contains("ERROR collecting tests"));
        assert!(msg.contains("fixture 'sotre' not found"));
        assert!(msg.contains("did you mean 'store'?"));
    }

    #[test]
    fn format_errors_without_suggestion() {
        let errors = vec![(NodeId::from_raw("test.py::test_foo"), "zzzzz".to_string())];
        let registered = vec!["store".to_string()];
        let msg = format_fixture_errors(&errors, &registered);
        assert!(msg.contains("fixture 'zzzzz' not found"));
        assert!(!msg.contains("did you mean"));
    }

    #[test]
    fn format_errors_multiple() {
        let errors = vec![
            (NodeId::from_raw("test.py::test_a"), "sotre".to_string()),
            (NodeId::from_raw("test.py::test_b"), "xyz".to_string()),
        ];
        let registered = vec!["store".to_string()];
        let msg = format_fixture_errors(&errors, &registered);
        assert!(msg.contains("test.py::test_a"));
        assert!(msg.contains("test.py::test_b"));
        assert!(msg.contains("sotre"));
        assert!(msg.contains("xyz"));
    }

    #[test]
    fn format_errors_empty_registered() {
        let errors = vec![(NodeId::from_raw("test.py::test_foo"), "store".to_string())];
        let msg = format_fixture_errors(&errors, &[]);
        assert!(msg.contains("fixture 'store' not found"));
        assert!(!msg.contains("did you mean"));
    }
}
