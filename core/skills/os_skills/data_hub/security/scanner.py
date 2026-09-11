"""Local security scanning — secrets, PII, prompt injection detection."""

import re
from typing import List, Tuple
from dataclasses import dataclass


@dataclass
class SecurityIssue:
    type: str  # "secret", "pii", "injection"
    pattern_name: str
    location: str  # "line X, column Y" or approximate
    severity: str  # "high", "medium", "low"


class SecurityScanner:
    """Detect secrets, PII, and prompt-injection patterns BEFORE model input."""

    # Secret patterns (API keys, tokens, AWS IDs, etc.)
    SECRET_PATTERNS = {
        "aws_key": (
            r"(aws_access_key_id|aws_secret_access_key|AKIA[0-9A-Z]{16})",
            "high",
        ),
        "github_token": (r"ghp_[a-zA-Z0-9]{20,}", "high"),
        "api_key_generic": (
            r"(api[_-]?key|apikey|api-key)[\s:=]+['\"]?[a-zA-Z0-9\-_.]{20,}['\"]?",
            "high",
        ),
        "jwt_token": (r"eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+", "high"),
    }

    # PII patterns (IBANs, SSNs, email+phone pairs, etc.)
    PII_PATTERNS = {
        "iban": (
            r"\b[A-Za-z]{2}\d{2}(?:[ ]?[A-Za-z0-9]{4}){2,8}[ ]?[A-Za-z0-9]{0,3}\b",
            "high",
        ),
        "ssn": (r"\b\d{3}-\d{2}-\d{4}\b", "high"),
        "email_phone_pair": (
            r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}).{0,100}(\d{3}[-.\s]?\d{3}[-.\s]?\d{4})",
            "medium",
        ),
    }

    # Prompt-injection patterns (DE + EN, including polite forms)
    INJECTION_PATTERNS = {
        "ignore_instructions_de": (
            r"(ignoriere?|vergiss?|überspring|vergesst?) (all|alle|vorherigen|bisherigen|obigen|oben)",
            "high",
        ),
        "ignore_instructions_en": (
            r"(ignore|disregard|forget|skip|bypass) (all previous|the above|prior|earlier)",
            "high",
        ),
        "system_prompt_de": (
            r"(system[_-]?prompt|instruktionen|anweisungen|regel)",
            "medium",
        ),
        "system_prompt_en": (r"(system[_-]?prompt|instructions|rules)", "medium"),
    }

    def __init__(self):
        self.compiled_secrets = {
            k: (re.compile(v[0], re.IGNORECASE), v[1])
            for k, v in self.SECRET_PATTERNS.items()
        }
        self.compiled_pii = {
            k: (re.compile(v[0]), v[1]) for k, v in self.PII_PATTERNS.items()
        }
        self.compiled_injections = {
            k: (re.compile(v[0], re.IGNORECASE), v[1])
            for k, v in self.INJECTION_PATTERNS.items()
        }

    def scan_text(self, text: str) -> Tuple[str, List[SecurityIssue]]:
        """
        Scan text for secrets/PII/injections.
        Returns: (redacted_text, issues_found)
        """
        issues = []
        redacted = text

        # Detect and redact secrets
        for pattern_name, (regex, severity) in self.compiled_secrets.items():
            matches = list(regex.finditer(text))
            if matches:
                for match in matches:
                    issues.append(
                        SecurityIssue(
                            type="secret",
                            pattern_name=pattern_name,
                            location=f"chars {match.start()}-{match.end()}",
                            severity=severity,
                        )
                    )
                    # Redact
                    redacted = redacted.replace(
                        match.group(), "<REDACTED_SECRET>", 1
                    )

        # Detect PII (flag, don't redact)
        for pattern_name, (regex, severity) in self.compiled_pii.items():
            matches = list(regex.finditer(text))
            if matches:
                for match in matches:
                    issues.append(
                        SecurityIssue(
                            type="pii",
                            pattern_name=pattern_name,
                            location=f"chars {match.start()}-{match.end()}",
                            severity=severity,
                        )
                    )

        # Detect prompt-injection patterns (flag, don't redact)
        for pattern_name, (regex, severity) in self.compiled_injections.items():
            matches = list(regex.finditer(text))
            if matches:
                for match in matches:
                    issues.append(
                        SecurityIssue(
                            type="injection",
                            pattern_name=pattern_name,
                            location=f"chars {match.start()}-{match.end()}",
                            severity=severity,
                        )
                    )

        return redacted, issues

    def scan_document(self, content: str, source: str) -> Tuple[str, List[str]]:
        """
        Scan a document. Returns: (redacted_content, security_issue_types)
        """
        redacted, issues = self.scan_text(content)
        issue_types = list(set(issue.type for issue in issues))
        return redacted, issue_types
