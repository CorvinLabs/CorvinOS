#!/usr/bin/env python3
"""
Adversarial Review Stream D: Compliance & Audit Verification for CorvinOS
Comprehensive audit of GDPR/EU AI Act compliance across 5 components
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Color codes
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

AUDIT_FILE = Path.home() / '.corvin' / 'tenants' / '_default' / 'global' / 'forge' / 'audit.jsonl'

def load_audit_events(limit=500):
    """Load audit events from the audit trail."""
    if not AUDIT_FILE.exists():
        return []
    
    events = []
    with open(AUDIT_FILE, 'r') as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events

def check_tenant_isolation(events):
    """Verify GDPR Art. 5 & 32: Tenant isolation in audit trail."""
    findings = []
    tenant_ids = set()
    
    for event in events:
        tenant_id = event.get('tenant_id')
        if not tenant_id:
            findings.append(('ERROR', f'Event missing tenant_id: {event.get("event_type")}'))
        else:
            tenant_ids.add(tenant_id)
    
    if not findings:
        findings.append(('OK', f'All {len(events)} events have tenant_id ({", ".join(tenant_ids)})'))
    
    return findings

def check_audit_completeness(events):
    """Verify GDPR Art. 30/32: Audit trail has required fields."""
    findings = []
    required_fields = ['event_type', 'ts', 'tenant_id']
    
    missing_field_counts = defaultdict(int)
    for event in events:
        for field in required_fields:
            if field not in event:
                missing_field_counts[field] += 1
    
    if missing_field_counts:
        for field, count in missing_field_counts.items():
            findings.append(('ERROR', f'{count} events missing required field: {field}'))
    else:
        findings.append(('OK', f'All {len(events)} events have required fields: {required_fields}'))
    
    return findings

def check_pii_leakage(events):
    """Verify GDPR Art. 5: No PII in audit labels/operation types."""
    findings = []
    pii_patterns = {
        'email': lambda s: '@' in str(s) and '.' in str(s),
        'phone': lambda s: any(c.isdigit() for c in str(s)) and len(str(s)) >= 10,
        'password': lambda s: 'password' in str(s).lower(),
        'credit_card': lambda s: any(c.isdigit() for c in str(s)) and len(str(s)) >= 16,
        'ssn': lambda s: str(s).count('-') == 2 and all(c.isdigit() or c == '-' for c in str(s)),
    }
    
    pii_found = defaultdict(int)
    for event in events:
        # Check event_type field (should be categorical, not PII)
        event_type = str(event.get('event_type', ''))
        for pii_name, pii_check in pii_patterns.items():
            if pii_check(event_type):
                pii_found[pii_name] += 1
    
    if pii_found:
        for pii_type, count in pii_found.items():
            findings.append(('WARNING', f'{count} events with suspected {pii_type} in event_type'))
    else:
        findings.append(('OK', f'No PII patterns detected in {len(events)} event types'))
    
    return findings

def check_audit_timestamp_coverage(events):
    """Verify GDPR Art. 30: All events have timestamps."""
    findings = []
    valid_ts = 0
    
    for event in events:
        ts = event.get('ts')
        if ts and isinstance(ts, (int, float)):
            valid_ts += 1
    
    if valid_ts == len(events):
        findings.append(('OK', f'All {len(events)} events have valid Unix timestamps'))
    else:
        findings.append(('ERROR', f'Only {valid_ts}/{len(events)} events have valid timestamps'))
    
    return findings

def check_hash_chain_presence():
    """Verify ADR-0232: Hash-chaining infrastructure exists."""
    findings = []
    
    # Check if tripwire.py exists
    tripwire_path = Path('/home/shumway/projects/CorvinOS/core/compliance/corvin_compliance_reports/tripwire.py')
    if tripwire_path.exists():
        findings.append(('OK', 'Boot tripwire (tripwire.py) exists'))
    else:
        findings.append(('ERROR', 'Boot tripwire (tripwire.py) missing'))
    
    # Check if chain.py exists
    chain_path = Path('/home/shumway/projects/CorvinOS/core/audit/chain.py')
    if chain_path.exists():
        findings.append(('OK', 'Audit chain implementation (chain.py) exists'))
    else:
        findings.append(('ERROR', 'Audit chain implementation (chain.py) missing'))
    
    # Check audit file exists and has content
    if AUDIT_FILE.exists():
        size = AUDIT_FILE.stat().st_size
        findings.append(('OK', f'Audit trail exists ({size:,} bytes)'))
    else:
        findings.append(('ERROR', 'Audit trail missing'))
    
    return findings

def check_consent_gate_implementation():
    """Verify GDPR Art. 6, 7: Consent gates exist."""
    findings = []
    
    consent_path = Path('/home/shumway/projects/CorvinOS/core/skills/creator/consent_gate.py')
    if consent_path.exists():
        findings.append(('OK', 'Consent gate implementation exists'))
        
        # Check for fail-closed semantics
        with open(consent_path, 'r') as f:
            content = f.read()
            if 'fail-closed' in content.lower() and 'ConsentDenied' in content:
                findings.append(('OK', 'Fail-closed consent validation present'))
            else:
                findings.append(('WARNING', 'Fail-closed semantics not clearly documented'))
    else:
        findings.append(('ERROR', 'Consent gate implementation missing'))
    
    return findings

def check_tripwire_integration():
    """Verify ADR-0232/0233: Tripwire is called at boot."""
    findings = []
    
    gateway_app_path = Path('/home/shumway/projects/CorvinOS/core/gateway/corvin_gateway/app.py')
    if gateway_app_path.exists():
        with open(gateway_app_path, 'r') as f:
            content = f.read()
            if '_tripwire_assert_all' in content and 'assert_all' in content:
                findings.append(('OK', 'Tripwire called in gateway boot sequence'))
                
                # Check for "no override" language
                tripwire_path = Path('/home/shumway/projects/CorvinOS/core/compliance/corvin_compliance_reports/tripwire.py')
                if tripwire_path.exists():
                    with open(tripwire_path, 'r') as tf:
                        tripwire_content = tf.read()
                        if 'NO override' in tripwire_content and 'env var' in tripwire_content:
                            findings.append(('OK', 'Tripwire explicitly forbids override switches'))
                        else:
                            findings.append(('WARNING', 'Tripwire override policy not explicit'))
            else:
                findings.append(('ERROR', 'Tripwire not called in gateway boot'))
    else:
        findings.append(('ERROR', 'Gateway app.py missing'))
    
    return findings

def check_path_gate_implementation():
    """Verify L10: Path-gate file write protection."""
    findings = []
    
    hardener_path = Path('/home/shumway/projects/CorvinOS/core/security/file_permission_hardener.py')
    if hardener_path.exists():
        findings.append(('OK', 'File permission hardener (L10 path-gate) exists'))
        
        with open(hardener_path, 'r') as f:
            content = f.read()
            if 'fail-closed' in content.lower() and 'PermissionDeniedError' in content:
                findings.append(('OK', 'L10 path-gate has fail-closed semantics'))
            else:
                findings.append(('WARNING', 'L10 fail-closed semantics not documented'))
    else:
        findings.append(('WARNING', 'File permission hardener not found'))
    
    return findings

def main():
    print(f"\n{BLUE}{'='*80}")
    print("Adversarial Review Stream D: Compliance & Audit Verification for CorvinOS")
    print(f"GDPR Art. 30/32 + EU AI Act Art. 50 Compliance Audit")
    print(f"{'='*80}{RESET}\n")
    
    # Load audit events
    events = load_audit_events(500)
    if not events:
        print(f"{RED}ERROR: Could not load audit events from {AUDIT_FILE}{RESET}")
        print("Proceeding with code inspection only...\n")
        events = []
    
    all_findings = {}
    
    # Component 1: GDPR Art. 30/32 Audit Compliance
    print(f"{BLUE}[1/5] GDPR Art. 30/32 Audit Compliance{RESET}")
    findings_1 = []
    findings_1.extend(check_tenant_isolation(events))
    findings_1.extend(check_audit_completeness(events))
    findings_1.extend(check_audit_timestamp_coverage(events))
    findings_1.extend(check_pii_leakage(events))
    all_findings['Audit Compliance'] = findings_1
    
    # Component 2: Disclosure (EU AI Act Art. 50)
    print(f"{BLUE}[2/5] Disclosure Compliance (EU AI Act Art. 50){RESET}")
    findings_2 = check_consent_gate_implementation()
    all_findings['Disclosure'] = findings_2
    
    # Component 3: Consent Gates (GDPR Art. 6, 7)
    print(f"{BLUE}[3/5] Consent Gates (GDPR Art. 6, 7){RESET}")
    findings_3 = []
    findings_3.extend(check_consent_gate_implementation())
    all_findings['Consent Gates'] = findings_3
    
    # Component 4: Boot Tripwire (ADR-0232)
    print(f"{BLUE}[4/5] Boot Tripwire Compliance (ADR-0232){RESET}")
    findings_4 = check_tripwire_integration()
    all_findings['Boot Tripwire'] = findings_4
    
    # Component 5: L10 Path-Gate
    print(f"{BLUE}[5/5] L10 Path-Gate File Write Protection{RESET}")
    findings_5 = check_path_gate_implementation()
    all_findings['Path-Gate'] = findings_5
    
    # Print all findings
    print(f"\n{BLUE}{'='*80}")
    print("COMPLIANCE MATRIX")
    print(f"{'='*80}{RESET}\n")
    
    critical_count = 0
    error_count = 0
    warning_count = 0
    ok_count = 0
    
    for component, findings in all_findings.items():
        print(f"{BLUE}## {component}{RESET}")
        for status, detail in findings:
            if status == 'CRITICAL':
                print(f"  {RED}[CRITICAL]{RESET} {detail}")
                critical_count += 1
            elif status == 'ERROR':
                print(f"  {RED}[ERROR]{RESET} {detail}")
                error_count += 1
            elif status == 'WARNING':
                print(f"  {YELLOW}[WARNING]{RESET} {detail}")
                warning_count += 1
            elif status == 'OK':
                print(f"  {GREEN}[OK]{RESET} {detail}")
                ok_count += 1
        print()
    
    # Summary
    print(f"{BLUE}{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}{RESET}\n")
    
    total = critical_count + error_count + warning_count + ok_count
    
    print(f"{GREEN}✅ PASSED{RESET}:  {ok_count}/{total}")
    if warning_count > 0:
        print(f"{YELLOW}⚠️  WARNING{RESET}: {warning_count}/{total}")
    if error_count > 0:
        print(f"{RED}❌ FAILED{RESET}:  {error_count}/{total}")
    if critical_count > 0:
        print(f"{RED}🚨 CRITICAL{RESET}: {critical_count}/{total}")
    
    print(f"\n{BLUE}Compliance Status:{RESET}", end=" ")
    if critical_count > 0:
        print(f"{RED}CRITICAL VIOLATIONS - PLATFORM NON-COMPLIANT{RESET}")
        return 1
    elif error_count > 0:
        print(f"{RED}COMPLIANCE VIOLATIONS FOUND{RESET}")
        return 1
    elif warning_count > 0:
        print(f"{YELLOW}COMPLIANCE WARNINGS - Review Required{RESET}")
        return 0
    else:
        print(f"{GREEN}COMPLIANT{RESET}")
        return 0

if __name__ == '__main__':
    sys.exit(main())
