"""
ADR-0400: Backward Compatibility Converter

Converts legacy CheckpointState to TaskGraph for seamless migration.
Handles old checkpoint formats and fills in missing graph structure.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
import logging
from .task_graph import TaskGraph, Node, Edge
from .graph_builder import GraphBuilder
from .checkpoint_manager import CheckpointState

logger = logging.getLogger(__name__)


class CheckpointToGraphConverter:
    """
    Convert CheckpointState (old format) to TaskGraph (new format).

    Backward compatibility layer ensures old checkpoints work seamlessly.
    """

    @staticmethod
    def convert(checkpoint: CheckpointState) -> TaskGraph:
        """
        Convert checkpoint to task graph.

        Args:
            checkpoint: CheckpointState from old system

        Returns:
            TaskGraph with inferred structure
        """
        builder = GraphBuilder(
            task_id=checkpoint.task_id,
            created_at=checkpoint.timestamp_iso
        )

        # Create checkpoint node
        checkpoint_node = Node(
            id=checkpoint.checkpoint_id,
            type="checkpoint",
            timestamp=checkpoint.timestamp_iso,
            data={
                "checkpoint_id": checkpoint.checkpoint_id,
                "iteration_num": checkpoint.iteration_num,
                "trigger": checkpoint.trigger,
                "phase": checkpoint.phase,
                "session_id": checkpoint.session_id
            }
        )
        builder.add_node(checkpoint_node)

        # Create context node from context_essentials
        if checkpoint.context_essentials:
            context_id = f"context_{checkpoint.checkpoint_id}"
            context_node = Node(
                id=context_id,
                type="context",
                timestamp=checkpoint.timestamp_iso,
                data={
                    "reduction_pct": checkpoint.context_essentials.get("reduction_pct", 91),
                    "kept_count": len(checkpoint.context_essentials.get("kept", [])),
                    "dropped_count": len(checkpoint.context_essentials.get("dropped", []))
                }
            )
            builder.add_node(context_node)

        # Create decision nodes from learning_state
        if checkpoint.learning_state:
            strategies = checkpoint.learning_state.get("strategies_tried", [])
            for idx, strategy in enumerate(strategies):
                decision_id = f"decision_{checkpoint.checkpoint_id}_{idx}"
                decision_node = Node(
                    id=decision_id,
                    type="decision",
                    timestamp=checkpoint.timestamp_iso,
                    data={
                        "strategy": strategy,
                        "phase": checkpoint.phase,
                        "iteration": checkpoint.iteration_num
                    }
                )
                builder.add_node(decision_node)

        # Create error node if recovery_reason provided
        if checkpoint.recovery_reason:
            error_id = f"error_{checkpoint.checkpoint_id}"
            error_node = Node(
                id=error_id,
                type="error",
                timestamp=checkpoint.timestamp_iso,
                data={
                    "error_type": "recovery_triggered",
                    "error_message": checkpoint.recovery_reason
                }
            )
            builder.add_node(error_node)

        # Create subgoal nodes
        for idx, subgoal in enumerate(checkpoint.open_subgoals or []):
            subgoal_id = f"subgoal_{checkpoint.checkpoint_id}_{idx}"
            subgoal_node = Node(
                id=subgoal_id,
                type="subgoal",
                timestamp=checkpoint.timestamp_iso,
                data={
                    "description": subgoal.get("description", ""),
                    "status": subgoal.get("status", "open"),
                    "work_done": subgoal.get("work_done", "")
                }
            )
            builder.add_node(subgoal_node)

        # Infer edges and build graph
        builder.infer_edges()
        graph = builder.build()

        logger.info(
            f"Converted checkpoint {checkpoint.checkpoint_id} to TaskGraph: "
            f"{len(graph.nodes)} nodes, {len(graph.edges)} edges"
        )
        return graph

    @staticmethod
    def merge_graphs(
        existing_graph: TaskGraph,
        checkpoint: CheckpointState
    ) -> TaskGraph:
        """
        Merge new checkpoint into existing graph.

        Used when resuming and adding new nodes to existing graph.

        Args:
            existing_graph: Graph from previous checkpoint
            checkpoint: New checkpoint to merge in

        Returns:
            Updated TaskGraph
        """
        # Create builder from existing graph
        builder = GraphBuilder(
            task_id=checkpoint.task_id,
            created_at=existing_graph.created_at
        )

        # Re-add all existing nodes
        for node in existing_graph.nodes.values():
            builder.add_node(node)

        # Re-add all existing edges
        for edge in existing_graph.edges:
            builder.add_edge(edge)

        # Convert new checkpoint and add its nodes
        new_graph = CheckpointToGraphConverter.convert(checkpoint)
        for node in new_graph.nodes.values():
            if node.id not in builder.nodes:  # Avoid duplicates
                builder.add_node(node)

        # Infer edges for combined graph
        builder.infer_edges()
        graph = builder.build()

        logger.info(
            f"Merged checkpoint {checkpoint.checkpoint_id} into graph: "
            f"{len(graph.nodes)} total nodes"
        )
        return graph

    @staticmethod
    def validate_conversion(checkpoint: CheckpointState, graph: TaskGraph) -> bool:
        """
        Validate that conversion was successful.

        Args:
            checkpoint: Original checkpoint
            graph: Converted graph

        Returns:
            True if conversion valid, False otherwise
        """
        # Check essential nodes exist
        if graph.task_id != checkpoint.task_id:
            logger.error("Task ID mismatch after conversion")
            return False

        if len(graph.nodes) == 0:
            logger.error("No nodes in converted graph")
            return False

        # Check checkpoint node exists
        checkpoint_nodes = graph.get_nodes_by_type("checkpoint")
        if not checkpoint_nodes:
            logger.error("No checkpoint node in converted graph")
            return False

        # Check DAG property
        if not graph.validate_dag():
            logger.error("Converted graph is not a DAG")
            return False

        logger.debug("Graph conversion validated successfully")
        return True

    @staticmethod
    def get_migration_stats(
        old_checkpoints: List[CheckpointState],
        new_graphs: List[TaskGraph]
    ) -> Dict[str, Any]:
        """
        Get statistics on migration from checkpoints to graphs.

        Args:
            old_checkpoints: List of old CheckpointState objects
            new_graphs: List of converted TaskGraph objects

        Returns:
            Migration statistics
        """
        total_old_nodes = 0
        total_new_nodes = 0
        total_new_edges = 0
        conversion_failures = 0

        for checkpoint in old_checkpoints:
            try:
                graph = CheckpointToGraphConverter.convert(checkpoint)
                total_old_nodes += 1  # One checkpoint per old state
                total_new_nodes += len(graph.nodes)
                total_new_edges += len(graph.edges)
            except Exception as e:
                conversion_failures += 1
                logger.error(f"Conversion failed for {checkpoint.checkpoint_id}: {e}")

        return {
            "total_checkpoints": len(old_checkpoints),
            "total_graphs": len(new_graphs),
            "conversion_failures": conversion_failures,
            "avg_nodes_per_graph": total_new_nodes / len(new_graphs) if new_graphs else 0,
            "avg_edges_per_graph": total_new_edges / len(new_graphs) if new_graphs else 0,
            "total_nodes_created": total_new_nodes,
            "total_edges_created": total_new_edges
        }
