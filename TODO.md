## Development

- Push ohne PR
- Manuelles deployment via Input actions (tests optional)


# PR
- Nur Tests (-> Ohne Environment)
- Required deployments ausschalten
- Alle Tests + Most recent push reviewd -> Merge allowed


# Main/Master Merge

- Deploy to prod und alle anderen
- RCancel superseeded
- Regression Tests via concurrency group