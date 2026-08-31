"""
CLI for Idea-to-Implementation Pipeline.

Subcommands: create, list, search, audit, skill-create, skill-list
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

from .palace import IdeaPalace
from .models.artifact import Idea, Concept, ADR, ImplementationPlan, Status, ArtifactType
from .palace.drawer import Drawer
from .gates.gates import IdeaGate, ConceptGate, ADRGate, ImplementationGate, PipelineAudit


class IdeaPipelineCLI:
    """CLI for idea-pipeline operations."""

    def __init__(self, palace_root: Optional[Path] = None):
        self.palace = IdeaPalace(palace_root)

    def create_artifact(self, artifact_type: str, name: str, room: str, wing: str, upstream: Optional[str] = None) -> str:
        """Create a new artifact (Idea, Concept, ADR, Plan)."""
        wing_obj = self.palace.create_wing(wing)
        room_obj = wing_obj.create_room(room)

        # Generate ID
        artifact_list = room_obj.drawer_manager.list_all()
        # Map artifact type to key in artifact_list
        key_map = {
            'idea': 'ideas',
            'concept': 'concepts',
            'adr': 'adrs',
            'implementation-plan': 'plans',
        }
        key = key_map.get(artifact_type, artifact_type + 's')
        count = len(artifact_list.get(key, []))
        artifact_id = f"{self._type_to_prefix(artifact_type)}-{count + 1:04d}"

        # Create artifact model
        if artifact_type == 'idea':
            artifact = Idea(
                id=artifact_id,
                name=name,
                room=room,
                wing=wing,
                status=Status.DRAFT,
                created_at=datetime.now(),
                tags=[],
            )
        elif artifact_type == 'concept':
            artifact = Concept(
                id=artifact_id,
                name=name,
                room=room,
                wing=wing,
                status=Status.DRAFT,
                created_at=datetime.now(),
                upstream=upstream,
                tags=[],
            )
        elif artifact_type == 'adr':
            artifact = ADR(
                id=artifact_id,
                name=name,
                room=room,
                wing=wing,
                status=Status.PROPOSED,
                created_at=datetime.now(),
                upstream=upstream,
                tags=[],
            )
        elif artifact_type == 'implementation-plan':
            artifact = ImplementationPlan(
                id=artifact_id,
                name=name,
                room=room,
                wing=wing,
                status=Status.APPROVED,
                created_at=datetime.now(),
                upstream=upstream,
                deployment_steps=[],
                success_criteria='',
            )
        else:
            raise ValueError(f"Unknown artifact type: {artifact_type}")

        # Save
        drawer = Drawer(artifact_type, artifact.id, f"# {name}\n\n(Created via CLI)", artifact.to_dict())
        room_obj.drawer_manager.save(drawer)

        return artifact_id

    def list_artifacts(self, artifact_type: Optional[str] = None, wing: Optional[str] = None, room: Optional[str] = None) -> None:
        """List artifacts."""
        if wing:
            wing_obj = self.palace.get_wing(wing)
            if not wing_obj:
                print(f"Wing '{wing}' not found")
                return

            if room:
                room_obj = wing_obj.get_room(room)
                if not room_obj:
                    print(f"Room '{room}' not found")
                    return

                all_artifacts = room_obj.drawer_manager.list_all()
                self._print_artifacts(all_artifacts, artifact_type)
            else:
                for room_name, room_obj in wing_obj.rooms.items():
                    all_artifacts = room_obj.drawer_manager.list_all()
                    if any(all_artifacts.values()):
                        print(f"\n{wing}/{room_name}:")
                        self._print_artifacts(all_artifacts, artifact_type)
        else:
            for wing_name, wing_obj in self.palace.wings.items():
                for room_name, room_obj in wing_obj.rooms.items():
                    all_artifacts = room_obj.drawer_manager.list_all()
                    if any(all_artifacts.values()):
                        print(f"\n{wing_name}/{room_name}:")
                        self._print_artifacts(all_artifacts, artifact_type)

    def _print_artifacts(self, all_artifacts: dict, artifact_type: Optional[str] = None) -> None:
        """Print formatted artifact list."""
        for key, artifacts in all_artifacts.items():
            if artifact_type and key != artifact_type + 's':
                continue
            if artifacts:
                print(f"  {key}:")
                for artifact in artifacts:
                    upstream_str = f" ← {artifact.upstream}" if artifact.upstream else ""
                    print(f"    {artifact.id}: {artifact.name}{upstream_str}")

    def search(self, query: str) -> None:
        """Search across all artifacts."""
        results = self.palace.search_all(query)
        if not results:
            print(f"No results for '{query}'")
            return

        print(f"Search results for '{query}':")
        for wing_name, wing_results in results.items():
            print(f"\n{wing_name}:")
            for room_name, artifacts in wing_results.items():
                print(f"  {room_name}:")
                for artifact in artifacts:
                    print(f"    {artifact['id']}: {artifact['name']}")

    def audit(self, wing: Optional[str] = None) -> None:
        """Run pipeline audit."""
        if wing:
            wing_obj = self.palace.get_wing(wing)
            if not wing_obj:
                print(f"Wing '{wing}' not found")
                return

            for room_name, room_obj in wing_obj.rooms.items():
                audit = PipelineAudit()
                results = audit.audit_all(room_obj.drawer_manager)
                self._print_audit_results(results)
        else:
            for wing_name, wing_obj in self.palace.wings.items():
                print(f"\nWing: {wing_name}")
                for room_name, room_obj in wing_obj.rooms.items():
                    audit = PipelineAudit()
                    results = audit.audit_all(room_obj.drawer_manager)
                    if results:
                        print(f"  Room: {room_name}")
                        self._print_audit_results(results, indent=4)

    def _print_audit_results(self, results, indent: int = 2) -> None:
        """Print audit results."""
        for result in results:
            if result.verdict.value != 'pass':
                prefix = ' ' * indent
                print(f"{prefix}{result.artifact_id} [{result.verdict.value}]: {result.issues or result.warnings}")

    def _type_to_prefix(self, artifact_type: str) -> str:
        """Convert type to ID prefix."""
        mapping = {
            'idea': 'IDEA',
            'concept': 'CONCEPT',
            'adr': 'ADR',
            'implementation-plan': 'IMPL',
        }
        return mapping.get(artifact_type, artifact_type.upper())


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description='Idea-to-Implementation Pipeline CLI')
    subparsers = parser.add_subparsers(dest='command', help='Command')

    # Create
    create_parser = subparsers.add_parser('create', help='Create artifact')
    create_parser.add_argument('type', choices=['idea', 'concept', 'adr', 'implementation-plan'])
    create_parser.add_argument('name', help='Artifact name')
    create_parser.add_argument('--room', default='general', help='Room/topic')
    create_parser.add_argument('--wing', default='default', help='Wing/project')
    create_parser.add_argument('--upstream', help='Upstream artifact ID')

    # List
    list_parser = subparsers.add_parser('list', help='List artifacts')
    list_parser.add_argument('--type', help='Filter by artifact type')
    list_parser.add_argument('--wing', help='Filter by wing')
    list_parser.add_argument('--room', help='Filter by room')

    # Search
    search_parser = subparsers.add_parser('search', help='Search artifacts')
    search_parser.add_argument('query', help='Search query')

    # Audit
    audit_parser = subparsers.add_parser('audit', help='Audit pipeline')
    audit_parser.add_argument('--wing', help='Audit specific wing')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    cli = IdeaPipelineCLI()

    if args.command == 'create':
        artifact_id = cli.create_artifact(args.type, args.name, args.room, args.wing, args.upstream)
        print(f"Created: {artifact_id}")

    elif args.command == 'list':
        cli.list_artifacts(args.type, args.wing, args.room)

    elif args.command == 'search':
        cli.search(args.query)

    elif args.command == 'audit':
        cli.audit(args.wing)


if __name__ == '__main__':
    main()
