"""ctwatch, ported (first-drop spec §3.1, §5): CloudTrail who-did-what. Library layers only; no TUI, CLI or Athena.
Every AWS call goes through aws95.aws.gate; botocore is imported lazily, never at module import."""
